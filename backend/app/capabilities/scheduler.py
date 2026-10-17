"""s12: cron scheduler — durable, session-scoped scheduled prompts (adapted)."""

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
import json

from datetime import datetime
import secrets
import threading

from app.capabilities.tasks import _fn


def _cron_field(field: str, value: int) -> bool:
    if field == "*":
        return True
    if field.startswith("*/"):
        return value % int(field[2:]) == 0
    if "," in field:
        return any(_cron_field(p.strip(), value) for p in field.split(","))
    if "-" in field:
        a, b = field.split("-", 1)
        return int(a) <= value <= int(b)
    return value == int(field)


def cron_matches(expr: str, moment: datetime) -> bool:
    fields = expr.strip().split()
    if len(fields) != 5:
        return False
    minute, hour, day, month, weekday = fields
    cw = (moment.weekday() + 1) % 7
    if not (_cron_field(minute, moment.minute) and _cron_field(hour, moment.hour)
            and _cron_field(month, moment.month)):
        return False
    dm = _cron_field(day, moment.day)
    wm = _cron_field(weekday, cw)
    if day == "*" and weekday == "*":
        return True
    if day == "*":
        return wm
    if weekday == "*":
        return dm
    return dm or wm


def validate_cron(expr: str) -> Optional[str]:
    fields = expr.strip().split()
    if len(fields) != 5:
        return f"expected 5 fields, got {len(fields)}"
    return None


@dataclass
class CronJob:
    id: str
    cron: str
    prompt: str
    recurring: bool = True
    durable: bool = True
    pending_delivery: bool = False
    last_fired: Optional[str] = None


class CronStore:
    def __init__(self, durable_path: Path):
        self.durable_path = Path(durable_path)
        self.jobs: Dict[str, CronJob] = {}
        self.queue: List[CronJob] = []
        self._lock = threading.RLock()
        self._load()

    def _load(self):
        if not self.durable_path.exists():
            return
        try:
            payload = json.loads(self.durable_path.read_text(encoding="utf-8"))
            for item in payload:
                try:
                    job = CronJob(**item)
                    self.jobs[job.id] = job
                    if job.pending_delivery:
                        self.queue.append(job)
                except (TypeError, ValueError):
                    continue
        except Exception:
            pass

    def _save(self):
        payload = [asdict(j) for j in self.jobs.values() if j.durable]
        self.durable_path.parent.mkdir(parents=True, exist_ok=True)
        self.durable_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def schedule(self, cron: str, prompt: str, recurring: bool = True, durable: bool = True) -> str:
        err = validate_cron(cron)
        if err:
            return f"Error: {err}"
        if not str(prompt).strip():
            return "Error: prompt cannot be empty"
        with self._lock:
            job = CronJob(id=f"cron_{secrets.token_hex(4)}", cron=cron, prompt=prompt,
                          recurring=recurring, durable=durable)
            self.jobs[job.id] = job
            if durable:
                self._save()
        return f"Scheduled {job.id}: {cron} -> {prompt}"

    def cancel(self, job_id: str) -> str:
        with self._lock:
            if job_id not in self.jobs:
                return f"Job {job_id} not found"
            job = self.jobs.pop(job_id)
            self.queue = [j for j in self.queue if j.id != job_id]
            if job.durable:
                self._save()
        return f"Cancelled {job_id}"

    def list(self) -> str:
        with self._lock:
            if not self.jobs:
                return "No cron jobs."
            return "\n".join(f"{j.id}: {j.cron} -> {j.prompt[:60]} "
                             f"[{'recurring' if j.recurring else 'one-shot'}]"
                             for j in self.jobs.values())

    def poll_due(self, moment: Optional[datetime] = None) -> List[str]:
        """Return prompts that became due, marking them delivered."""
        moment = moment or datetime.now()
        marker = moment.strftime("%Y-%m-%d %H:%M")
        delivered: List[str] = []
        with self._lock:
            for job in list(self.jobs.values()):
                if job.pending_delivery or job.last_fired == marker:
                    continue
                if cron_matches(job.cron, moment):
                    job.pending_delivery = True
                    job.last_fired = marker
                    if job.recurring:
                        job.pending_delivery = False  # clear for next cycle
                    else:
                        self.jobs.pop(job.id)
                    delivered.append(job.prompt)
                    if job.durable:
                        self._save()
        return delivered


def register(registry, durable_path: Path) -> CronStore:
    store = CronStore(durable_path)
    registry.register_tool("schedule_cron", _fn("schedule_cron",
        "Schedule a prompt with a 5-field cron expression.",
        {"cron": {"type": "string"}, "prompt": {"type": "string"},
         "recurring": {"type": "boolean"}, "durable": {"type": "boolean"}},
        ["cron", "prompt"]),
        lambda **kw: store.schedule(kw["cron"], kw["prompt"],
                                    bool(kw.get("recurring", True)), bool(kw.get("durable", True))))
    registry.register_tool("list_crons", _fn("list_crons", "List scheduled cron jobs.", {}, []),
        lambda **kw: store.list())
    registry.register_tool("cancel_cron", _fn("cancel_cron", "Cancel a cron job by id.",
        {"job_id": {"type": "string"}}, ["job_id"]),
        lambda **kw: store.cancel(kw["job_id"]))
    return store
