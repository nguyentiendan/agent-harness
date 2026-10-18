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


class FileRequest(Scope):
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
def ensure(rid: UUID, body: Scope):
    return safe(runtime.ensure, str(rid), str(body.owner_id), str(body.scope_id))


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
    )


@app.post("/scopes/purge")
def purge(body: Scope):
    return safe(runtime.purge, str(body.owner_id), str(body.scope_id))


@app.get("/health")
def health():
    return {"ready": True}
