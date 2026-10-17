"""services / workspaces."""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException

from app.api.schemas import CreateWorkspaceRequest
from app.config import CONFIG_FILE, WORKSPACES_ROOT, load_config


def validate_workspace_name(name: str) -> str:
    """A workspace is addressed by NAME only (never an absolute/escaping path)."""
    name = (name or "").strip()
    if (
        not name
        or name in (".", "..")
        or name.startswith(".")
        or "/" in name
        or "\\" in name
        or "\x00" in name
    ):
        raise HTTPException(status_code=400, detail="Invalid workspace name.")
    return name


def resolve_workspace(name: str) -> Path:
    """Resolve a workspace name under WORKSPACES_ROOT (absolute paths rejected)."""
    name = validate_workspace_name(name or "default")
    root = WORKSPACES_ROOT.resolve()
    path = (root / name).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="Workspace escapes the workspaces root.")
    return path


async def get_config():
    return {"config": load_config(), "config_path": str(Path(CONFIG_FILE).resolve())}


async def list_workspaces():
    WORKSPACES_ROOT.mkdir(parents=True, exist_ok=True)
    names = sorted(d.name for d in WORKSPACES_ROOT.iterdir() if d.is_dir())
    if not names:
        (WORKSPACES_ROOT / "default").mkdir(parents=True, exist_ok=True)
        names = ["default"]
    return {"workspaces": names, "root": str(WORKSPACES_ROOT)}


async def create_workspace(payload: CreateWorkspaceRequest):
    name = validate_workspace_name(payload.name)
    (WORKSPACES_ROOT / name).mkdir(parents=True, exist_ok=True)
    return {"workspace": name, "path": str(WORKSPACES_ROOT / name)}
