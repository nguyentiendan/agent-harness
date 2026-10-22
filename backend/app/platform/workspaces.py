"""Project-owned storage selection; preflight never changes the active binding."""

from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field
from .auth import Input, context, COOKIE
from .identity import authenticate, owned_project, audit
from .db import ExecutionScope
from . import chat, projects
from app.execution.bindings import validate, snapshot
from app.execution.lifecycle import check_idle, transfers
from app.execution.client import RunnerClient, RunnerUnavailable
from app.execution.operations import run_sync

router = APIRouter()


class WorkspaceInput(Input):
    mode: Literal["managed", "local"]
    local_path: str | None = Field(default=None, max_length=4096)
    expected_revision: int = Field(ge=0)


@router.put("/api/projects/{pid}/workspace")
async def configure(
    pid: str, body: WorkspaceInput, request: Request, ctx=Depends(context)
):
    try:
        desired = validate(
            dict(
                mode=body.mode,
                local_path=body.local_path,
                revision=body.expected_revision + 1,
            )
        )
    except ValueError as e:
        raise HTTPException(422, str(e))
    async with chat.admission:
        db, _, _ = ctx
        db.expire_all()
        user, _ = authenticate(db, request.cookies.get(COOKIE))
        if user.must_change_password:
            raise HTTPException(403, "Change your temporary password first.")
        p = owned_project(db, user, pid, "continue")
        check_idle(db, pid)
        scope = db.get(ExecutionScope, pid)
        if scope.workspace_revision != body.expected_revision:
            raise HTTPException(409, "Workspace changed. Refresh and retry.")
        old = snapshot(scope)
        if (old["mode"], old["local_path"]) == (desired["mode"], desired["local_path"]):
            return projects.dto(db, p)
        transfers.add(pid)
        try:
            await run_sync(RunnerClient().validate_workspace, desired)
            db.expire_all()
            actor, _ = authenticate(db, request.cookies.get(COOKIE))
            if actor.must_change_password:
                raise HTTPException(403, "Change your temporary password first.")
            owned_project(db, actor, pid, "continue")
            scope.workspace_mode = desired["mode"]
            scope.local_path = desired["local_path"]
            scope.workspace_revision = desired["revision"]
            if desired["mode"] == "local":
                scope.has_files = True
            audit(
                db,
                actor,
                "workspace.connect"
                if desired["mode"] == "local"
                else "workspace.disconnect",
                pid,
            )
            result = projects.dto(db, p)
            db.commit()
            return result
        except RunnerUnavailable:
            raise HTTPException(
                503,
                "Cannot access the folder. Check that it exists on the Docker host, is shared with Docker Desktop, and is readable/writable by sandbox user 10001:10001.",
            )
        finally:
            transfers.discard(pid)
