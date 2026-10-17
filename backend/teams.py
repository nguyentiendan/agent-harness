"""s13: agent teams — shared task queue + task-bound worktrees (adapted)."""
import shutil
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from tasks import TaskStore, _fn


class WorktreeManager:
    """A per-task working directory under the worktrees root."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def path(self, task_id: str) -> Path:
        return self.root / task_id

    def create(self, task_id: str) -> Path:
        p = self.path(task_id)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def remove(self, task_id: str):
        p = self.path(task_id)
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)


class TeamManager:
    """Coordinates teammates over a shared TaskStore, one worktree per task."""

    def __init__(self, task_store: TaskStore, worktrees: WorktreeManager,
                 run_subagent: Callable[[str, Path], Any]):
        self.tasks = task_store
        self.worktrees = worktrees
        self.run_subagent = run_subagent

    def claim_next(self, owner: str = "teammate") -> Optional[str]:
        for t in self.tasks.list():
            if t.status == "pending" and self.tasks.can_start(t.id):
                self.tasks.claim(t.id, owner)
                return t.id
        return None

    def work_on(self, task_id: str, owner: str = "teammate") -> str:
        task = self.tasks.load(task_id)
        if task.status != "in_progress" or task.owner != owner:
            return f"Task {task_id} is not owned by {owner}"
        workdir = self.worktrees.create(task_id)
        prompt = f"Task: {task.subject}\nDescription: {task.description or '(none)'}\nWork directory: {workdir}"
        try:
            result = self.run_subagent(prompt, workdir)
        except Exception as e:
            return f"Error: {type(e).__name__}: {e}"
        done = self.tasks.complete(task_id, owner)
        return f"{done}\nResult:\n{result}"

    def status(self) -> str:
        tasks = self.tasks.list()
        if not tasks:
            return "No tasks in the shared queue."
        return "\n".join(f"- {t.id} [{t.status}] {t.subject}" + (f" (owner: {t.owner})" if t.owner else "")
                         for t in tasks)


def register(registry, task_store: TaskStore, worktrees_root: Path,
             run_subagent: Callable[[str, Path], Any]) -> TeamManager:
    wt = WorktreeManager(worktrees_root)
    mgr = TeamManager(task_store, wt, run_subagent)
    registry.register_tool("team_claim_next", _fn("team_claim_next",
        "Claim the next ready task for a teammate.", {}, []),
        lambda **kw: mgr.claim_next() or "(no ready task)")
    registry.register_tool("team_work_on", _fn("team_work_on",
        "Delegate an owned task to a teammate (runs a subagent in a task-bound worktree).",
        {"task_id": {"type": "string"}}, ["task_id"]),
        lambda **kw: mgr.work_on(kw["task_id"]))
    registry.register_tool("team_status", _fn("team_status",
        "Show the shared task queue status.", {}, []),
        lambda **kw: mgr.status())
    return mgr
