"""agent / workspace."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional


class WorkspaceManager:
    """Sets up the workspace directory structure and loads system rules."""

    def __init__(self, root_dir: Path, config: Optional[Dict[str, Any]] = None):
        self.root_dir = root_dir
        ws_cfg = (config or {}).get("workspace", {})
        self.auto_init_dirs = ws_cfg.get("auto_init_dirs", [])
        self.rules_file = root_dir / ws_cfg.get("default_rules_file", "rules.md")

    def init_workspace(self):
        self.root_dir.mkdir(parents=True, exist_ok=True)
        for dir_name in self.auto_init_dirs:
            (self.root_dir / dir_name).mkdir(parents=True, exist_ok=True)

        if not self.rules_file.exists():
            default_rules = (
                "# Workspace Rules\n"
                "- Write working files in the workspace root or an appropriate subdirectory.\n"
                "- Never write secrets, API keys, tokens, or credentials to any file; use environment variables instead.\n"
                "- Read a file before editing it; prefer edit_file over rewriting whole files.\n"
                "- Break multi-step work into tasks (create_task / claim_task / complete_task) and keep their status current.\n"
                "- Run the relevant tests or a verification step before marking work complete.\n"
                "- Report concrete results (exit codes, file paths, command output) so completion can be verified.\n"
                "- Do not leave dead code, debug prints, or unused files behind.\n"
            )
            self.rules_file.write_text(default_rules, encoding="utf-8")

    def get_system_prompt(self) -> str:
        rules_content = self.rules_file.read_text(encoding="utf-8") if self.rules_file.exists() else ""
        return (
            "You are an autonomous AI Agent executing long-term tasks inside a structured harness.\n"
            "Follow workspace rules strictly:\n\n"
            f"{rules_content}\n"
        )
