"""Durable lease: never release ownership until Docker confirms cleanup."""

import threading
from fastapi import HTTPException
from sqlalchemy import select
from app.platform.db import DB, ExecutionScope, RunLease, Run, Chat
from . import client

cleanup_lock = threading.RLock()


# Single-worker file transfers share admission with runs and deletion.
transfers = set()
pending_validations = {}


def scope_id(chat):
    return chat.project_id or chat.id


def scope_for_chat(db, chat):
    sid = scope_id(chat)
    scope = db.get(ExecutionScope, sid)
    if not scope:
        scope = ExecutionScope(
            id=sid,
            owner_id=chat.owner_id,
            project_id=chat.project_id,
            standalone_session_id=None if chat.project_id else chat.id,
        )
        db.add(scope)
        db.flush()
    return scope


def check_idle(db, sid):
    if sid in transfers or db.get(RunLease, sid):
        raise HTTPException(
            409,
            "This chat or project is busy. Wait for its run or file transfer to finish.",
        )


def acquire(db, run):
    chat = db.get(Chat, run.session_id)
    if not chat.project_id:
        return {"owner_id": chat.owner_id, "scope_id": chat.id, "run_id": run.id}
    scope = scope_for_chat(db, chat)
    from .bindings import snapshot

    if run.workspace_snapshot is None:
        run.workspace_snapshot = snapshot(scope)
    if scope.id in transfers:
        raise HTTPException(409, "A file transfer is in progress. Please retry.")
    lease = db.get(RunLease, scope.id)
    if lease and lease.run_id != run.id:
        raise HTTPException(409, "This project already has a running chat.")
    if not lease:
        db.add(RunLease(scope_id=scope.id, run_id=run.id))
        db.flush()
    return {
        "owner_id": scope.owner_id,
        "scope_id": scope.id,
        "run_id": run.id,
        "workspace_revision": run.workspace_snapshot["revision"],
    }


def prepare(rid):
    # Serializes lazy ensure with cancellation; a late worker thread cannot resurrect a stopped run.
    with cleanup_lock:
        with DB.begin() as db:
            run = db.get(Run, rid)
            if not run or run.status != "running":
                raise RuntimeError("Run is no longer active.")
            if not db.get(Chat, run.session_id).project_id:
                raise RuntimeError("Create or open a Project to work with files.")
            ctx = acquire(db, run)
            binding = dict(run.workspace_snapshot)
            lease = db.get(RunLease, ctx["scope_id"])
            if lease.terminal_status:
                raise RuntimeError("Run is stopping.")
            first = not lease.sandbox_started
            lease.sandbox_started = True
            db.get(ExecutionScope, ctx["scope_id"]).has_files = True
        if first:
            from .bindings import sync

            sync(ctx["owner_id"], ctx["scope_id"], binding)
            client.RunnerClient().ensure(ctx)
        return ctx


def settle(rid, status, reason=""):
    from app.platform.chat import finish

    with cleanup_lock:
        with DB.begin() as db:
            lease = db.scalar(select(RunLease).where(RunLease.run_id == rid))
            if not lease:
                finish(rid, status, reason)
                return True
            lease.terminal_status = status
            lease.terminal_reason = reason[:500]
            started = lease.sandbox_started
        if started:
            try:
                client.RunnerClient().destroy(rid)
            except Exception:
                return False
        with DB.begin() as db:
            lease = db.scalar(select(RunLease).where(RunLease.run_id == rid))
            if lease:
                db.delete(lease)
        finish(rid, status, reason)
        return True


def release_validation(owner, sid, operation):
    try:
        client.RunnerClient().release_validation(owner, sid, operation)
    except Exception:
        pending_validations[operation] = (owner, sid)
        return False
    pending_validations.pop(operation, None)
    transfers.discard(sid)
    return True


def retry_pending():
    for operation, (owner, sid) in list(pending_validations.items()):
        release_validation(owner, sid, operation)
    with DB() as db:
        pending = [
            (l.run_id, l.terminal_status, l.terminal_reason)
            for l in db.scalars(
                select(RunLease).where(RunLease.terminal_status.is_not(None))
            )
        ]
    for rid, status, reason in pending:
        settle(rid, status, reason)
