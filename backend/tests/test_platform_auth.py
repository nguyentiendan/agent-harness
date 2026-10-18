"""HTTP security contract; fake only the external model call."""

import support
import unittest
from fastapi.testclient import TestClient
from app.main import app


class AuthBoundaryTests(unittest.TestCase):
    def test_anonymous_cannot_list_sessions(self):
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/sessions").status_code, 401)

    def test_workspace_api_is_removed(self):
        with TestClient(app) as client:
            self.assertEqual(
                client.post("/api/workspaces", json={"name": "oops"}).status_code, 404
            )


class PlatformTests(unittest.TestCase):
    def setUp(self):
        import asyncio
        from app.platform import chat
        # Each TestClient owns a fresh event loop, unlike the single production worker.
        chat.admission = asyncio.Lock()
        from app.platform.db import Base, engine, DB
        from app.platform.identity import create_user

        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        with DB.begin() as db:
            self.admin = create_user(
                db, "admin", "Admin password 123!", "admin", False
            )["id"]
            self.a = create_user(db, "alice", "Alice password 123!", "user", False)[
                "id"
            ]
            self.b = create_user(db, "bob", "Bob password 123!", "user", False)["id"]
        self.client = TestClient(app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def login(self, username="alice", password="Alice password 123!"):
        r = self.client.post(
            "/api/auth/login",
            json={"username": username, "password": password},
            headers={"Origin": "http://testserver"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.client.headers.update(
            {"Origin": "http://testserver", "X-CSRF-Token": r.json()["csrf_token"]}
        )
        return r

    def new_chat(self):
        r = self.client.post("/api/sessions", json={})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["session_id"]

    def test_cookie_csrf_logout_and_hashes(self):
        r = self.login()
        self.assertIn("HttpOnly", r.headers["set-cookie"])
        self.assertNotIn("password_hash", r.text)
        self.assertEqual(
            self.client.post(
                "/api/sessions", json={}, headers={"X-CSRF-Token": "bad"}
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                "/api/sessions", json={}, headers={"Origin": "https://evil.example"}
            ).status_code,
            403,
        )
        self.assertEqual(self.client.post("/api/auth/logout").status_code, 200)
        self.assertEqual(self.client.get("/api/sessions").status_code, 401)

    def test_ownership_matrix(self):
        self.login()
        sid = self.new_chat()
        self.login("bob", "Bob password 123!")
        self.assertEqual(self.client.get("/api/sessions").json()["sessions"], [])
        for path in [
            f"/api/sessions/{sid}",
            f"/api/harness/status/{sid}",
            f"/api/harness/list/{sid}",
            f"/api/harness/read/{sid}?path=x",
            f"/api/harness/files/{sid}",
            f"/api/harness/stream/{sid}",
            f"/api/harness/approvals/{sid}",
        ]:
            self.assertEqual(self.client.get(path).status_code, 404, path)
        self.assertEqual(self.client.delete(f"/api/sessions/{sid}").status_code, 404)
        self.assertEqual(self.client.post(f"/api/harness/stop/{sid}").status_code, 404)
        self.assertEqual(
            self.client.post(
                "/api/harness/start",
                json={"session_id": sid, "goal": "test", "request_id": "one"},
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                "/api/command", json={"session_id": sid, "command": "status"}
            ).status_code,
            404,
        )
        self.assertEqual(self.client.get("/api/admin/users").status_code, 403)
        self.assertEqual(
            self.client.post("/api/sessions", json={"owner_id": self.a}).status_code,
            422,
        )
        self.login("admin", "Admin password 123!")
        self.assertEqual(
            len(self.client.get("/api/sessions?all_users=true").json()["sessions"]), 1
        )
        self.assertEqual(self.client.get(f"/api/sessions/{sid}").status_code, 200)
        self.assertEqual(
            self.client.post(
                "/api/harness/start",
                json={"session_id": sid, "goal": "test", "request_id": "one"},
            ).status_code,
            403,
        )

    def test_user_admin_lifecycle(self):
        self.login("admin", "Admin password 123!")
        r = self.client.post(
            "/api/admin/users",
            json={
                "username": " NewUser ",
                "password": "Temporary pass 123!",
                "role": "user",
            },
        )
        self.assertEqual(r.status_code, 200, r.text)
        uid = r.json()["id"]
        self.assertEqual(
            self.client.post(
                "/api/admin/users",
                json={
                    "username": "newuser",
                    "password": "Temporary pass 123!",
                    "role": "user",
                },
            ).status_code,
            409,
        )
        self.assertEqual(
            self.client.patch(
                f"/api/admin/users/{self.admin}", json={"active": False}
            ).status_code,
            409,
        )
        self.login("newuser", "Temporary pass 123!")
        self.assertEqual(self.client.get("/api/sessions").status_code, 403)
        self.assertEqual(
            self.client.post(
                "/api/auth/password",
                json={
                    "current_password": "Temporary pass 123!",
                    "new_password": "Changed password 123!",
                },
            ).status_code,
            200,
        )
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)
        self.login("newuser", "Changed password 123!")
        stolen = self.client.cookies.get("harness_session")
        self.login("admin", "Admin password 123!")
        self.assertEqual(
            self.client.patch(
                f"/api/admin/users/{uid}", json={"active": False}
            ).status_code,
            200,
        )
        with TestClient(app) as other:
            other.cookies.set("harness_session", stolen)
            self.assertEqual(other.get("/api/auth/me").status_code, 401)

    def test_text_chat_continuity_idempotency_and_no_tools(self):
        from unittest.mock import AsyncMock, patch
        from types import SimpleNamespace

        response = SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content="Hello", tool_calls=[]))
            ]
        )
        self.login()
        sid = self.new_chat()
        with patch(
            "app.platform.chat.LiteLLMClientWrapper.complete",
            new=AsyncMock(return_value=response),
        ) as model:
            payload = {"session_id": sid, "goal": "first", "request_id": "r1"}
            r = self.client.post("/api/harness/start", json=payload)
            self.assertEqual(r.status_code, 200, r.text)
            import time

            for _ in range(100):
                if (
                    self.client.get(f"/api/harness/status/{sid}").json()["status"]
                    != "running"
                ):
                    break
                time.sleep(0.01)
            self.assertEqual(
                self.client.post("/api/harness/start", json=payload).status_code, 200
            )
            self.assertEqual(
                self.client.post(
                    "/api/harness/start", json={**payload, "goal": "different"}
                ).status_code,
                409,
            )
            r = self.client.post(
                "/api/harness/start",
                json={"session_id": sid, "goal": "second", "request_id": "r2"},
            )
            self.assertEqual(r.status_code, 200, r.text)
            for _ in range(100):
                if (
                    self.client.get(f"/api/harness/status/{sid}").json()["status"]
                    != "running"
                ):
                    break
                time.sleep(0.01)
            self.assertEqual(model.await_count, 2)
            self.assertNotIn("tools", model.call_args.kwargs)
            self.assertTrue(
                any(m.get("content") == "first" for m in model.call_args.args[0])
            )
        rows = self.client.get("/api/sessions").json()["sessions"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["goal"], "first")
        events = self.client.get(f"/api/sessions/{sid}").json()["events"]
        self.assertEqual(
            [e["text"] for e in events if e["type"] == "user"], ["first", "second"]
        )
        self.assertEqual(
            self.client.get(f"/api/harness/read/{sid}?path=/etc/passwd").status_code,
            409,
        )
        self.assertEqual(
            self.client.post(
                "/api/harness/start",
                json={
                    "session_id": sid,
                    "goal": "x",
                    "request_id": "x",
                    "workspace_path": "../",
                },
            ).status_code,
            422,
        )

    def test_login_origin_and_throttle(self):
        self.assertEqual(
            self.client.post(
                "/api/auth/login",
                json={"username": "alice", "password": "bad"},
                headers={"Origin": "https://evil.example"},
            ).status_code,
            403,
        )
        for _ in range(5):
            self.assertEqual(
                self.client.post(
                    "/api/auth/login",
                    json={"username": "alice", "password": "bad"},
                    headers={"Origin": "http://testserver"},
                ).status_code,
                401,
            )
        self.assertEqual(
            self.client.post(
                "/api/auth/login",
                json={"username": "alice", "password": "bad"},
                headers={"Origin": "http://testserver"},
            ).status_code,
            429,
        )

    def test_expiry_reset_and_stable_csrf_across_tabs(self):
        from app.platform.db import DB, LoginSession
        from app.platform.identity import digest
        import time

        self.login()
        first = self.client.headers["X-CSRF-Token"]
        self.assertEqual(self.client.get("/api/auth/me").json()["csrf_token"], first)
        token = self.client.cookies.get("harness_session")
        with DB.begin() as db:
            db.get(LoginSession, digest(token)).expires_at = time.time() - 1
        self.assertEqual(self.client.get("/api/sessions").status_code, 401)
        self.login()
        token = self.client.cookies.get("harness_session")
        self.login("admin", "Admin password 123!")
        r = self.client.post(
            f"/api/admin/users/{self.a}/reset-password",
            json={"password": "Reset password 123!"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        with TestClient(app) as other:
            other.cookies.set("harness_session", token)
            self.assertEqual(other.get("/api/auth/me").status_code, 401)
        self.login("alice", "Reset password 123!")
        self.assertTrue(
            self.client.get("/api/auth/me").json()["user"]["must_change_password"]
        )

    def test_hash_storage_and_validation_never_echo_password(self):
        from app.platform.db import DB, User, LoginSession
        from sqlalchemy import select

        self.login()
        with DB() as db:
            user = db.get(User, self.a)
            self.assertTrue(user.password_hash.startswith("$argon2id$"))
            session = db.scalar(
                select(LoginSession).where(LoginSession.user_id == self.a)
            )
            self.assertNotEqual(
                session.token_hash, self.client.cookies.get("harness_session")
            )
        secret = "sensitive-too-long-" * 20
        r = self.client.post(
            "/api/auth/login", json={"username": "alice", "password": secret}
        )
        self.assertEqual(r.status_code, 422)
        self.assertNotIn(secret, r.text)

    def test_run_busy_disable_and_restart_recovery(self):
        from unittest.mock import patch
        import asyncio
        from app.platform.db import DB, Chat, Run
        from app.platform import chat
        from app.platform.identity import authenticate

        async def slow(*args, **kwargs):
            await asyncio.sleep(30)

        self.login()
        sid = self.new_chat()
        token = self.client.cookies.get("harness_session")
        with patch("app.platform.chat.LiteLLMClientWrapper.complete", new=slow):
            r = self.client.post(
                "/api/harness/start",
                json={"session_id": sid, "goal": "wait", "request_id": "busy"},
            )
            self.assertEqual(r.status_code, 200)
            self.assertEqual(
                self.client.post(
                    "/api/harness/start",
                    json={"session_id": sid, "goal": "another", "request_id": "busy2"},
                ).status_code,
                409,
            )
            self.assertEqual(
                self.client.delete("/api/sessions/" + sid).status_code, 409
            )
            self.login("admin", "Admin password 123!")
            self.assertEqual(
                self.client.patch(
                    "/api/admin/users/" + self.a, json={"active": False}
                ).status_code,
                200,
            )
            self.assertEqual(
                self.client.get("/api/harness/status/" + sid).json()["status"],
                "stopped",
            )
            with DB() as db:
                from fastapi import HTTPException

                with self.assertRaises(HTTPException):
                    authenticate(db, token)
        with DB.begin() as db:
            db.get(Chat, sid).status = "running"
            db.get(Run, r.json()["run_id"]).status = "running"
        chat.reconcile()
        self.assertEqual(
            self.client.get("/api/harness/status/" + sid).json()["status"], "failed"
        )
        self.assertEqual(
            self.client.get("/api/sessions/" + sid).json()["events"][0]["text"], "wait"
        )

    def test_trash_purge_retention_and_idempotency(self):
        from app.cli.cleanup import purge
        from app.platform.db import DB, Chat
        import time

        self.login()
        sid = self.new_chat()
        self.client.delete("/api/sessions/" + sid)
        self.assertEqual(purge(), 0)
        self.assertEqual(purge(time.time() + 8 * 86400), 1)
        self.assertEqual(purge(time.time() + 8 * 86400), 0)
        with DB() as db:
            self.assertIsNone(db.get(Chat, sid))

    def test_pending_start_rechecks_revoked_session(self):
        from app.platform import chat, auth
        from app.platform.db import DB
        from app.platform.identity import revoke
        from concurrent.futures import ThreadPoolExecutor
        from unittest.mock import patch
        import threading

        self.login()
        sid = self.new_chat()
        self.client.portal.call(chat.admission.acquire)
        seen = threading.Event()
        original = auth.authenticate

        def observed(*args, **kwargs):
            result = original(*args, **kwargs)
            seen.set()
            return result

        try:
            with (
                patch("app.platform.auth.authenticate", side_effect=observed),
                ThreadPoolExecutor(max_workers=1) as pool,
            ):
                future = pool.submit(
                    self.client.post,
                    "/api/harness/start",
                    json={
                        "session_id": sid,
                        "goal": "must not run",
                        "request_id": "race",
                    },
                )
                self.assertTrue(seen.wait(3))
                with DB.begin() as db:
                    revoke(db, self.a)
                self.client.portal.call(chat.admission.release)
                self.assertEqual(future.result(timeout=5).status_code, 401)
        finally:
            if chat.admission.locked():
                self.client.portal.call(chat.admission.release)

    def test_sse_cursor_and_revocation(self):
        from app.platform.db import DB, Run, Chat
        from app.platform import chat, routes
        from app.platform.identity import authenticate, revoke
        from starlette.requests import Request
        import asyncio

        self.login()
        sid = self.new_chat()
        token = self.client.cookies.get("harness_session")
        rid = "00000000-0000-0000-0000-000000000001"
        with DB.begin() as db:
            db.add(
                Run(
                    id=rid,
                    session_id=sid,
                    request_id="stream",
                    body_hash="x",
                    model="fake",
                    event_offset=0,
                )
            )
            db.flush()
            chat.append_event(db, sid, rid, {"type": "user", "text": "one"})
            chat.append_event(db, sid, rid, {"type": "assistant", "text": "two"})
            db.get(Chat, sid).status = "running"

        async def check():
            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}

            request = Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/api/harness/stream/" + sid,
                    "headers": [
                        (b"cookie", ("harness_session=" + token).encode()),
                        (b"last-event-id", b"1"),
                    ],
                },
                receive=receive,
            )
            with DB.begin() as db:
                user, login = authenticate(db, token)
                response = await routes.stream(
                    sid, request, after=0, ctx=(db, user, login)
                )
            iterator = response.body_iterator
            first = await anext(iterator)
            self.assertIn("id: 2", first)
            self.assertIn("two", first)
            self.assertNotIn("one", first)
            with DB.begin() as db:
                revoke(db, self.a)
            with self.assertRaises(StopAsyncIteration):
                await asyncio.wait_for(anext(iterator), 2)

        self.client.portal.call(check)

    def test_pending_admin_update_rechecks_role_and_session(self):
        from app.platform import chat, auth
        from app.platform.db import DB, User
        from app.platform.identity import revoke
        from concurrent.futures import ThreadPoolExecutor
        from unittest.mock import patch
        import threading

        self.login("admin", "Admin password 123!")
        self.client.portal.call(chat.admission.acquire)
        seen = threading.Event()
        original = auth.authenticate

        def observed(*args, **kwargs):
            result = original(*args, **kwargs)
            seen.set()
            return result

        try:
            with (
                patch("app.platform.auth.authenticate", side_effect=observed),
                ThreadPoolExecutor(max_workers=1) as pool,
            ):
                future = pool.submit(
                    self.client.patch,
                    "/api/admin/users/" + self.b,
                    json={"active": False},
                )
                self.assertTrue(seen.wait(3))
                with DB.begin() as db:
                    revoke(db, self.admin)
                self.client.portal.call(chat.admission.release)
                self.assertEqual(future.result(timeout=5).status_code, 401)
                with DB() as db:
                    self.assertTrue(db.get(User, self.b).active)
        finally:
            if chat.admission.locked():
                self.client.portal.call(chat.admission.release)
