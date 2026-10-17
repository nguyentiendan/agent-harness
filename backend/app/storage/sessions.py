"""storage / sessions."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List
import json
import re
import time

from fastapi import HTTPException

from app.config import DATA_DIR, SESSIONS_FILE, SESSION_EVENTS_DIR
from app.security.redaction import redact_text


def _load_sessions() -> List[Dict[str, Any]]:
    if SESSIONS_FILE.exists():
        try:
            data = json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []
    return []


def _save_sessions(records: List[Dict[str, Any]]):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_FILE.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def session_events_path(session_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id):
        raise HTTPException(status_code=400, detail="Invalid session ID.")
    path = (SESSION_EVENTS_DIR / f"{session_id}.json").resolve()
    if not path.is_relative_to(SESSION_EVENTS_DIR.resolve()):
        raise HTTPException(status_code=400, detail="Invalid session path.")
    return path


def _save_session_events(session_id: str, events: List[Dict[str, Any]]):
    SESSION_EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    payload = redact_text(json.dumps(events, ensure_ascii=False))
    session_events_path(session_id).write_text(payload, encoding="utf-8")


def _record_session(session_id: str, **fields: Any):
    for s in sessions:
        if s["session_id"] == session_id:
            s.update(fields)
            s["updated_at"] = time.time()
            _save_sessions(sessions)
            return
    sessions.append({"session_id": session_id, "created_at": time.time(),
                     "updated_at": time.time(), **fields})
    _save_sessions(sessions)


sessions: List[Dict[str, Any]] = _load_sessions()
