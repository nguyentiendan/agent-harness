"""services / commands."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List
import json

from fastapi import HTTPException

from app.api.schemas import CommandRequest
from app.services.runtime import active_sessions, session_busy
from app.services.sessions import session_details
from app.storage.sessions import _save_session_events, session_events_path, sessions


COMMAND_HELP = (
    "/help              show this help\n"
    "/new               start a new conversation\n"
    "/stop              stop the running session\n"
    "/model <name>      set the primary model for the next run\n"
    "/settings          open the settings panel\n"
    "/files             open the workspace files panel\n"
    "/status            current session status + metrics\n"
    "/tasks             list workspace tasks\n"
    "/skills            list available skills\n"
    "/memory            list stored memories\n"
    "/usage             usage metrics (this session + totals)\n"
    "/compact           compact this session's stored history"
)


async def run_command(payload: CommandRequest):
    """Handle a slash command sent from the chat input."""
    cmd = payload.command.lstrip("/").strip().lower()
    if not cmd or cmd in ("help", "commands", "?"):
        return {"output": COMMAND_HELP}

    try:
        session = session_details(payload.session_id or "")
    except HTTPException:
        session = None

    if cmd == "status":
        if not session:
            return {"output": "No session selected."}
        m = session.get("metrics", {}) or {}
        return {"output": (
            f"status:     {session.get('status')}\n"
            f"goal:       {session.get('goal')}\n"
            f"model:      {session.get('model')}\n"
            f"turn:       {m.get('turns', '-')}\n"
            f"tool calls: {m.get('tool_calls', '-')}\n"
            f"duration:   {m.get('duration_seconds', '-')}s"
        )}

    if cmd == "usage":
        mine = (session.get("metrics", {}) if session else {}) or {}
        total_runs = len(sessions)
        total_calls = sum(int((s.get("metrics") or {}).get("tool_calls", 0) or 0) for s in sessions)
        return {"output": (
            f"this session: {mine.get('duration_seconds', '-')}s · "
            f"{mine.get('tool_calls', '-')} tool calls · {mine.get('turns', '-')} turns\n"
            f"all sessions: {total_runs} runs · {total_calls} tool calls\n"
            "token/cost per request: LiteLLM Admin UI -> http://localhost:4000/ui"
        )}

    if cmd == "compact":
        sid = payload.session_id or ""
        if session_busy(active_sessions.get(sid)):
            raise HTTPException(status_code=409, detail="Stop this session before compacting history.")
        if not sid:
            return {"output": "No stored history for this session yet."}
        f = session_events_path(sid)
        if not sid or not f.exists():
            return {"output": "No stored history for this session yet."}
        try:
            events = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            return {"output": f"Could not read history: {e}"}
        before = len(json.dumps(events, ensure_ascii=False))
        compacted: List[Dict[str, Any]] = []
        for ev in events:
            if ev.get("type") == "tool_result":
                out = str(ev.get("output", ""))
                if len(out) > 400:
                    ev = {**ev, "output": out[:400] + f"\n...[compacted {len(out) - 400} chars]"}
            elif ev.get("type") == "tool_call":
                args_text = json.dumps(ev.get("args") or {}, ensure_ascii=False)
                if len(args_text) > 300:
                    ev = {**ev, "args": {"_compacted": args_text[:300] + "..."}}
            compacted.append(ev)
        after = len(json.dumps(compacted, ensure_ascii=False))
        try:
            _save_session_events(sid, compacted)
            if sid in active_sessions:
                active_sessions[sid]["events"] = compacted
        except Exception as e:
            return {"output": f"Could not write history: {e}"}
        return {"output": (
            f"Compacted stored history: {before} -> {after} chars "
            f"(saved {max(0, before - after)}).\n"
            "Note: this compacts the saved session history; the live agent "
            "context is already compacted per run."
        )}

    # The remaining commands read the session's workspace.
    if not session:
        return {"output": "Select or start a session first (these commands read its workspace)."}
    ws_path = Path(session["workspace_path"])

    if cmd == "tasks":
        from app.capabilities.tasks import TaskStore
        rows = []
        for t in TaskStore(ws_path / ".tasks").list():
            marker = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}.get(t.status, "[?]")
            deps = f" (blockedBy: {', '.join(t.blockedBy)})" if t.blockedBy else ""
            owner = f" [{t.owner}]" if t.owner else ""
            rows.append(f"{marker} {t.id}: {t.subject} [{t.status}]{owner}{deps}")
        return {"output": "\n".join(rows) if rows else "No tasks in this workspace."}

    if cmd == "skills":
        from app.capabilities.skills import SkillLoader
        return {"output": SkillLoader(ws_path / "skills").catalog()}

    if cmd == "memory":
        from app.capabilities.memory import MemoryStore
        return {"output": MemoryStore(ws_path / ".memory").list_memories()}

    return {"output": f"Unknown command: /{cmd}\n\n{COMMAND_HELP}"}
