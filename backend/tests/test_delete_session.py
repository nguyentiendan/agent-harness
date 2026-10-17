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

from support import app, config, store, sessions_service, runtime, runs, workspaces, schemas, HTTPException, _ROOT
from fastapi.testclient import TestClient

def _seed(session_id: str, status: str = "completed", active: bool = False) -> None:
    store.sessions[:] = [
        s for s in store.sessions if s["session_id"] != session_id
    ]
    store.sessions.append(
        {
            "session_id": session_id,
            "goal": "goal for " + session_id,
            "status": status,
            "workspace": "default",
            "created_at": 1.0,
            "updated_at": 2.0,
        }
    )
    store._save_sessions(store.sessions)
    config.SESSION_EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    (config.SESSION_EVENTS_DIR / f"{session_id}.json").write_text(
        json.dumps([{"type": "assistant", "text": "hi"}]), encoding="utf-8"
    )
    if active:
        runtime.active_sessions[session_id] = {
            "status": status,
            "events": [],
            "workspace_path": str(_ROOT / "workspaces" / "default"),
            "goal": "goal for " + session_id,
        }


class DeleteSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def tearDown(self) -> None:
        store.sessions.clear()
        runtime.active_sessions.clear()
        store._save_sessions(store.sessions)

    def test_delete_removes_record_and_events_file(self) -> None:
        _seed("session_done", active=True)
        res = self.client.delete("/api/sessions/session_done")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["session_id"], "session_done")

        listed = self.client.get("/api/sessions").json()["sessions"]
        self.assertNotIn("session_done", [s["session_id"] for s in listed])
        self.assertFalse(
            (config.SESSION_EVENTS_DIR / "session_done.json").exists()
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
            (config.SESSION_EVENTS_DIR / "session_running.json").exists()
        )


if __name__ == "__main__":
    unittest.main()
