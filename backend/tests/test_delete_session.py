"""Authenticated deletion: trash, authorization, and busy-state protection."""

import unittest
import support
from fastapi.testclient import TestClient
from app.main import app
from app.platform.db import DB, Base, engine, Chat
from app.platform.identity import create_user


class DeleteSessionTests(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        with DB.begin() as db:
            create_user(db, "deleteuser", "Testing password 123!", "user", False)
        self.client = TestClient(app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        r = self.client.post(
            "/api/auth/login",
            json={"username": "deleteuser", "password": "Testing password 123!"},
            headers={"Origin": "http://testserver"},
        )
        self.client.headers.update(
            {"Origin": "http://testserver", "X-CSRF-Token": r.json()["csrf_token"]}
        )
        self.sid = self.client.post("/api/sessions", json={}).json()["session_id"]

    def test_delete_hides_chat_and_preserves_trash_record(self):
        self.assertEqual(
            self.client.delete("/api/sessions/" + self.sid).status_code, 200
        )
        self.assertEqual(self.client.get("/api/sessions").json()["sessions"], [])
        self.assertEqual(self.client.get("/api/sessions/" + self.sid).status_code, 404)
        with DB() as db:
            self.assertIsNotNone(db.get(Chat, self.sid).deleted_at)

    def test_delete_unknown_returns_404(self):
        self.assertEqual(self.client.delete("/api/sessions/missing").status_code, 404)

    def test_delete_running_returns_409(self):
        with DB.begin() as db:
            db.get(Chat, self.sid).status = "running"
        self.assertEqual(
            self.client.delete("/api/sessions/" + self.sid).status_code, 409
        )
        self.assertEqual(len(self.client.get("/api/sessions").json()["sessions"]), 1)
