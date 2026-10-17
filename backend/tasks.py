"""s10: task system — file-backed dependency graph (learn-claude-code, adapted)."""
import json
import re
import secrets
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional

TASK_ID_RE = re.compile(r"^task_[0-9a-f]{8}$")


def _fn(name, desc, props, required):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": required},
    }}


@dataclass
class Task:
    id: str
    subject: str
    description: str = ""
    status: str = "pending"
    owner: Optional[str] = None
    blockedBy: List[str] = field(default_factory=list)


class TaskStore:
    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def _root(self, create: bool = False) -> Path:
        if create:
            self.directory.mkdir(parents=True, exist_ok=True)
        return self.directory.resolve()

    def _path(self, task_id: str) -> Path:
        if not TASK_ID_RE.fullmatch(str(task_id)):
            raise ValueError(f"Invalid task ID: {task_id!r}")
        return (self._root() / f"{task_id}.json").resolve()

    def exists(self, task_id: str) -> bool:
        return self._path(task_id).is_file()

    def create(self, subject: str, description: str = "") -> Task:
        subject = subject.strip()
        if not subject:
            raise ValueError("Task subject cannot be empty")
        self._root(create=True)
        for _ in range(100):
            task = Task(id=f"task_{secrets.token_hex(4)}", subject=subject, description=description)
            try:
                with self._path(task.id).open("x", encoding="utf-8") as f:
                    json.dump(asdict(task), f, indent=2)
                return task
            except FileExistsError:
                continue
        raise RuntimeError("Could not allocate a unique task ID")

    def load(self, task_id: str) -> Task:
        data = json.loads(self._path(task_id).read_text(encoding="utf-8"))
        task = Task(**data)
        if task.status not in ("pending", "in_progress", "completed"):
            raise ValueError(f"Invalid task status: {task.status}")
        return task

    def save(self, task: Task):
        self._path(task.id).write_text(json.dumps(asdict(task), indent=2), encoding="utf-8")

    def list(self) -> List[Task]:
        if not self.directory.exists():
            return []
        return [self.load(p.stem) for p in sorted(self.directory.glob("task_*.json"))]

    def _depends_on(self, task_id: str, target: str) -> bool:
        pending = [task_id]
        seen = set()
        while pending:
            cur = pending.pop()
            if cur == target:
                return True
            if cur in seen:
                continue
            seen.add(cur)
            pending.extend(self.load(cur).blockedBy)
        return False

    def update_dependencies(self, task_id: str, add_blocked_by: List[str]) -> Task:
        task = self.load(task_id)
        if task.status != "pending" or task.owner is not None:
            raise ValueError("Dependencies can only change while pending and unowned")
        deps = list(dict.fromkeys(add_blocked_by))
        for d in deps:
            if d == task_id:
                raise ValueError("Task cannot depend on itself")
            if not self.exists(d):
                raise ValueError(f"Dependency not found: {d}")
            if d not in task.blockedBy and self._depends_on(d, task_id):
                raise ValueError(f"Dependency cycle detected: {task_id} -> {d}")
        task.blockedBy.extend(d for d in deps if d not in task.blockedBy)
        self.save(task)
        return task

    def incomplete_dependencies(self, task: Task) -> List[str]:
        out = []
        for d in task.blockedBy:
            try:
                if self.load(d).status != "completed":
                    out.append(d)
            except (FileNotFoundError, ValueError):
                out.append(d)
        return out

    def can_start(self, task_id: str) -> bool:
        return not self.incomplete_dependencies(self.load(task_id))

    def claim(self, task_id: str, owner: str = "agent") -> str:
        task = self.load(task_id)
        if task.status != "pending":
            return f"Task {task_id} is {task.status}, cannot claim"
        deps = self.incomplete_dependencies(task)
        if deps:
            return f"Blocked by: {deps}"
        task.owner = owner
        task.status = "in_progress"
        self.save(task)
        return f"Claimed {task.id} ({task.subject})"

    def complete(self, task_id: str, owner: str = "agent") -> str:
        task = self.load(task_id)
        if task.status != "in_progress":
            return f"Task {task_id} is {task.status}, cannot complete"
        if task.owner != owner:
            return f"Task {task_id} is owned by {task.owner}"
        task.status = "completed"
        self.save(task)
        unblocked = [t.subject for t in self.list()
                     if t.status == "pending" and t.blockedBy and self.can_start(t.id)]
        msg = f"Completed {task.id} ({task.subject})"
        return msg + (f"\nUnblocked: {', '.join(unblocked)}" if unblocked else "")


def register(registry, tasks_dir: Path) -> TaskStore:
    store = TaskStore(tasks_dir)

    def _create(subject, description=""):
        t = store.create(subject, description)
        return f"Created {t.id}: {t.subject}"

    def _update(task_id, addBlockedBy):
        t = store.update_dependencies(task_id, addBlockedBy or [])
        return f"Updated {t.id} blockedBy: {', '.join(t.blockedBy) or '(none)'}"

    def _list():
        tasks = store.list()
        if not tasks:
            return "No tasks."
        lines = []
        for t in tasks:
            marker = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}.get(t.status, "[?]")
            deps = f" (blockedBy: {', '.join(t.blockedBy)})" if t.blockedBy else ""
            owner = f" [{t.owner}]" if t.owner else ""
            lines.append(f"{marker} {t.id}: {t.subject} [{t.status}]{owner}{deps}")
        return "\n".join(lines)

    registry.register_tool("create_task", _fn("create_task", "Create a task and return its generated ID.",
        {"subject": {"type": "string"}, "description": {"type": "string"}}, ["subject"]), _create)
    registry.register_tool("update_task", _fn("update_task", "Add dependencies to a pending task.",
        {"task_id": {"type": "string"}, "addBlockedBy": {"type": "array", "items": {"type": "string"}}},
        ["task_id", "addBlockedBy"]), _update)
    registry.register_tool("list_tasks", _fn("list_tasks", "List tasks with status and dependencies.", {}, []), _list)
    registry.register_tool("claim_task", _fn("claim_task", "Claim a ready task.", {"task_id": {"type": "string"}}, ["task_id"]),
        lambda **kw: store.claim(kw["task_id"], "agent"))
    registry.register_tool("complete_task", _fn("complete_task", "Complete a claimed task.", {"task_id": {"type": "string"}}, ["task_id"]),
        lambda **kw: store.complete(kw["task_id"], "agent"))
    return store
