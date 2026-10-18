"""Durable lease: never release ownership until Docker confirms cleanup."""

import threading
from sqlalchemy import select
from app.platform.db import DB, ExecutionScope, RunLease, Run, Chat
from . import client

cleanup_lock = threading.RLock()


def acquire(db, run):
    chat = db.get(Chat, run.session_id)
    scope = db.get(ExecutionScope, chat.id)
    if not scope:
        scope = ExecutionScope(id=chat.id, owner_id=chat.owner_id)
        db.add(scope)
        db.flush()
    lease = db.get(RunLease, scope.id)
    if lease and lease.run_id != run.id:
        raise RuntimeError("Execution scope is busy.")
    if not lease:
        db.add(RunLease(scope_id=scope.id, run_id=run.id))
        db.flush()
    return {"owner_id": scope.owner_id, "scope_id": scope.id, "run_id": run.id}


def prepare(rid):
    # Serializes lazy ensure with cancellation; a late worker thread cannot resurrect a stopped run.
    with cleanup_lock:
        with DB.begin() as db:
            run = db.get(Run, rid)
            if not run or run.status != "running":
                raise RuntimeError("Run is no longer active.")
            ctx = acquire(db, run)
            lease = db.get(RunLease, ctx["scope_id"])
            if lease.terminal_status:
                raise RuntimeError("Run is stopping.")
            first = not lease.sandbox_started
            lease.sandbox_started = True
            db.get(ExecutionScope, ctx["scope_id"]).has_files = True
        if first:
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


def retry_pending():
    with DB() as db:
        pending = [
            (l.run_id, l.terminal_status, l.terminal_reason)
            for l in db.scalars(
                select(RunLease).where(RunLease.terminal_status.is_not(None))
            )
        ]
    for rid, status, reason in pending:
        settle(rid, status, reason)
