"""Purge chat trash after its retention period; safe to run repeatedly."""

import time
from sqlalchemy import select, delete
from app.platform.db import (
    DB,
    Chat,
    Run,
    Event,
    LoginSession,
    LoginAttempt,
    ExecutionScope,
    RunLease,
)


def purge(now=None):
    now = time.time() if now is None else now
    with DB.begin() as db:
        ids = list(
            db.scalars(
                select(Chat.id).where(
                    Chat.deleted_at < now - 7 * 86400, Chat.status != "running"
                )
            )
        )
        if ids:
            from app.execution import client

            removable = []
            for sid in ids:
                if db.get(RunLease, sid):
                    continue
                scope = db.get(ExecutionScope, sid)
                if scope and scope.has_files:
                    try:
                        client.RunnerClient().purge(scope.owner_id, sid)
                    except Exception:
                        continue
                removable.append(sid)
            ids = removable
            db.execute(delete(ExecutionScope).where(ExecutionScope.id.in_(ids)))
            db.execute(delete(Event).where(Event.session_id.in_(ids)))
            db.execute(delete(Run).where(Run.session_id.in_(ids)))
            db.execute(delete(Chat).where(Chat.id.in_(ids)))
        db.execute(delete(LoginSession).where(LoginSession.expires_at < now))
        db.execute(delete(LoginAttempt).where(LoginAttempt.reset_at < now))
    return len(ids)


if __name__ == "__main__":
    print("Purged chats:", purge())
