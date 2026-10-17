"""services / runtime."""

from __future__ import annotations

from typing import Any, Dict, Optional


def session_busy(session: Optional[Dict[str, Any]]) -> bool:
    return bool(session and (session.get("status") == "running" or
                (session.get("task") and not session["task"].done())))


active_sessions: Dict[str, Dict[str, Any]] = {}
