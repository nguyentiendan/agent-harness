"""s11: background tasks — run slow bash commands off the main loop (adapted)."""
import os
import subprocess
import threading
from typing import Any, Dict

from tasks import _fn  # reuse the small schema helper

_BASH_ENV_KEEP = {"PATH", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "TZ", "SHELL"}


def bash_env(workdir) -> Dict[str, str]:
    """Background shells also run without API keys/secrets in their environment."""
    env = {k: v for k, v in os.environ.items() if k in _BASH_ENV_KEEP}
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    env["HOME"] = str(workdir)
    env["PWD"] = str(workdir)
    return env


class BackgroundManager:
    def __init__(self, workdir, timeout: int = 600):
        self.workdir = workdir
        self.timeout = timeout
        self.tasks: Dict[str, Dict[str, Any]] = {}
        self.results: Dict[str, str] = {}
        self._counter = 0
        self._lock = threading.Lock()

    def start(self, command: str) -> str:
        command = str(command).strip()
        if not command:
            raise ValueError("command cannot be empty")
        with self._lock:
            self._counter += 1
            tid = f"bg_{self._counter:04d}"
            self.tasks[tid] = {"command": command, "status": "running"}
        t = threading.Thread(target=self._run, args=(tid, command), daemon=True)
        t.start()
        return tid

    def _run(self, tid: str, command: str):
        try:
            proc = subprocess.run(command, shell=True, cwd=self.workdir,
                                  capture_output=True, text=True, errors="replace",
                                  timeout=self.timeout, env=bash_env(self.workdir))
            out = (proc.stdout + proc.stderr).strip() or "(no output)"
            out = out[-30000:]
            status = "completed" if proc.returncode == 0 else "failed"
            result = f"exit_code={proc.returncode}\n{out}"
        except subprocess.TimeoutExpired:
            status, result = "failed", f"Error: timeout after {self.timeout}s"
        except Exception as e:
            status, result = "failed", f"Error: {type(e).__name__}: {e}"
        with self._lock:
            if tid in self.tasks:
                self.tasks[tid]["status"] = status
                self.results[tid] = result

    def check(self, tid: str) -> str:
        with self._lock:
            if tid not in self.tasks:
                return f"Unknown background task: {tid}"
            status = self.tasks[tid]["status"]
            if status == "running":
                return f"Background task {tid} is still running."
            result = self.results.get(tid, "")
            del self.tasks[tid]
            self.results.pop(tid, None)
        return f"Background task {tid} {status}:\n{result}"

    def list(self) -> str:
        with self._lock:
            if not self.tasks:
                return "(no background tasks)"
            lines = [f"- {tid}: {t['status']} — {t['command'][:60]}" for tid, t in self.tasks.items()]
        return "\n".join(lines)


def register(registry, workdir, timeout: int = 600) -> BackgroundManager:
    mgr = BackgroundManager(workdir, timeout)
    registry.register_tool("bash_background", _fn("bash_background",
        "Run a shell command in the background and return a task id.",
        {"command": {"type": "string"}}, ["command"]),
        lambda **kw: f"[Background task {mgr.start(kw['command'])} started]")
    registry.register_tool("check_background", _fn("check_background",
        "Check a background task by id (returns its result when done).",
        {"task_id": {"type": "string"}}, ["task_id"]),
        lambda **kw: mgr.check(kw["task_id"]))
    registry.register_tool("list_background", _fn("list_background",
        "List running background tasks.", {}, []),
        lambda **kw: mgr.list())
    return mgr
