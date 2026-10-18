import os
import tempfile
from pathlib import Path

_TMP = tempfile.TemporaryDirectory()
_ROOT = Path(_TMP.name)
os.environ["HARNESS_DATA_DIR"] = str(_ROOT / "data")
os.environ["WORKSPACES_ROOT"] = str(_ROOT / "workspaces")
os.environ.setdefault("HARNESS_CONFIG", str(_ROOT / "missing-config.yaml"))

os.environ["PLATFORM_DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "sqlite:///" + str(_ROOT / "platform-test.db")
)
if "test" not in os.environ["PLATFORM_DATABASE_URL"].rsplit("/", 1)[-1]:
    raise RuntimeError(
        "Refusing to run destructive DB tests against a non-test database"
    )
os.environ["APP_ORIGIN"] = "http://testserver"
os.environ["SANDBOX_ENABLED"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from fastapi import HTTPException
from app.main import app
from app import config
from app.storage import sessions as store
from app.services import sessions as sessions_service, runtime, runs, workspaces
from app.api import schemas

from app.platform.db import Base, engine

Base.metadata.create_all(engine)
