"""Public owned-chat routes. No legacy unauthenticated handlers are mounted."""

import asyncio
import json
import time
import uuid
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import StreamingResponse
from pydantic import Field
from sqlalchemy import select
from .auth import context, Input, COOKIE
from .db import DB, Chat, Run
from .identity import owned_chat, owned_project, authenticate
from . import chat
from app.execution import client as execution_client

router = APIRouter()


class NewChat(Input):
    project_id: str | None = Field(default=None, max_length=36)
    title: str = Field(default="New chat", min_length=1, max_length=200)


class Start(Input):
    session_id: str = Field(max_length=128)
    request_id: str = Field(min_length=1, max_length=100)
    goal: str = Field(min_length=1, max_length=32000)
    primary_model: str | None = Field(default=None, max_length=120)
    reviewer_model: str | None = Field(default=None, max_length=120)
    max_turns: int | None = Field(default=None, ge=1, le=100)


class Command(Input):
    session_id: str | None = None
    command: str = Field(max_length=80)
    args: str = Field(default="", max_length=2000)


@router.get("/api/config")
def config(ctx=Depends(context)):
    return {
        "models": chat.allowed_models(),
        "tools_enabled": execution_client.enabled(),
        "projects_enabled": True,
    }


@router.post("/api/sessions")
async def create(body: NewChat, request: Request, ctx=Depends(context)):
    async with chat.admission:
        db, _, _ = ctx
        db.expire_all()
        user, _ = authenticate(db, request.cookies.get(COOKIE))
        if user.must_change_password:
            raise HTTPException(403, "Change your temporary password first.")
        if body.project_id:
            owned_project(db, user, body.project_id, "continue")
        c = Chat(
            id=str(uuid.uuid4()),
            owner_id=user.id,
            title=body.title,
            project_id=body.project_id,
        )
        db.add(c)
        db.flush()
        from app.execution.lifecycle import scope_for_chat

        scope_for_chat(db, c)
        db.commit()
        return chat.chat_dto(db, c)


@router.get("/api/sessions")
def list_chats(all_users: bool = False, ctx=Depends(context)):
    db, user, _ = ctx
    if all_users and user.role != "admin":
        raise HTTPException(403, "Administrator access required.")
    q = select(Chat).where(Chat.deleted_at.is_(None)).order_by(Chat.updated_at.desc())
    if not all_users:
        q = q.where(Chat.owner_id == user.id)
    else:
        from .identity import audit

        audit(db, user, "chat.list_all", "all")
    return {"sessions": [chat.chat_dto(db, c) for c in db.scalars(q)]}


@router.get("/api/sessions/{sid}")
def get_chat(sid: str, ctx=Depends(context)):
    db, user, _ = ctx
    owned_chat(db, user, sid)
    return {"session_id": sid, "events": chat.events_for(db, sid)}


@router.delete("/api/sessions/{sid}")
async def delete_chat(sid: str, request: Request, ctx=Depends(context)):
    async with chat.admission:
        db, _, _ = ctx
        db.expire_all()
        user, _ = authenticate(db, request.cookies.get(COOKIE))
        c = owned_chat(db, user, sid, "delete")
        from app.execution.lifecycle import check_idle, scope_id

        check_idle(db, scope_id(c))
        if c.status == "running":
            raise HTTPException(409, "Stop the running chat before deleting it.")
        c.deleted_at = time.time()
        db.commit()
        return {"session_id": sid, "message": "Chat deleted"}


@router.post("/api/harness/start")
async def start(body: Start, request: Request, ctx=Depends(context)):
    async with chat.admission:
        db, _, _ = ctx
        db.expire_all()
        user, _ = authenticate(db, request.cookies.get(COOKIE))
        if user.must_change_password:
            raise HTTPException(403, "Change your temporary password first.")
        c = owned_chat(db, user, body.session_id, "continue")
        if not body.goal.strip():
            raise HTTPException(422, "Message cannot be blank.")
        return await chat.start(db, c, body)


@router.post("/api/harness/stop/{sid}")
async def stop(sid: str, ctx=Depends(context)):
    db, user, _ = ctx
    owned_chat(db, user, sid, "stop")
    ids = list(
        db.scalars(select(Run.id).where(Run.session_id == sid, Run.status == "running"))
    )
    db.commit()
    for rid in ids:
        await chat.stop_run(rid)
    return {"session_id": sid, "status": "stopped"}


@router.get("/api/harness/status/{sid}")
def status(sid: str, ctx=Depends(context)):
    db, user, _ = ctx
    c = owned_chat(db, user, sid)
    return {"session_id": sid, "status": c.status, "goal": c.title}


@router.get("/api/harness/stream/{sid}")
async def stream(
    sid: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    ctx=Depends(context),
):
    db, user, _ = ctx
    owned_chat(db, user, sid)
    db.commit()
    token = request.cookies.get(COOKIE)
    # Last-Event-ID takes precedence on EventSource reconnect.
    cursor = after
    last = request.headers.get("last-event-id")
    if last:
        try:
            cursor = max(cursor, int(last))
        except ValueError:
            raise HTTPException(400, "Invalid event cursor.")

    async def generate():
        nonlocal cursor
        while not await request.is_disconnected():
            with DB.begin() as local:
                try:
                    actor, _ = authenticate(local, token)
                    if actor.must_change_password:
                        return
                    c = owned_chat(local, actor, sid)
                except HTTPException:
                    return
                events = chat.events_for(local, sid)
                state = c.status
            for event in events[cursor:]:
                cursor += 1
                yield f"id: {cursor}\ndata: {json.dumps(event)}\n\n"
            if state != "running":
                return
            await asyncio.sleep(0.5)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.get("/api/harness/list/{sid}")
@router.get("/api/harness/files/{sid}")
async def files(sid: str, ctx=Depends(context)):
    c = owned_chat(ctx[0], ctx[1], sid)
    if not execution_client.enabled():
        return {"session_id": sid, "files": [], "tools_enabled": False}
    try:
        result = await asyncio.to_thread(
            execution_client.RunnerClient().files,
            c.owner_id,
            c.project_id or sid,
            "list_dir",
        )
        if "error" in result:
            raise HTTPException(409, result["error"])
        return {"session_id": sid, **result}
    except execution_client.RunnerUnavailable:
        raise HTTPException(503, "File gateway unavailable.")


@router.get("/api/harness/read/{sid}")
async def read_file(sid: str, path: str, ctx=Depends(context)):
    c = owned_chat(ctx[0], ctx[1], sid)
    if not execution_client.enabled():
        raise HTTPException(409, "File tools will be available after sandbox setup.")
    try:
        result = await asyncio.to_thread(
            execution_client.RunnerClient().files,
            c.owner_id,
            c.project_id or sid,
            "read_file",
            path,
        )
        if "error" in result:
            raise HTTPException(409, result["error"])
        return result
    except execution_client.RunnerUnavailable:
        raise HTTPException(503, "File gateway unavailable.")


@router.get("/api/harness/approvals/{sid}")
def approvals(sid: str, ctx=Depends(context)):
    owned_chat(ctx[0], ctx[1], sid)
    return {"approvals": []}


@router.post("/api/harness/approve")
def approve(ctx=Depends(context)):
    raise HTTPException(404, "Approval not found.")


@router.post("/api/command")
def command(body: Command, ctx=Depends(context)):
    if body.session_id:
        owned_chat(ctx[0], ctx[1], body.session_id)
    if body.command == "help":
        return {
            "output": "/new · /stop · /settings · /model · /status. Sandbox tools run offline; /workspace files persist within this chat."
        }
    if body.command == "status" and body.session_id:
        return {"output": owned_chat(ctx[0], ctx[1], body.session_id).status}
    raise HTTPException(409, "This command is not available in this version.")
