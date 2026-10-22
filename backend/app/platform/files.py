"""Authenticated file gateway. Binary bodies are bounded and never loaded whole."""

import asyncio
import os
import tempfile
from urllib.parse import quote
import httpx
from fastapi import APIRouter, Depends, Request, HTTPException, Query
from fastapi.responses import StreamingResponse
from .auth import context, COOKIE
from .identity import owned_chat, owned_project, authenticate
from .db import DB, ExecutionScope
from . import chat
from app.execution.bindings import snapshot, sync
from app.execution.operations import run_sync, finish_on_cancel
from app.execution.lifecycle import scope_for_chat, check_idle, transfers
from app.execution.client import RunnerClient, RunnerUnavailable

router = APIRouter()
LIMIT = 25 * 1024 * 1024


def target(request, ctx, action="read"):
    db, user, _ = ctx
    pid = request.path_params.get("pid")
    if pid:
        p = owned_project(db, user, pid, action)
        scope = db.get(ExecutionScope, pid)
        if not scope:
            scope = ExecutionScope(id=pid, owner_id=p.owner_id, project_id=pid)
            db.add(scope)
            db.flush()
        return scope
    c = owned_chat(db, user, request.path_params["sid"], action)
    if not c.project_id:
        scope = db.get(ExecutionScope, c.id)
        if action not in ("list", "download") or not scope or not scope.has_files:
            raise HTTPException(403, "Create or open a Project to work with files.")
        return scope
    return scope_for_chat(db, c)


def connection():
    token = os.environ.get("RUNNER_TOKEN", "")
    if len(token) < 32:
        raise HTTPException(503, "File gateway is not configured.")
    return httpx.AsyncClient(
        base_url=os.environ.get("RUNNER_URL", "http://sandbox-runner:8080"),
        headers={"Authorization": "Bearer " + token},
        timeout=120,
        trust_env=False,
    )


def response_ok(r):
    if not r.is_success:
        status = r.status_code if r.status_code in (409, 413, 422) else 503
        raise HTTPException(
            status,
            "File operation failed. Check the path, size limit, or active run and retry.",
        )


@router.get("/api/sessions/{sid}/files")
@router.get("/api/projects/{pid}/files")
async def list_project(request: Request, ctx=Depends(context)):
    return await read_operation(request, ctx, "list", "list_dir", "")


@router.get("/api/sessions/{sid}/read")
@router.get("/api/projects/{pid}/read")
async def read_project(request: Request, path: str, ctx=Depends(context)):
    return await read_operation(request, ctx, "read", "read_file", path)


async def read_operation(request, ctx, action, op, path):
    scope = await reserve(request, ctx, action)
    try:
        await sync_scope(scope)
        result = await run_sync(
            RunnerClient().files,
            scope.owner_id,
            scope.id,
            op,
            path,
            scope.workspace_revision,
        )
        if "error" in result:
            raise HTTPException(409, result["error"])
        return result
    except RunnerUnavailable:
        raise HTTPException(
            503,
            "File gateway unavailable. Check the active workspace folder and retry.",
        )
    finally:
        transfers.discard(scope.id)


async def sync_scope(scope):
    try:
        await run_sync(sync, scope.owner_id, scope.id, snapshot(scope))
    except RunnerUnavailable:
        raise HTTPException(
            503, "Workspace unavailable. Refresh and check the connected folder."
        )


async def reserve(request, ctx, action):
    async with chat.admission:
        db, _, _ = ctx
        db.expire_all()
        user, session = authenticate(db, request.cookies.get(COOKIE))
        if user.must_change_password:
            raise HTTPException(403, "Change your password first.")
        scope = target(request, (db, user, session), action)
        check_idle(db, scope.id)
        if len(transfers) >= 2:
            raise HTTPException(429, "File transfers are busy. Retry shortly.")
        if action == "upload":
            scope.has_files = True  # persist before any uncertain external write
        db.commit()
        transfers.add(scope.id)
        return scope


@router.post("/api/sessions/{sid}/files/upload")
@router.post("/api/projects/{pid}/files/upload")
async def upload(
    request: Request,
    path: str = Query(min_length=1, max_length=1024),
    ctx=Depends(context),
):
    scope = await reserve(request, ctx, "upload")
    try:
        with tempfile.TemporaryFile() as source:
            count = 0
            async with asyncio.timeout(120):
                async for chunk in request.stream():
                    count += len(chunk)
                    if count > LIMIT:
                        raise HTTPException(413, "File exceeds 25 MiB.")
                    source.write(chunk)
                # An account revoked while uploading cannot dispatch a file mutation.
                with DB.begin() as fresh:
                    actor, _ = authenticate(fresh, request.cookies.get(COOKIE))
                    if actor.must_change_password:
                        raise HTTPException(
                            403, "Change your temporary password first."
                        )
                await sync_scope(scope)
                source.seek(0)

                async def body():
                    while chunk := source.read(65536):
                        yield chunk

                async with connection() as client:
                    r = await finish_on_cancel(
                        client.post(
                            "/transfer/upload",
                            params={
                                "owner_id": scope.owner_id,
                                "scope_id": scope.id,
                                "workspace_revision": scope.workspace_revision,
                                "path": path,
                            },
                            content=body(),
                        )
                    )
                response_ok(r)
                return r.json()
    except (httpx.HTTPError, TimeoutError):
        raise HTTPException(503, "File gateway unavailable or timed out.")
    finally:
        transfers.discard(scope.id)


@router.get("/api/sessions/{sid}/files/download")
@router.get("/api/projects/{pid}/files/download")
async def download(
    request: Request,
    path: str = Query(min_length=1, max_length=1024),
    ctx=Depends(context),
):
    scope = await reserve(request, ctx, "download")
    client = None
    r = None
    try:
        await sync_scope(scope)
        client = connection()

        async def receive():
            nonlocal r
            r = await client.send(
                client.build_request(
                    "GET",
                    "/transfer/download",
                    params={
                        "owner_id": scope.owner_id,
                        "scope_id": scope.id,
                        "workspace_revision": scope.workspace_revision,
                        "path": path,
                    },
                ),
                stream=True,
            )

        await finish_on_cancel(receive())
        response_ok(r)
        length = int(r.headers.get("content-length", LIMIT + 1))
        if length > LIMIT:
            raise HTTPException(413, "File exceeds 25 MiB.")
    except BaseException:
        if r:
            await r.aclose()
        if client:
            await client.aclose()
        transfers.discard(scope.id)
        raise

    async def body():
        count = 0
        try:
            async for chunk in r.aiter_bytes(65536):
                count += len(chunk)
                if count > LIMIT:
                    raise RuntimeError("Download exceeds limit.")
                yield chunk
        finally:
            await r.aclose()
            await client.aclose()
            transfers.discard(scope.id)

    return StreamingResponse(
        body(),
        media_type="application/octet-stream",
        headers={
            "Content-Length": str(length),
            "Content-Disposition": "attachment; filename*=UTF-8''"
            + quote(path.rsplit("/", 1)[-1], safe=""),
            "Content-Security-Policy": "sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/api/sessions/{sid}/files")
@router.delete("/api/projects/{pid}/files")
async def delete_file(
    request: Request,
    path: str = Query(min_length=1, max_length=1024),
    ctx=Depends(context),
):
    scope = await reserve(request, ctx, "delete_file")
    try:
        await sync_scope(scope)
        async with connection() as client:
            r = await finish_on_cancel(
                client.delete(
                    "/transfer/file",
                    params={
                        "owner_id": scope.owner_id,
                        "scope_id": scope.id,
                        "workspace_revision": scope.workspace_revision,
                        "path": path,
                    },
                )
            )
        response_ok(r)
        return r.json()
    except httpx.HTTPError:
        raise HTTPException(503, "File gateway unavailable.")
    finally:
        transfers.discard(scope.id)
