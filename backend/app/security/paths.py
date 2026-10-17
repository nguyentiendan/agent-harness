"""security / paths."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional
import re


ABS_PATH_RE = re.compile(r"(?<![\w.:\-])/[A-Za-z0-9._+\-/]*")


CD_RE = re.compile(r"(?:^|[;&|()\n])\s*cd\s+(?:\s*)([^\s;&|()<>]+)")


URL_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9+.\-]*://\S+")


SAFE_DEVICE_PATHS = {"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/stdin"}


DEFAULT_ALLOWED_PATH_PREFIXES = [
    "/tmp", "/var/tmp", "/usr", "/bin", "/sbin", "/lib", "/lib64",
    "/opt", "/etc/ssl", "/etc/alternatives", "/proc/self",
    "/dev/null", "/dev/stdout", "/dev/stderr", "/dev/stdin",
]


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def check_bash_paths(command: str, workspace_root: Path, allowed_prefixes: List[str]) -> Optional[str]:
    """Return a violation message when a shell command steps outside the workspace."""
    root = workspace_root.resolve()
    allowed = [str(p).rstrip("/") for p in (allowed_prefixes or [])]
    scan = URL_RE.sub(" ", command)  # ignore scheme://... URLs

    def permitted(raw: str) -> bool:
        candidate = Path(raw)
        try:
            resolved = candidate.resolve() if candidate.is_absolute() else (root / raw).resolve()
        except Exception:
            resolved = candidate
        if _within(resolved, root):
            return True
        return any(raw == a or raw.startswith(a + "/") for a in allowed)

    for match in CD_RE.finditer(scan):
        target = match.group(1).strip("'\"")
        # '-' , '~', or an unexpanded variable could land anywhere.
        if not target or target in ("-",) or target.startswith("~") or "$" in target:
            return f"'cd {target or '~'}' leaves the workspace"
        if not permitted(target):
            return f"'cd {target}' leaves the workspace"

    for match in ABS_PATH_RE.finditer(scan):
        raw = match.group(0).rstrip("/") or "/"
        if raw in SAFE_DEVICE_PATHS:
            continue
        if not permitted(raw):
            return f"path '{raw}' is outside the workspace"

    return None
