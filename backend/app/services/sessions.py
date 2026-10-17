"""services / sessions."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict
import json

from fastapi import HTTPException

from app.services.approvals import broker
from app.services.runtime import active_sessions, session_busy
from app.services.workspaces import resolve_workspace
from app.storage.sessions import _save_sessions, session_events_path, sessions


async def list_sessions():
    result = []
    for s in sessions:
        rec = dict(s)
        a = active_sessions.get(s["session_id"])
        if a:
            rec["status"] = a.get("status", s.get("status"))
        result.append(rec)
    result.sort(key=lambda x: x.get("updated_at", 0), reverse=True)
    return {"sessions": result}


def session_details(session_id: str) -> Dict[str, Any]:
    if session_id in active_sessions:
        return active_sessions[session_id]
    record = next((s for s in sessions if s["session_id"] == session_id), None)
    if not record:
        raise HTTPException(status_code=404, detail="Session not found.")
    return {**record, "workspace_path": str(resolve_workspace(Path(record["workspace"]).name)),
            "model": (record.get("metrics") or {}).get("model", "")}


async def delete_session(session_id: str):
    path = session_events_path(session_id)
    active = active_sessions.get(session_id)
    if active is None and not any(s["session_id"] == session_id for s in sessions):
        raise HTTPException(status_code=404, detail="Session not found.")
    if session_busy(active):
        raise HTTPException(status_code=409, detail="This session is still running. Stop it before deleting.")
    for aid, item in list(broker.pending.items()):
        if item["session_id"] == session_id:
            broker.resolve(aid, False)
    active_sessions.pop(session_id, None)
    sessions[:] = [s for s in sessions if s["session_id"] != session_id]
    _save_sessions(sessions)
    path.unlink(missing_ok=True)
    return {"message": "Session deleted", "session_id": session_id}


async def get_session(session_id: str):
    a = active_sessions.get(session_id)
    if a:
        return {"session_id": session_id, "events": a.get("events", [])}
    f = session_events_path(session_id)
    if f.exists():
        try:
            return {"session_id": session_id, "events": json.loads(f.read_text(encoding="utf-8"))}
        except Exception:
            pass
    raise HTTPException(status_code=404, detail="Session not found.")
