"""s09: memory — durable knowledge across sessions (learn-claude-code, adapted)."""

from pathlib import Path
from typing import Any, Dict, List
import json
import re

import yaml


MEMORY_TYPES = ("user", "feedback", "project", "reference")


def _fn(name, desc, props, required):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": required},
    }}


class MemoryStore:
    def __init__(self, memory_dir: Path):
        self.memory_dir = Path(memory_dir)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.index_file = self.memory_dir / "MEMORY.md"

    @staticmethod
    def _slug(name: str) -> str:
        return re.sub(r"[^\w]+", "-", str(name).lower()).strip("-_") or "memory"

    @staticmethod
    def _parse(text: str):
        if not text.startswith("---\n"):
            return {}, text
        parts = text.split("---", 2)
        if len(parts) < 3:
            return {}, text
        try:
            meta = yaml.safe_load(parts[1]) or {}
        except Exception:
            meta = {}
        return (meta if isinstance(meta, dict) else {}), parts[2].lstrip()

    def _records(self) -> List[Dict[str, Any]]:
        if not self.memory_dir.exists():
            return []
        out = []
        for p in sorted(self.memory_dir.glob("*.md")):
            if p.name == self.index_file.name:
                continue
            try:
                meta, body = self._parse(p.read_text(encoding="utf-8"))
                out.append({
                    "filename": p.name,
                    "name": str(meta.get("name") or p.stem),
                    "type": str(meta.get("type") or "project"),
                    "description": str(meta.get("description") or ""),
                    "body": body.strip(),
                })
            except Exception:
                continue
        return out

    def _rebuild_index(self):
        lines = [f"- [{r['name']}]({r['filename']}) - {r['description']}" for r in self._records()]
        self.index_file.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    def remember(self, name: str, mem_type: str, description: str, body: str) -> str:
        name = str(name).strip()
        description = str(description).strip()
        body = str(body).strip()
        mem_type = str(mem_type).strip() or "project"
        if not name or not description or not body:
            return "Error: name, description and body are required"
        if mem_type not in MEMORY_TYPES:
            return f"Error: type must be one of {MEMORY_TYPES}"
        doc = f"---\nname: {name}\ndescription: {description}\ntype: {mem_type}\n---\n\n{body}\n"
        (self.memory_dir / f"{self._slug(name)}.md").write_text(doc, encoding="utf-8")
        self._rebuild_index()
        return f"Remembered '{name}'"

    def recall(self, query: str = "", limit: int = 5) -> str:
        records = self._records()
        if not records:
            return "(no memories)"
        words = set(re.findall(r"[a-z0-9_]{3,}", str(query or "").lower()))
        if words:
            scored = []
            for r in records:
                text = f"{r['name']} {r['description']} {r['body']}".lower()
                scored.append((sum(w in text for w in words), r))
            scored.sort(key=lambda x: -x[0])
            chosen = [r for s, r in scored if s > 0][: int(limit)]
        else:
            chosen = records[: int(limit)]
        if not chosen:
            return "(no matching memories)"
        return json.dumps(chosen, ensure_ascii=False, indent=2)

    def list_memories(self) -> str:
        records = self._records()
        if not records:
            return "(empty)"
        return "\n".join(f"- {r['name']} ({r['type']}): {r['description']}" for r in records)

    def index_text(self) -> str:
        return self.index_file.read_text(encoding="utf-8").strip() if self.index_file.exists() else ""


def register(registry, memory_dir: Path) -> MemoryStore:
    store = MemoryStore(memory_dir)
    registry.register_tool(
        "remember",
        _fn("remember", "Save a durable memory record.", {
            "name": {"type": "string"}, "type": {"type": "string"},
            "description": {"type": "string"}, "body": {"type": "string"}},
            ["name", "description", "body"]),
        lambda **kw: store.remember(kw.get("name", ""), kw.get("type", "project"),
                                    kw.get("description", ""), kw.get("body", "")),
    )
    registry.register_tool(
        "recall",
        _fn("recall", "Recall memories matching a query.",
            {"query": {"type": "string"}, "limit": {"type": "integer"}}, []),
        lambda **kw: store.recall(kw.get("query", ""), int(kw.get("limit", 5))),
    )
    registry.register_tool(
        "list_memories",
        _fn("list_memories", "List all stored memories.", {}, []),
        lambda **kw: store.list_memories(),
    )
    return store
