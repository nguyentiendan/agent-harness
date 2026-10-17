"""services / files."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from app.config import WORKSPACE_FILES
from app.services.sessions import session_details


async def get_workspace_files(session_id: str):
    ws_path = Path(session_details(session_id)["workspace_path"])
    files: Dict[str, Optional[str]] = {}
    for rel in WORKSPACE_FILES:
        p = ws_path / rel
        files[rel] = p.read_text(encoding="utf-8") if p.exists() else None
    return {"session_id": session_id, "files": files}


async def list_workspace(session_id: str):
    ws_path = Path(session_details(session_id)["workspace_path"])
    entries: List[Dict[str, Any]] = []
    if ws_path.exists():
        for p in sorted(ws_path.rglob("*")):
            if p.is_file():
                rel = str(p.relative_to(ws_path))
                parts = Path(rel).parts
                if any(part.startswith(".") for part in parts) or "worktrees" in parts:
                    continue
                if rel in {"state.json", "progress.md", "rules.md", "todos.json"}:
                    continue
                entries.append({"path": rel, "size": p.stat().st_size})
    return {"session_id": session_id, "files": entries[:500]}


async def read_workspace_file(session_id: str, path: str):
    """Read a single workspace file (path is relative to the workspace root)."""
    ws_path = Path(session_details(session_id)["workspace_path"]).resolve()
    p = (ws_path / path).resolve()
    try:
        p.relative_to(ws_path)
    except ValueError:
        raise HTTPException(status_code=400, detail="Path outside workspace.")
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="File not found.")
    return {"path": path, "content": p.read_text(encoding="utf-8", errors="replace")}
