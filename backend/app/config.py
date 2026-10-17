"""config."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict
import os

import yaml


CONFIG_FILE = os.environ.get("HARNESS_CONFIG", "config.yaml")


WORKSPACES_ROOT = Path(os.environ.get("WORKSPACES_ROOT", "./workspaces")).resolve()


DATA_DIR = Path(os.environ.get("HARNESS_DATA_DIR", "./data")).resolve()


SESSIONS_FILE = DATA_DIR / "sessions.json"


SESSION_EVENTS_DIR = DATA_DIR / "sessions"


def load_config() -> Dict[str, Any]:
    path = Path(CONFIG_FILE)
    if path.exists():
        try:
            return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as e:
            print(f"[Config] Warning: could not parse {path}: {e}")
    return {}


WORKSPACE_FILES = ["progress.md", "state.json", "rules.md"]
