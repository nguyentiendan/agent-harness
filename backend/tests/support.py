import os
import tempfile
from pathlib import Path
_TMP = tempfile.TemporaryDirectory()
_ROOT = Path(_TMP.name)
os.environ["HARNESS_DATA_DIR"] = str(_ROOT / "data")
os.environ["WORKSPACES_ROOT"] = str(_ROOT / "workspaces")
os.environ.setdefault("HARNESS_CONFIG", str(_ROOT / "missing-config.yaml"))

from fastapi.testclient import TestClient  # noqa: E402

from fastapi import HTTPException
from app.main import app
from app import config
from app.storage import sessions as store
from app.services import sessions as sessions_service, runtime, runs, workspaces
from app.api import schemas
