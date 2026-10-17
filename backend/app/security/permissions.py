"""security / permissions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
import fnmatch
import re

from app.security.paths import DEFAULT_ALLOWED_PATH_PREFIXES, check_bash_paths


DENY_LIST = ["rm -rf /", "sudo", "shutdown", "reboot", "mkfs", "dd if="]


DESTRUCTIVE_COMMAND_WORD = re.compile(
    r"(?i)(?:^|[;&|()\n])\s*(?:rm|del)(?=\s|$|[;&|()])"
)


DESTRUCTIVE_KEYWORDS = ["rm ", "> /etc/", "chmod 777"]


FILE_TOOLS = {"read_file", "write_file", "edit_file", "list_dir"}


def contains_destructive_command(command: str) -> bool:
    return bool(DESTRUCTIVE_COMMAND_WORD.search(command))


class PermissionDenied(Exception):
    """Raised when a tool call is blocked by the permission pipeline."""


@dataclass
class ApprovalRequired:
    """Sentinel returned by the permission pipeline when user approval is needed."""
    reason: str


class PermissionManager:
    """
    Gates applied before tool execution:
      1. hard deny list (bash) — always blocked
      2. bash path escape (cd / absolute paths outside the workspace) — always blocked
      3. destructive command (bash) — blocked unless approval_mode == auto_approve
      4. file path outside workspace / matching deny patterns — always blocked
    """

    def __init__(self, workspace_root: Path, approval_mode: str = "auto_deny",
                 deny_patterns: Optional[List[str]] = None,
                 restrict_paths: bool = True,
                 allowed_path_prefixes: Optional[List[str]] = None):
        self.workspace_root = workspace_root.resolve()
        self.approval_mode = approval_mode
        self.deny_patterns = deny_patterns or []
        self.restrict_paths = restrict_paths
        self.allowed_path_prefixes = (
            list(allowed_path_prefixes) if allowed_path_prefixes is not None
            else list(DEFAULT_ALLOWED_PATH_PREFIXES)
        )

    def _safe_path(self, path: str) -> Path:
        p = (self.workspace_root / path).resolve()
        try:
            p.relative_to(self.workspace_root)
        except ValueError:
            raise PermissionDenied(f"path escapes the workspace: {path}")
        return p

    def check(self, tool_name: str, args: Dict[str, Any]) -> Any:
        """Return None (allow), a denial reason string, or ApprovalRequired."""
        if tool_name == "bash":
            command = str(args.get("command", ""))
            for pattern in DENY_LIST:
                if pattern in command:
                    return f"Permission denied by deny list: '{pattern}'"
            if self.restrict_paths:
                violation = check_bash_paths(command, self.workspace_root, self.allowed_path_prefixes)
                if violation:
                    return f"Permission denied: {violation}"
            if contains_destructive_command(command) or any(k in command for k in DESTRUCTIVE_KEYWORDS):
                if self.approval_mode == "auto_approve":
                    return None
                if self.approval_mode == "ask":
                    return ApprovalRequired(reason="destructive command requires approval")
                return "Permission denied: destructive command requires approval"
        elif tool_name in FILE_TOOLS:
            path = str(args.get("path", ""))
            try:
                self._safe_path(path)
            except PermissionDenied as e:
                return f"Permission denied: {e}"
            for pattern in self.deny_patterns:
                if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(Path(path).name, pattern):
                    return f"Permission denied: path matches deny pattern '{pattern}'"
        return None
