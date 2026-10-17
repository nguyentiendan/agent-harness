"""Focused tests for DELETE /api/sessions/{session_id}.

Run from the backend directory with the project environment:

    python -m unittest test_delete_session -v

The test points the session store at a temporary directory before importing
api_server, so it never touches real session data.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

_TMP = tempfile.TemporaryDirectory()
_ROOT = Path(_TMP.name)
os.environ["HARNESS_DATA_DIR"] = str(_ROOT / "data")
os.environ["WORKSPACES_ROOT"] = str(_ROOT / "workspaces")
os.environ.setdefault("HARNESS_CONFIG", str(_ROOT / "missing-config.yaml"))

from fastapi.testclient import TestClient  # noqa: E402

import api_server  # noqa: E402


def _seed(session_id: str, status: str = "completed", active: bool = False) -> None:
    api_server.sessions[:] = [
        s for s in api_server.sessions if s["session_id"] != session_id
    ]
    api_server.sessions.append(
        {
            "session_id": session_id,
            "goal": "goal for " + session_id,
            "status": status,
            "workspace": "default",
            "created_at": 1.0,
            "updated_at": 2.0,
        }
    )
    api_server._save_sessions(api_server.sessions)
    api_server.SESSION_EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    (api_server.SESSION_EVENTS_DIR / f"{session_id}.json").write_text(
        json.dumps([{"type": "assistant", "text": "hi"}]), encoding="utf-8"
    )
    if active:
        api_server.active_sessions[session_id] = {
            "status": status,
            "events": [],
            "workspace_path": str(_ROOT / "workspaces" / "default"),
            "goal": "goal for " + session_id,
        }


class DeleteSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(api_server.app)

    def tearDown(self) -> None:
        api_server.sessions.clear()
        api_server.active_sessions.clear()
        api_server._save_sessions(api_server.sessions)

    def test_delete_removes_record_and_events_file(self) -> None:
        _seed("session_done", active=True)
        res = self.client.delete("/api/sessions/session_done")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["session_id"], "session_done")

        listed = self.client.get("/api/sessions").json()["sessions"]
        self.assertNotIn("session_done", [s["session_id"] for s in listed])
        self.assertFalse(
            (api_server.SESSION_EVENTS_DIR / "session_done.json").exists()
        )
        self.assertEqual(self.client.get("/api/sessions/session_done").status_code, 404)

    def test_delete_unknown_session_returns_404(self) -> None:
        res = self.client.delete("/api/sessions/session_missing")
        self.assertEqual(res.status_code, 404)

    def test_delete_running_session_returns_409_and_keeps_it(self) -> None:
        _seed("session_running", status="running", active=True)
        res = self.client.delete("/api/sessions/session_running")
        self.assertEqual(res.status_code, 409)

        listed = self.client.get("/api/sessions").json()["sessions"]
        self.assertIn("session_running", [s["session_id"] for s in listed])
        self.assertTrue(
            (api_server.SESSION_EVENTS_DIR / "session_running.json").exists()
        )


if __name__ == "__main__":
    unittest.main()
