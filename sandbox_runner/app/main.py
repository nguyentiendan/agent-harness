import asyncio
from contextlib import asynccontextmanager
import os
import secrets
from typing import Literal
from uuid import UUID
from fastapi import FastAPI, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from .runtime import Runtime, RunnerError

runtime = None
TOKEN = os.environ.get("RUNNER_TOKEN", "")
if len(TOKEN) < 32:
    raise RuntimeError("RUNNER_TOKEN must contain at least 32 characters.")


def authorized(authorization: str = Header(default="")):
    if not secrets.compare_digest(authorization, "Bearer " + TOKEN):
        raise HTTPException(401, "Unauthorized")


class Scope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    owner_id: UUID
    scope_id: UUID


class VersionedScope(Scope):
    workspace_revision: int = Field(default=0, ge=0)


class WorkspaceBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["managed", "local"]
    local_path: str | None = Field(default=None, max_length=4096)
    revision: int = Field(ge=0)


class Configure(Scope):
    binding: WorkspaceBinding


class Tool(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["bash", "read_file", "write_file", "edit_file", "list_dir"]
    command: str = Field(default="", max_length=16000)
    timeout: int = Field(default=120, ge=1, le=120)
    path: str = Field(default="", max_length=1024)
    content: str = Field(default="", max_length=60000)
    old: str = Field(default="", max_length=60000)
    new: str = Field(default="", max_length=60000)

    def payload(self):
        if self.op == "bash":
            return {"op": self.op, "command": self.command, "timeout": self.timeout}
        return {
            "op": self.op,
            **{key: getattr(self, key) for key in ("path", "content", "old", "new")},
        }


class FileRequest(VersionedScope):
    op: Literal["read_file", "list_dir"]
    path: str = Field(default="", max_length=1024)


async def sweep_loop():
    while True:
        await asyncio.sleep(5)
        try:
            await asyncio.to_thread(runtime.sweep)
        except Exception:
            pass  # retried; leases in backend prevent unsafe reuse


@asynccontextmanager
async def lifespan(app):
    global runtime
    runtime = Runtime()
    await asyncio.to_thread(runtime.sweep, True)
    task = asyncio.create_task(sweep_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await asyncio.to_thread(runtime.sweep, True)


app = FastAPI(
    lifespan=lifespan,
    dependencies=[Depends(authorized)],
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


def safe(call, *args):
    try:
        return call(*args)
    except (RunnerError, ValueError) as e:
        raise HTTPException(409, str(e))
    except Exception:
        raise HTTPException(503, "Sandbox runtime unavailable.")


@app.post("/runs/{rid}/ensure")
def ensure(rid: UUID, body: VersionedScope):
    return safe(
        runtime.ensure,
        str(rid),
        str(body.owner_id),
        str(body.scope_id),
        body.workspace_revision,
    )


@app.post("/runs/{rid}/exec")
def execute(rid: UUID, body: Tool):
    return safe(runtime.execute, str(rid), body.payload())


@app.delete("/runs/{rid}")
def destroy(rid: UUID):
    return safe(runtime.destroy, str(rid))


@app.post("/files")
def files(body: FileRequest):
    return safe(
        runtime.files,
        str(body.owner_id),
        str(body.scope_id),
        {"op": body.op, "path": body.path},
        body.workspace_revision,
    )


@app.post("/workspaces/validate")
def validate_workspace(body: WorkspaceBinding):
    return safe(runtime.validate_workspace, body.model_dump())


@app.post("/scopes/configure")
def configure(body: Configure):
    return safe(
        runtime.configure,
        str(body.owner_id),
        str(body.scope_id),
        body.binding.model_dump(),
    )


@app.post("/scopes/purge")
def purge(body: Scope):
    return safe(runtime.purge, str(body.owner_id), str(body.scope_id))


@app.get("/health")
def health():
    return {"ready": True}


# Reserve before reading request bodies: at most two bounded disk spools.
import tempfile
from fastapi import Request
from fastapi.responses import StreamingResponse
from . import transfers

transfer_slots = __import__("threading").BoundedSemaphore(2)


async def run_sync(call, *args):
    # A cancelled HTTP request must not close a spool still used by a worker thread.
    task = asyncio.create_task(asyncio.to_thread(call, *args))
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            if cancelled:
                raise asyncio.CancelledError()
            raise
    if cancelled:
        raise asyncio.CancelledError()
    return result


@app.post("/transfer/upload")
async def upload(
    request: Request,
    owner_id: UUID,
    scope_id: UUID,
    path: str,
    workspace_revision: int = 0,
):
    if not transfer_slots.acquire(blocking=False):
        raise HTTPException(503, "File gateway busy.")
    try:
        with tempfile.TemporaryFile() as source:
            count = 0
            async with asyncio.timeout(120):
                async for chunk in request.stream():
                    count += len(chunk)
                    if count > transfers.LIMIT:
                        raise HTTPException(413, "File exceeds 25 MiB.")
                    source.write(chunk)
                return await run_sync(
                    safe,
                    transfers.upload,
                    runtime,
                    str(owner_id),
                    str(scope_id),
                    path,
                    source,
                    workspace_revision,
                )
    finally:
        transfer_slots.release()


@app.get("/transfer/download")
async def download(
    owner_id: UUID, scope_id: UUID, path: str, workspace_revision: int = 0
):
    if not transfer_slots.acquire(blocking=False):
        raise HTTPException(503, "File gateway busy.")
    output = tempfile.TemporaryFile()
    try:
        result = await run_sync(
            safe,
            transfers.download,
            runtime,
            str(owner_id),
            str(scope_id),
            path,
            output,
            workspace_revision,
        )
    except BaseException:
        output.close()
        transfer_slots.release()
        raise

    async def stream():
        try:
            while chunk := output.read(65536):
                yield chunk
        finally:
            output.close()
            transfer_slots.release()

    return StreamingResponse(
        stream(),
        media_type="application/octet-stream",
        headers={"Content-Length": str(result["bytes"])},
    )


@app.delete("/transfer/file")
def delete_file(owner_id: UUID, scope_id: UUID, path: str, workspace_revision: int = 0):
    return safe(
        transfers.delete,
        runtime,
        str(owner_id),
        str(scope_id),
        path,
        workspace_revision,
    )
