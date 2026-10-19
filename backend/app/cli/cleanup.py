"""Purge expired chat/project trash only after persistent files are removed."""

import time
from sqlalchemy import select, delete
from app.platform.db import (
    DB,
    Chat,
    Project,
    Run,
    Event,
    LoginSession,
    LoginAttempt,
    ExecutionScope,
    RunLease,
)
from app.execution.lifecycle import transfers
from app.execution import client


def purge_scope(db, sid):
    if sid in transfers or db.get(RunLease, sid):
        return False
    scope = db.get(ExecutionScope, sid)
    if scope:
        if scope.has_files:
            try:
                client.RunnerClient().purge(scope.owner_id, sid)
            except Exception:
                return False
        db.delete(scope)
        db.flush()
    return True


def delete_chats(db, ids):
    db.execute(delete(Event).where(Event.session_id.in_(ids)))
    db.execute(delete(Run).where(Run.session_id.in_(ids)))
    db.execute(delete(Chat).where(Chat.id.in_(ids)))


def purge(now=None):
    now = time.time() if now is None else now
    count = 0
    with DB.begin() as db:
        projects = list(
            db.scalars(select(Project).where(Project.deleted_at < now - 7 * 86400))
        )
        for p in projects:
            children = list(db.scalars(select(Chat).where(Chat.project_id == p.id)))
            if any(c.status == "running" for c in children) or not purge_scope(
                db, p.id
            ):
                continue
            ids = [c.id for c in children]
            delete_chats(db, ids)
            count += len(ids)
            db.delete(p)
            db.flush()
        candidates = list(
            db.scalars(
                select(Chat).where(
                    Chat.deleted_at < now - 7 * 86400, Chat.status != "running"
                )
            )
        )
        for c in candidates:
            if c.project_id:
                if c.project_id in transfers or db.get(RunLease, c.project_id):
                    continue
                parent = db.get(Project, c.project_id)
                if parent and parent.deleted_at:
                    continue
            elif not purge_scope(db, c.id):
                continue
            delete_chats(db, [c.id])
            count += 1
        db.execute(delete(LoginSession).where(LoginSession.expires_at < now))
        db.execute(delete(LoginAttempt).where(LoginAttempt.reset_at < now))
    return count


if __name__ == "__main__":
    print("Purged chats:", purge())
