"""s07: skill loading — catalog first, full SKILL.md on demand (learn-claude-code)."""
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


def _fn(name, desc, props, required):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": required},
    }}


class SkillLoader:
    def __init__(self, skills_dir: Path):
        self.skills_dir = Path(skills_dir)
        self.skills: Dict[str, Dict[str, str]] = {}
        self.scan()

    @staticmethod
    def _parse_frontmatter(text: str):
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

    def scan(self):
        self.skills.clear()
        if not self.skills_dir.exists():
            return
        root = self.skills_dir.resolve()
        for manifest in sorted(self.skills_dir.glob("*/SKILL.md")):
            try:
                if not manifest.is_file() or not manifest.resolve().is_relative_to(root):
                    continue
                content = manifest.read_text(encoding="utf-8")
                meta, body = self._parse_frontmatter(content)
                name = str(meta.get("name") or manifest.parent.name).strip()
                desc = str(meta.get("description") or "").strip()
                if not desc and body:
                    desc = " ".join(body.splitlines()[0].lstrip("# ").split())
                self.skills[name] = {"name": name, "description": desc, "content": content}
            except Exception:
                continue

    def catalog(self) -> str:
        if not self.skills:
            return "(no skills found)"
        return "\n".join(f"- {s['name']}: {s['description']}" for s in self.skills.values())

    def load(self, name: str) -> str:
        skill = self.skills.get(str(name).strip())
        if skill:
            return skill["content"]
        available = ", ".join(self.skills) or "none"
        return f"Error: unknown skill '{name}'. Available: {available}"


def register(registry, skills_dir: Optional[Path] = None) -> SkillLoader:
    loader = SkillLoader(skills_dir or Path("skills"))
    registry.register_tool(
        "load_skill",
        _fn("load_skill", "Load the full SKILL.md content by skill name.",
            {"name": {"type": "string"}}, ["name"]),
        loader.load,
    )
    return loader
