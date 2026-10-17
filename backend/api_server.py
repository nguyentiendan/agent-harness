"""
FastAPI Server Wrapper for Agent Harness Core
=============================================
Exposes the 7-Layer Agent Harness (Long Tasks) as an async REST + SSE API.

- config.yaml is the single source of truth for defaults (GET /api/config).
- Sessions are persisted to disk (GET /api/sessions) and streamed via SSE.
- Workspaces are listed/created under WORKSPACES_ROOT.
- Destructive tool calls can require approval (GET/POST /api/harness/approve).
"""

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from harness_core import Harness, StateManager
from redact import redact_text

CONFIG_FILE = os.environ.get("HARNESS_CONFIG", "config.yaml")
WORKSPACES_ROOT = Path(os.environ.get("WORKSPACES_ROOT", "./workspaces")).resolve()
DATA_DIR = Path(os.environ.get("HARNESS_DATA_DIR", "./data")).resolve()
SESSIONS_FILE = DATA_DIR / "sessions.json"
SESSION_EVENTS_DIR = DATA_DIR / "sessions"

app = FastAPI(
    title="Agent Harness API",
    description="Backend API wrapper for 7-Layer Agent Harness (Long Tasks)",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------------------------------------------------
# Persistent session store
# ----------------------------------------------------------------------
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


def _save_session_events(session_id: str, events: List[Dict[str, Any]]):
    SESSION_EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    payload = redact_text(json.dumps(events, ensure_ascii=False))
    (SESSION_EVENTS_DIR / f"{session_id}.json").write_text(payload, encoding="utf-8")


def _compute_metrics(events: List[Dict[str, Any]], started_at: float, status: str,
                     model: str, turns: int) -> Dict[str, Any]:
    """Aggregate basic observability metrics from a run's events."""
    tool_calls = [e for e in events if e.get("type") == "tool_call"]
    tools: Dict[str, int] = {}
    for e in tool_calls:
        name = str(e.get("name", "?"))
        tools[name] = tools.get(name, 0) + 1
    return {
        "status": status,
        "duration_seconds": round(max(0.0, time.time() - started_at), 1),
        "turns": turns,
        "tool_calls": len(tool_calls),
        "tools": tools,
        "approvals": sum(1 for e in events if e.get("type") == "approval"),
        "model": model,
    }


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
active_sessions: Dict[str, Dict[str, Any]] = {}


# ----------------------------------------------------------------------
# Approval broker (non-interactive approval via the API)
# ----------------------------------------------------------------------
class ApprovalBroker:
    def __init__(self):
        self.pending: Dict[str, Dict[str, Any]] = {}
        self._counter = 0

    def create(self, session_id: str, name: str, args: Dict[str, Any], reason: str) -> str:
        self._counter += 1
        aid = f"approval_{self._counter}_{int(time.time() * 1000)}"
        self.pending[aid] = {
            "session_id": session_id, "name": name, "args": args,
            "reason": reason, "future": asyncio.get_running_loop().create_future(),
            "created_at": time.time(),
        }
        return aid

    async def wait(self, approval_id: str) -> bool:
        item = self.pending.get(approval_id)
        if not item:
            return False
        try:
            return await item["future"]
        finally:
            self.pending.pop(approval_id, None)

    def list_pending(self, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        out = []
        for aid, item in self.pending.items():
            if session_id and item["session_id"] != session_id:
                continue
            out.append({
                "approval_id": aid, "session_id": item["session_id"],
                "name": item["name"], "args": item["args"], "reason": item["reason"],
                "created_at": item["created_at"],
            })
        return out

    def resolve(self, approval_id: str, allow: bool) -> bool:
        item = self.pending.get(approval_id)
        if not item or item["future"].done():
            return False
        item["future"].set_result(bool(allow))
        return True


broker = ApprovalBroker()


def load_config() -> Dict[str, Any]:
    path = Path(CONFIG_FILE)
    if path.exists():
        try:
            return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as e:
            print(f"[Config] Warning: could not parse {path}: {e}")
    return {}


def validate_workspace_name(name: str) -> str:
    """A workspace is addressed by NAME only (never an absolute/escaping path)."""
    name = (name or "").strip()
    if (
        not name
        or name in (".", "..")
        or name.startswith(".")
        or "/" in name
        or "\\" in name
        or "\x00" in name
    ):
        raise HTTPException(status_code=400, detail="Invalid workspace name.")
    return name


def resolve_workspace(name: str) -> Path:
    """Resolve a workspace name under WORKSPACES_ROOT (absolute paths rejected)."""
    name = validate_workspace_name(name or "default")
    root = WORKSPACES_ROOT.resolve()
    path = (root / name).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="Workspace escapes the workspaces root.")
    return path


# Workspace files the UI is allowed to read directly (whitelist).
WORKSPACE_FILES = ["progress.md", "state.json", "rules.md"]


class StartHarnessRequest(BaseModel):
    session_id: str
    goal: str
    max_turns: Optional[int] = None
    primary_model: Optional[str] = None
    reviewer_model: Optional[str] = None
    workspace_path: Optional[str] = None


class CommandRequest(BaseModel):
    session_id: Optional[str] = None
    command: str
    args: str = ""


class CreateWorkspaceRequest(BaseModel):
    name: str


class ApproveRequest(BaseModel):
    allow: bool


# ----------------------------------------------------------------------
# Config / workspaces / sessions
# ----------------------------------------------------------------------
@app.get("/api/config")
async def get_config():
    return {"config": load_config(), "config_path": str(Path(CONFIG_FILE).resolve())}


@app.get("/api/workspaces")
async def list_workspaces():
    WORKSPACES_ROOT.mkdir(parents=True, exist_ok=True)
    names = sorted(d.name for d in WORKSPACES_ROOT.iterdir() if d.is_dir())
    if not names:
        (WORKSPACES_ROOT / "default").mkdir(parents=True, exist_ok=True)
        names = ["default"]
    return {"workspaces": names, "root": str(WORKSPACES_ROOT)}


@app.post("/api/workspaces")
async def create_workspace(payload: CreateWorkspaceRequest):
    name = validate_workspace_name(payload.name)
    (WORKSPACES_ROOT / name).mkdir(parents=True, exist_ok=True)
    return {"workspace": name, "path": str(WORKSPACES_ROOT / name)}


@app.get("/api/sessions")
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


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    a = active_sessions.get(session_id)
    if a:
        return {"session_id": session_id, "events": a.get("events", [])}
    f = SESSION_EVENTS_DIR / f"{session_id}.json"
    if f.exists():
        try:
            return {"session_id": session_id, "events": json.loads(f.read_text(encoding="utf-8"))}
        except Exception:
            pass
    raise HTTPException(status_code=404, detail="Session not found.")


# ----------------------------------------------------------------------
# Harness control
# ----------------------------------------------------------------------
@app.post("/api/harness/start")
async def start_harness(request: StartHarnessRequest):
    if request.session_id in active_sessions:
        raise HTTPException(status_code=400, detail="Session ID already exists.")

    ws_abs = resolve_workspace(request.workspace_path or "default")
    events: List[Dict[str, Any]] = []

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
    }
    _record_session(request.session_id, goal=request.goal, status="running", workspace=str(ws_abs))

    async def run_loop():
        status = "completed"
        try:
            result = await harness.run(
                goal=request.goal,
                max_turns=request.max_turns,
                reviewer_model=request.reviewer_model,
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

        events.append({"type": "status", "status": status,
                       "reason": active_sessions[request.session_id].get("error", "")})

        session_info = active_sessions[request.session_id]
        try:
            turns = int(StateManager(Path(session_info["workspace_path"])).load_state().get("turn", 0) or 0)
        except Exception:
            turns = 0
        metrics = _compute_metrics(events, session_info.get("started_at", time.time()),
                                   status, session_info.get("model", ""), turns)
        session_info["metrics"] = metrics

        _record_session(request.session_id, status=status,
                        error=session_info.get("error", ""),
                        result_text=session_info.get("result_text", ""),
                        metrics=metrics)
        _save_session_events(request.session_id, events)

    task = asyncio.create_task(run_loop())
    active_sessions[request.session_id]["task"] = task
    return {"message": "Harness session started", "session_id": request.session_id,
            "workspace": str(ws_abs)}


@app.post("/api/harness/stop/{session_id}")
async def stop_harness(session_id: str):
    if session_id not in active_sessions:
        raise HTTPException(status_code=404, detail="Session not found.")

    session = active_sessions[session_id]
    if session["status"] == "running":
        session["harness"].request_stop()
        task = session.get("task")
        if task and not task.done():
            task.cancel()
        session["status"] = "stopped"

    return {"message": "Harness session stop requested", "session_id": session_id,
            "status": session["status"]}


@app.get("/api/harness/status/{session_id}")
async def get_status(session_id: str):
    if session_id not in active_sessions:
        raise HTTPException(status_code=404, detail="Session not found.")

    session_info = active_sessions[session_id]
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


@app.get("/api/harness/files/{session_id}")
async def get_workspace_files(session_id: str):
    if session_id not in active_sessions:
        raise HTTPException(status_code=404, detail="Session not found.")

    ws_path = Path(active_sessions[session_id]["workspace_path"])
    files: Dict[str, Optional[str]] = {}
    for rel in WORKSPACE_FILES:
        p = ws_path / rel
        files[rel] = p.read_text(encoding="utf-8") if p.exists() else None
    return {"session_id": session_id, "files": files}


@app.get("/api/harness/list/{session_id}")
async def list_workspace(session_id: str):
    if session_id not in active_sessions:
        raise HTTPException(status_code=404, detail="Session not found.")

    ws_path = Path(active_sessions[session_id]["workspace_path"])
    entries: List[Dict[str, Any]] = []
    if ws_path.exists():
        for p in sorted(ws_path.rglob("*")):
            if p.is_file():
                rel = str(p.relative_to(ws_path))
                parts = Path(rel).parts
                if any(part.startswith(".") for part in parts) or "worktrees" in parts:
                    continue
                if rel in {"state.json", "progress.md", "rules.md", "todos.json"}:
                    continue
                entries.append({"path": rel, "size": p.stat().st_size})
    return {"session_id": session_id, "files": entries[:500]}


@app.get("/api/harness/read/{session_id}")
async def read_workspace_file(session_id: str, path: str):
    """Read a single workspace file (path is relative to the workspace root)."""
    if session_id not in active_sessions:
        raise HTTPException(status_code=404, detail="Session not found.")
    ws_path = Path(active_sessions[session_id]["workspace_path"]).resolve()
    p = (ws_path / path).resolve()
    try:
        p.relative_to(ws_path)
    except ValueError:
        raise HTTPException(status_code=400, detail="Path outside workspace.")
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="File not found.")
    return {"path": path, "content": p.read_text(encoding="utf-8", errors="replace")}


@app.get("/api/harness/approvals/{session_id}")
async def list_approvals(session_id: str):
    return {"approvals": broker.list_pending(session_id)}


@app.post("/api/harness/approve")
async def resolve_approval(payload: ApproveRequest, approval_id: str):
    if not broker.resolve(approval_id, payload.allow):
        raise HTTPException(status_code=404, detail="Approval not found or already resolved.")
    return {"approval_id": approval_id, "allowed": payload.allow}


# ----------------------------------------------------------------------
# Slash commands (e.g. /help, /tasks, /skills, /memory, /usage, /compact)
# ----------------------------------------------------------------------
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


@app.post("/api/command")
async def run_command(payload: CommandRequest):
    """Handle a slash command sent from the chat input."""
    cmd = payload.command.lstrip("/").strip().lower()
    if not cmd or cmd in ("help", "commands", "?"):
        return {"output": COMMAND_HELP}

    session = active_sessions.get(payload.session_id or "")

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
        f = SESSION_EVENTS_DIR / f"{sid}.json"
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
            f.write_text(json.dumps(compacted, ensure_ascii=False), encoding="utf-8")
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
        from tasks import TaskStore
        rows = []
        for t in TaskStore(ws_path / ".tasks").list():
            marker = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}.get(t.status, "[?]")
            deps = f" (blockedBy: {', '.join(t.blockedBy)})" if t.blockedBy else ""
            owner = f" [{t.owner}]" if t.owner else ""
            rows.append(f"{marker} {t.id}: {t.subject} [{t.status}]{owner}{deps}")
        return {"output": "\n".join(rows) if rows else "No tasks in this workspace."}

    if cmd == "skills":
        from skills import SkillLoader
        return {"output": SkillLoader(ws_path / "skills").catalog()}

    if cmd == "memory":
        from memory import MemoryStore
        return {"output": MemoryStore(ws_path / ".memory").list_memories()}

    return {"output": f"Unknown command: /{cmd}\n\n{COMMAND_HELP}"}


# ----------------------------------------------------------------------
# SSE: structured events for the chat UI
# ----------------------------------------------------------------------
@app.get("/api/harness/stream/{session_id}")
async def stream_progress(session_id: str):
    if session_id not in active_sessions:
        raise HTTPException(status_code=404, detail="Session not found.")

    events = active_sessions[session_id]["events"]

    async def event_generator():
        idx = 0
        while True:
            while idx < len(events):
                yield f"data: {json.dumps(events[idx], ensure_ascii=False)}\n\n"
                idx += 1
            if active_sessions[session_id]["status"] in ["completed", "failed", "stopped"]:
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn
    print("Starting Agent Harness API Server on http://localhost:8000...")
    uvicorn.run(app, host="0.0.0.0", port=8000)
