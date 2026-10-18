"""Owned text-chat execution. Tools are deliberately absent until sandbox milestone."""

import asyncio
import json
import time
import uuid
from sqlalchemy import select, func
from fastapi import HTTPException
from app.config import load_config
from app.integrations.llm import LiteLLMClientWrapper
from app.security.redaction import redact_text
from .db import DB, Chat, Event, Run, User
from .identity import digest

running: dict[str, asyncio.Task] = {}
admission = asyncio.Lock()


def allowed_models():
    config = load_config().get("llm", {})
    return list(
        dict.fromkeys(
            [
                config.get("primary_model", "gpt-4o-mini"),
                config.get("reviewer_model", "gpt-4o-mini"),
            ]
        )
    )


def events_for(db, sid):
    return [
        e.payload
        for e in db.scalars(
            select(Event).where(Event.session_id == sid).order_by(Event.sequence)
        )
    ]


def append_event(db, sid, rid, payload):
    seq = db.scalar(
        select(func.count()).select_from(Event).where(Event.session_id == sid)
    )
    db.add(
        Event(
            session_id=sid,
            run_id=rid,
            sequence=seq,
            payload=json.loads(redact_text(json.dumps(payload))),
        )
    )
    db.flush()


def chat_dto(db, c):
    owner = db.get(User, c.owner_id)
    return {
        "session_id": c.id,
        "owner_id": c.owner_id,
        "owner_username": owner.username,
        "goal": c.title,
        "status": c.status,
        "created_at": c.created_at,
        "updated_at": c.updated_at,
        "workspace": "",
    }


def finish(rid, status, reason=""):
    with DB.begin() as db:
        r = db.get(Run, rid)
        if not r or r.status != "running":
            return
        r.status = status
        r.finished_at = time.time()
        c = db.get(Chat, r.session_id)
        c.status = status
        c.updated_at = time.time()
        append_event(
            db, c.id, rid, {"type": "status", "status": status, "reason": reason}
        )


async def execute(rid):
    try:
        with DB() as db:
            r = db.get(Run, rid)
            sid = r.session_id
            model = r.model
            history = [
                {"role": e["type"], "content": e["text"]}
                for e in events_for(db, sid)
                if e["type"] in ("user", "assistant")
            ]
        llm = LiteLLMClientWrapper(default_model=model)
        from app.execution import client, agent, lifecycle

        if client.enabled():

            def emit(event):
                with DB.begin() as db:
                    append_event(db, sid, rid, event)

            await asyncio.wait_for(
                agent.run(llm, rid, history, model, emit), timeout=900
            )
        else:
            response = await asyncio.wait_for(
                llm.complete(
                    [
                        {
                            "role": "system",
                            "content": "You are a helpful text-only assistant. You cannot execute tools or access files.",
                        },
                        *history,
                    ],
                    model=model,
                ),
                timeout=900,
            )
            with DB.begin() as db:
                append_event(
                    db,
                    sid,
                    rid,
                    {
                        "type": "assistant",
                        "text": response.choices[0].message.content
                        or "No text response was returned.",
                    },
                )
        status, reason = "completed", ""
    except asyncio.CancelledError:
        status, reason = "stopped", ""
    except Exception as error:
        from app.execution.client import RunnerUnavailable

        status = "failed"
        reason = (
            str(error)
            if isinstance(error, RunnerUnavailable)
            else "The model or sandbox run failed. Check service availability and execution limits."
        )
    finally:
        from app.execution.lifecycle import settle

        await asyncio.shield(
            asyncio.to_thread(
                settle,
                rid,
                locals().get("status", "failed"),
                locals().get("reason", ""),
            )
        )
        running.pop(rid, None)


async def start(db, c, body):
    # Single-worker admission plus persisted run state; future multi-worker requires DB leases.
    fingerprint = digest(
        json.dumps(body.model_dump(exclude={"request_id"}), sort_keys=True)
    )
    existing = db.scalar(
        select(Run).where(Run.session_id == c.id, Run.request_id == body.request_id)
    )
    if existing:
        if existing.body_hash != fingerprint:
            raise HTTPException(409, "Request ID already used for different content.")
        return {
            "session_id": c.id,
            "event_offset": existing.event_offset,
            "run_id": existing.id,
        }
    active = list(db.scalars(select(Run).where(Run.status == "running")))
    if c.status == "running" or any(
        db.get(Chat, r.session_id).owner_id == c.owner_id for r in active
    ):
        raise HTTPException(
            409, "You already have a running chat. Stop it or wait for completion."
        )
    if len(active) >= 4:
        raise HTTPException(429, "The server is busy. Try again shortly.")
    model = body.primary_model or allowed_models()[0]
    if model not in allowed_models():
        raise HTTPException(422, "Model is not enabled on this server.")
    if body.reviewer_model and body.reviewer_model not in allowed_models():
        raise HTTPException(422, "Reviewer model is not enabled.")
    offset = len(events_for(db, c.id))
    rid = str(uuid.uuid4())
    r = Run(
        id=rid,
        session_id=c.id,
        request_id=body.request_id,
        body_hash=fingerprint,
        model=model,
        event_offset=offset,
    )
    db.add(r)
    db.flush()
    from app.execution.lifecycle import acquire

    acquire(db, r)
    if offset == 0:
        c.title = body.goal[:200]
    c.status = "running"
    c.updated_at = time.time()
    append_event(db, c.id, rid, {"type": "user", "text": body.goal})
    db.commit()  # durable message before task dispatch
    running[rid] = asyncio.create_task(execute(rid))
    return {"session_id": c.id, "event_offset": offset, "run_id": rid}


async def stop_run(rid):
    task = running.get(rid)
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    from app.execution.lifecycle import settle

    await asyncio.to_thread(
        settle, rid, "stopped"
    )  # also cancel before coroutine entry
    running.pop(rid, None)


async def stop_user_runs(uid):
    with DB() as db:
        ids = list(
            db.scalars(
                select(Run.id)
                .join(Chat, Chat.id == Run.session_id)
                .where(Chat.owner_id == uid, Run.status == "running")
            )
        )
    for rid in ids:
        await stop_run(rid)


def reconcile():
    with DB() as db:
        ids = list(db.scalars(select(Run.id).where(Run.status == "running")))
    for rid in ids:
        from app.execution.lifecycle import settle

        settle(rid, "failed", "Server restarted. Send a new message to continue.")
