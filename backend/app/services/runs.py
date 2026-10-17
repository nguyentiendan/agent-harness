"""services / runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List
import asyncio
import json
import time

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.agent.harness import Harness
from app.agent.state import StateManager
from app.api.schemas import StartHarnessRequest
from app.config import CONFIG_FILE, load_config
from app.observability.metrics import _compute_metrics
from app.services.approvals import broker
from app.services.runtime import active_sessions, session_busy
from app.services.sessions import get_session, session_details
from app.services.workspaces import resolve_workspace
from app.storage.sessions import _record_session, _save_session_events, session_events_path, sessions


async def start_harness(request: StartHarnessRequest):
    session_events_path(request.session_id)
    active = active_sessions.get(request.session_id)
    if session_busy(active):
        raise HTTPException(status_code=409, detail="This session is still running.")
    record = next((s for s in sessions if s["session_id"] == request.session_id), None)
    workspace_name = Path(record["workspace"]).name if record else request.workspace_path
    ws_abs = resolve_workspace(workspace_name or "default")
    events: List[Dict[str, Any]] = []
    if active or record:
        events = list((await get_session(request.session_id))["events"])
        if record and not any(e.get("type") == "user" for e in events):
            events.insert(0, {"type": "user", "text": record["goal"]})
    history = [{"role": e["type"], "content": str(e.get("text", ""))}
               for e in events if e.get("type") in ("user", "assistant")]
    event_offset = len(events)
    events.append({"type": "user", "text": request.goal})

    async def approve(name: str, args: Dict[str, Any], reason: str) -> bool:
        aid = broker.create(request.session_id, name, args, reason)
        events.append({"type": "approval", "approval_id": aid, "name": name,
                       "args": args, "reason": reason})
        return await broker.wait(aid)

    harness = Harness(
        config_file=CONFIG_FILE,
        workspace_path=str(ws_abs),
        default_model=request.primary_model,
        events=events,
        approve=approve,
    )

    model_used = request.primary_model or load_config().get("llm", {}).get("primary_model", "gpt-4o-mini")
    active_sessions[request.session_id] = {
        "status": "running",
        "goal": request.goal,
        "harness": harness,
        "workspace_path": str(ws_abs),
        "events": events,
        "task": None,
        "started_at": time.time(),
        "model": model_used,
        "event_offset": event_offset,
    }
    _record_session(request.session_id, goal=record["goal"] if record else request.goal,
                    status="running", workspace=str(ws_abs), error="", result_text="")
    _save_session_events(request.session_id, events)

    async def run_loop():
        status = "completed"
        try:
            result = await harness.run(
                goal=request.goal,
                max_turns=request.max_turns,
                reviewer_model=request.reviewer_model,
                history=history,
            )
            status = "stopped" if harness.is_stop_requested() else "completed"
            active_sessions[request.session_id]["status"] = status
            active_sessions[request.session_id]["result_text"] = getattr(result, "text", "") or ""
        except asyncio.CancelledError:
            status = "stopped"
            active_sessions[request.session_id]["status"] = status
        except Exception as e:
            status = "failed"
            active_sessions[request.session_id]["status"] = status
            active_sessions[request.session_id]["error"] = str(e)

        _finish_run(request.session_id, status)

    task = asyncio.create_task(run_loop())
    active_sessions[request.session_id]["task"] = task
    return {"message": "Harness session started", "session_id": request.session_id,
            "workspace": str(ws_abs), "event_offset": event_offset}


def _finish_run(session_id: str, status: str):
    """Persist a terminal event and metrics, including a task cancelled before start."""
    session = active_sessions[session_id]
    session["status"] = status
    events = session["events"]
    events.append({"type": "status", "status": status, "reason": session.get("error", "")})
    try:
        turns = int(StateManager(Path(session["workspace_path"])).load_state().get("turn", 0) or 0)
    except Exception:
        turns = 0
    metrics = _compute_metrics(events[session["event_offset"]:], session["started_at"],
                               status, session.get("model", ""), turns)
    session["metrics"] = metrics
    _record_session(session_id, status=status, error=session.get("error", ""),
                    result_text=session.get("result_text", ""), metrics=metrics)
    _save_session_events(session_id, events)


async def stop_harness(session_id: str):
    if session_id not in active_sessions:
        raise HTTPException(status_code=404, detail="Session not found.")

    session = active_sessions[session_id]
    if session["status"] == "running":
        session["harness"].request_stop()
        task = session.get("task")
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                # Cancellation before run_loop enters cannot execute its cleanup.
                pass
        if not session["events"] or session["events"][-1].get("type") != "status":
            _finish_run(session_id, "stopped")

    return {"message": "Harness session stop requested", "session_id": session_id,
            "status": session["status"]}


async def get_status(session_id: str):
    session_info = session_details(session_id)
    state_mgr = StateManager(Path(session_info["workspace_path"]))
    state_data = state_mgr.load_state()

    return {
        "session_id": session_id,
        "status": session_info["status"],
        "goal": session_info["goal"],
        "current_turn": state_data.get("turn", 0),
        "progress_log": session_info.get("progress_log", ""),
        "result_text": session_info.get("result_text", ""),
        "metrics": session_info.get("metrics", {}),
        "state": state_data,
    }


async def stream_progress(session_id: str, after: int = 0):
    if session_id not in active_sessions:
        raise HTTPException(status_code=404, detail="Session not found.")

    session = active_sessions[session_id]
    events = session["events"]

    async def event_generator():
        idx = max(0, min(after, len(events)))
        while True:
            while idx < len(events):
                yield f"data: {json.dumps(events[idx], ensure_ascii=False)}\n\n"
                idx += 1
            if session["status"] in ["completed", "failed", "stopped"]:
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
