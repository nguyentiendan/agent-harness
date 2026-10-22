"""Private project metadata; files are addressed only by immutable scope IDs."""

import time
import uuid
from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import Field
from sqlalchemy import select
from .auth import Input, context, COOKIE
from .db import Project, Chat, User, ExecutionScope
from .identity import owned_project, authenticate, audit
from . import chat
from app.execution.lifecycle import check_idle

router = APIRouter(prefix="/api/projects")


class ProjectInput(Input):
    name: str = Field(min_length=1, max_length=120)
    instructions: str = Field(default="", max_length=16000)


class ProjectUpdate(Input):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    instructions: str | None = Field(default=None, max_length=16000)


def dto(db, p):
    from app.execution.bindings import snapshot

    return dict(
        id=p.id,
        owner_id=p.owner_id,
        owner_username=db.get(User, p.owner_id).username,
        name=p.name,
        instructions=p.instructions,
        created_at=p.created_at,
        workspace=snapshot(db.get(ExecutionScope, p.id)),
    )


def clean_name(name):
    if not name.strip():
        raise HTTPException(422, "Project name cannot be blank.")
    return name.strip()


@router.get("")
def listing(all_users: bool = False, ctx=Depends(context)):
    db, user, _ = ctx
    q = (
        select(Project)
        .where(Project.deleted_at.is_(None))
        .order_by(Project.created_at.desc())
    )
    if all_users:
        if user.role != "admin":
            raise HTTPException(403, "Administrator access required.")
        audit(db, user, "project.list_all", "all")
    else:
        q = q.where(Project.owner_id == user.id)
    return {"projects": [dto(db, p) for p in db.scalars(q)]}


@router.post("")
def create(body: ProjectInput, ctx=Depends(context)):
    db, user, _ = ctx
    p = Project(
        id=str(uuid.uuid4()),
        owner_id=user.id,
        name=clean_name(body.name),
        instructions=body.instructions,
    )
    db.add(p)
    db.flush()
    db.add(ExecutionScope(id=p.id, owner_id=p.owner_id, project_id=p.id))
    db.flush()
    return dto(db, p)


@router.get("/{pid}")
def get(pid: str, ctx=Depends(context)):
    return dto(ctx[0], owned_project(ctx[0], ctx[1], pid))


@router.patch("/{pid}")
async def update(pid: str, body: ProjectUpdate, request: Request, ctx=Depends(context)):
    async with chat.admission:
        db, _, _ = ctx
        db.expire_all()
        user, _ = authenticate(db, request.cookies.get(COOKIE))
        p = owned_project(db, user, pid, "update")
        check_idle(db, pid)
        if body.name is not None:
            p.name = clean_name(body.name)
        if body.instructions is not None:
            p.instructions = body.instructions
        result = dto(db, p)
        db.commit()
        return result


@router.delete("/{pid}")
async def trash(pid: str, request: Request, ctx=Depends(context)):
    async with chat.admission:
        db, _, _ = ctx
        db.expire_all()
        user, _ = authenticate(db, request.cookies.get(COOKIE))
        p = owned_project(db, user, pid, "delete")
        check_idle(db, pid)
        children = list(db.scalars(select(Chat).where(Chat.project_id == pid)))
        if any(c.status == "running" for c in children):
            raise HTTPException(409, "Stop project runs first.")
        p.deleted_at = time.time()
        for c in children:
            c.deleted_at = p.deleted_at
        db.commit()
        return {"id": pid, "deleted": True}
