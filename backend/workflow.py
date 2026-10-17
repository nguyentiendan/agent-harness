"""s16: workflow runtime — saved orchestration with journal resume (adapted).

A workflow is a Python async callable `fn(ctx, args)` that uses the primitives
`ctx.agent / ctx.parallel / ctx.pipeline / ctx.phase / ctx.log`. Results are
journaled so a resume can replay unchanged `agent()` calls from cache.
"""
import asyncio
import hashlib
import json
import secrets
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from tasks import _fn  # reuse schema helper

CONCURRENCY = 8


def _stable_hash(s: str) -> int:
    return int(hashlib.sha256(s.encode()).hexdigest(), 16)


def _parse_json(text: str) -> Any:
    s = str(text).strip()
    if s.startswith("```"):
        lines = s.splitlines()
        lines = lines[1:] if lines else lines
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        dec = json.JSONDecoder()
        for i, ch in enumerate(s):
            if ch in "{[":
                try:
                    return dec.raw_decode(s[i:])[0]
                except json.JSONDecodeError:
                    continue
        raise


class WorkflowJournal:
    def __init__(self, path: Path, resume: bool = False):
        self.path = Path(path)
        self.cache: Dict[str, Any] = {}
        if resume and self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    rec = json.loads(line)
                    self.cache[rec["key"]] = rec["value"]
                except Exception:
                    continue
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._f = self.path.open("a" if resume else "w", encoding="utf-8")

    def key(self, kind: str, label: str, prompt: str, schema: Any) -> str:
        basis = f"{kind}|{label}|{prompt}|{json.dumps(schema, sort_keys=True, default=str)}"
        return f"{kind}-{_stable_hash(basis) % 10**10:010d}"

    def cached(self, key: str) -> Any:
        return self.cache.get(key)

    def record(self, key: str, value: Any):
        self._f.write(json.dumps({"key": key, "value": value}, default=str) + "\n")
        self._f.flush()
        self.cache[key] = value

    def close(self):
        self._f.close()


class ExecutionState:
    def __init__(self, runner, journal: WorkflowJournal, args: Dict[str, Any]):
        self.runner = runner
        self.journal = journal
        self.args = args or {}
        self.semaphore = asyncio.Semaphore(CONCURRENCY)
        self._phase = None
        self.events: List[Dict[str, Any]] = []

    def phase(self, title: str):
        self._phase = title
        self.events.append({"type": "workflow_phase", "title": title})

    def log(self, message: str):
        self.events.append({"type": "workflow_log", "message": str(message)})

    async def agent(self, prompt: str, schema: Any = None, label: Optional[str] = None, phase: Optional[str] = None):
        label = label or str(prompt)[:24]
        key = self.journal.key("agent", label, str(prompt), schema)
        cached = self.journal.cached(key)
        if cached is not None:
            return cached
        async with self.semaphore:
            value = await self.runner(str(prompt), schema)
        self.journal.record(key, value)
        self.events.append({"type": "workflow_agent", "label": label, "phase": phase or self._phase})
        return value

    async def parallel(self, thunks: List[Callable[[], Awaitable[Any]]]) -> List[Any]:
        return await asyncio.gather(*[t() for t in thunks])

    async def pipeline(self, items: List[Any], *stages) -> List[Any]:
        async def run_item(item, idx):
            value = item
            for stage in stages:
                value = await stage(value, item, idx)
            return value
        return await asyncio.gather(*[run_item(it, i) for i, it in enumerate(items)])


class AgentRunner:
    """Runs a workflow sub-agent through LiteLLM, optionally as structured JSON."""

    def __init__(self, llm, model: str):
        self.llm = llm
        self.model = model

    async def __call__(self, prompt: str, schema: Any = None) -> Any:
        request = str(prompt)
        if schema is not None:
            request += "\n\nReturn only one JSON object matching this schema:\n" + json.dumps(schema, sort_keys=True)
        resp = await self.llm.complete(
            messages=[{"role": "system", "content": "You are a focused workflow agent. Complete only the supplied step."},
                      {"role": "user", "content": request}],
            model=self.model, temperature=0.0, max_tokens=2000,
        )
        text = resp.choices[0].message.content or ""
        if schema is None:
            return text
        try:
            return _parse_json(text)
        except Exception:
            return text


class WorkflowRuntime:
    def __init__(self, runner: AgentRunner, store_dir: Path):
        self.runner = runner
        self.store = Path(store_dir)
        self.store.mkdir(parents=True, exist_ok=True)
        self.workflows: Dict[str, Callable[[ExecutionState, Dict[str, Any]], Awaitable[Any]]] = {}

    def register(self, name: str, fn):
        self.workflows[name] = fn

    async def run(self, name: str, args: Optional[Dict[str, Any]] = None,
                  resume_from_run_id: Optional[str] = None) -> str:
        if name not in self.workflows:
            return f"Error: unknown workflow '{name}'. Available: {', '.join(self.workflows) or 'none'}"
        args = args or {}
        run_id = resume_from_run_id or f"wf_{name}_{secrets.token_hex(8)}"
        journal = WorkflowJournal(self.store / f"{run_id}.journal.jsonl", resume=resume_from_run_id is not None)
        ctx = ExecutionState(self.runner, journal, args)
        try:
            result = await self.workflows[name](ctx, args)
        except Exception as e:
            result = {"error": f"{type(e).__name__}: {e}"}
        finally:
            journal.close()
        return json.dumps({"run_id": run_id, "result": result, "events": ctx.events},
                          ensure_ascii=False, default=str)


def register(registry, llm, model: str, store_dir: Path) -> WorkflowRuntime:
    runtime = WorkflowRuntime(AgentRunner(llm, model), store_dir)

    async def _run(name, args=None, resume_from_run_id=None):
        return await runtime.run(name, args, resume_from_run_id)

    def _handler(**kw):
        return asyncio.run(_run(kw.get("name", ""), kw.get("args"), kw.get("resume_from_run_id")))

    registry.register_tool("run_workflow", _fn("run_workflow",
        "Run a saved workflow by name (args is an optional JSON object).",
        {"name": {"type": "string"}, "args": {"type": "object"},
         "resume_from_run_id": {"type": "string"}}, ["name"]),
        _handler)
    return runtime
