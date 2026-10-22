import support
import unittest
import test_platform_auth as auth_tests


class ProjectTests(unittest.TestCase):
    setUp = auth_tests.PlatformTests.setUp
    login = auth_tests.PlatformTests.login

    def project(self):
        r = self.client.post(
            "/api/projects",
            json={"name": "Marketing", "instructions": "Use Vietnamese."},
        )
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["id"]

    def test_project_ownership_and_no_impersonation(self):
        self.login()
        pid = self.project()
        r = self.client.post("/api/sessions", json={"project_id": pid})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["project_id"], pid)
        self.login("bob", "Bob password 123!")
        self.assertEqual(self.client.get("/api/projects").json()["projects"], [])
        self.assertEqual(
            self.client.post("/api/sessions", json={"project_id": pid}).status_code, 404
        )
        self.assertEqual(
            self.client.patch(
                "/api/projects/" + pid, json={"name": "stolen"}
            ).status_code,
            404,
        )
        self.login("admin", "Admin password 123!")
        self.assertEqual(
            len(self.client.get("/api/projects?all_users=true").json()["projects"]), 1
        )
        self.assertEqual(
            self.client.post("/api/sessions", json={"project_id": pid}).status_code, 403
        )

    def test_shared_scope_separate_history_and_chat_trash(self):
        self.login()
        pid = self.project()
        from app.platform.db import DB, Chat
        from app.execution.lifecycle import scope_for_chat
        from app.cli.cleanup import purge

        ids = [
            self.client.post("/api/sessions", json={"project_id": pid}).json()[
                "session_id"
            ]
            for _ in range(2)
        ]
        with DB.begin() as db:
            self.assertEqual(scope_for_chat(db, db.get(Chat, ids[0])).id, pid)
            self.assertEqual(scope_for_chat(db, db.get(Chat, ids[1])).id, pid)
        self.assertEqual(
            self.client.patch(
                "/api/sessions/" + ids[0], json={"project_id": None}
            ).status_code,
            405,
        )
        self.assertEqual(self.client.delete("/api/sessions/" + ids[0]).status_code, 200)
        with DB.begin() as db:
            db.get(Chat, ids[0]).deleted_at = 1
        self.assertEqual(purge(), 1)
        self.assertEqual(self.client.get("/api/projects/" + pid).status_code, 200)
        self.assertEqual(
            self.client.get("/api/sessions/" + ids[1]).json()["events"], []
        )
        self.assertEqual(self.client.delete("/api/projects/" + pid).status_code, 200)
        self.assertEqual(self.client.get("/api/sessions/" + ids[1]).status_code, 404)

    def test_upload_limit_and_owner_boundary(self):
        self.login()
        pid = self.project()
        r = self.client.post(
            f"/api/projects/{pid}/files/upload?path=huge.bin",
            content=(b"x" * 65536 for _ in range(401)),
        )
        self.assertEqual(r.status_code, 413, r.text)
        from app.execution.lifecycle import transfers

        self.assertEqual(transfers, set())
        self.login("bob", "Bob password 123!")
        for method, path in [
            ("post", "files/upload"),
            ("get", "files/download"),
            ("delete", "files"),
            ("get", "read"),
        ]:
            self.assertEqual(
                getattr(self.client, method)(
                    f"/api/projects/{pid}/{path}?path=a"
                ).status_code,
                404,
            )

    def test_busy_project_blocks_second_chat_and_mutations(self):
        import asyncio
        from unittest.mock import patch
        from app.platform import chat

        async def wait(rid):
            await asyncio.sleep(60)

        self.login()
        pid = self.project()
        ids = [
            self.client.post("/api/sessions", json={"project_id": pid}).json()[
                "session_id"
            ]
            for _ in range(2)
        ]
        with patch.object(chat, "execute", side_effect=wait):
            r = self.client.post(
                "/api/harness/start",
                json={"session_id": ids[0], "request_id": "first", "goal": "test"},
            )
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(
                self.client.post(
                    "/api/harness/start",
                    json={"session_id": ids[1], "request_id": "second", "goal": "test"},
                ).status_code,
                409,
            )
            self.assertEqual(
                self.client.delete("/api/projects/" + pid).status_code, 409
            )
            self.assertEqual(
                self.client.delete("/api/sessions/" + ids[1]).status_code, 409
            )
            self.assertEqual(
                self.client.post(
                    f"/api/projects/{pid}/files/upload?path=x", content=b"x"
                ).status_code,
                409,
            )
            self.client.post("/api/harness/stop/" + ids[0])

    def test_project_instructions_do_not_share_other_chat_history(self):
        from unittest.mock import patch, AsyncMock
        from types import SimpleNamespace
        from app.platform.db import DB, Run
        from app.platform import chat
        import asyncio, uuid

        self.login()
        pid = self.project()
        ids = [
            self.client.post("/api/sessions", json={"project_id": pid}).json()[
                "session_id"
            ]
            for _ in range(2)
        ]
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="answer", tool_calls=[])
                )
            ]
        )
        for sid in ids:
            rid = str(uuid.uuid4())
            with DB.begin() as db:
                db.add(
                    Run(
                        id=rid,
                        session_id=sid,
                        request_id=rid,
                        body_hash="x",
                        model="fake",
                        event_offset=0,
                    )
                )
                db.flush()
                chat.append_event(
                    db, sid, rid, {"type": "user", "text": "private-" + sid}
                )
            with patch(
                "app.platform.chat.LiteLLMClientWrapper.complete",
                new=AsyncMock(return_value=response),
            ) as complete:
                asyncio.run(chat.execute(rid))
                text = str(complete.call_args.args[0])
                self.assertIn("Use Vietnamese.", text)
                self.assertIn("private-" + sid, text)
                self.assertNotIn("private-" + next(x for x in ids if x != sid), text)

    def test_project_trash_waits_for_expiry_and_runner(self):
        from app.platform.db import DB, Project, ExecutionScope, Chat
        from app.cli.cleanup import purge
        from unittest.mock import patch, MagicMock

        self.login()
        pid = self.project()
        sid = self.client.post("/api/sessions", json={"project_id": pid}).json()[
            "session_id"
        ]
        with DB.begin() as db:
            db.get(ExecutionScope, pid).has_files = True
        self.client.delete("/api/projects/" + pid)
        self.assertEqual(purge(), 0)
        with DB.begin() as db:
            db.get(Project, pid).deleted_at = 1
            db.get(Chat, sid).deleted_at = 1
        runner = MagicMock()
        runner.purge.side_effect = RuntimeError("offline")
        with patch("app.execution.client.RunnerClient", return_value=runner):
            self.assertEqual(purge(), 0)
            with DB() as db:
                self.assertIsNotNone(db.get(Project, pid))
            runner.purge.side_effect = None
            self.assertEqual(purge(), 1)
            self.assertEqual(purge(), 0)
        with DB() as db:
            self.assertIsNone(db.get(Project, pid))

    def test_small_upload_releases_scope_and_admin_update(self):
        import httpx
        from unittest.mock import patch
        from app.execution.lifecycle import transfers

        self.login()
        pid = self.project()
        received = []

        def transport(request):
            received.append(request.content)
            return httpx.Response(200, json={"bytes": 5, "path": "a.txt"})

        def connection():
            return httpx.AsyncClient(
                transport=httpx.MockTransport(transport), base_url="http://runner"
            )

        with (
            patch("app.platform.files.connection", side_effect=connection),
            patch("app.execution.client.RunnerClient.configure"),
        ):
            r = self.client.post(
                f"/api/projects/{pid}/files/upload?path=a.txt", content=b"hello"
            )
            self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(received, [b"hello"])
        self.assertEqual(transfers, set())
        self.login("admin", "Admin password 123!")
        r = self.client.patch("/api/projects/" + pid, json={"name": "Updated"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["name"], "Updated")
