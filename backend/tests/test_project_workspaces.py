import support
import unittest
from unittest.mock import patch
import test_platform_auth as auth_tests
from app.execution.client import RunnerUnavailable
from app.execution.lifecycle import transfers


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        auth_tests.PlatformTests.setUp(self)
        cleanup = patch(
            "app.execution.client.RunnerClient.release_validation",
            return_value={"removed": True},
        )
        cleanup.start()
        self.addCleanup(cleanup.stop)

    login = auth_tests.PlatformTests.login

    def create(self):
        return self.client.post("/api/projects", json={"name": "Local"}).json()["id"]

    def put(self, pid, **changes):
        return self.client.put(
            f"/api/projects/{pid}/workspace",
            json=dict(
                mode="local",
                local_path="/host/path with spaces",
                expected_revision=0,
                **changes,
            ),
        )

    def test_connect_disconnect_and_stale_revision(self):
        self.login()
        pid = self.create()
        with patch(
            "app.execution.client.RunnerClient.validate_workspace",
            return_value={"ready": True},
        ):
            r = self.put(pid)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["workspace"]["revision"], 1)
            self.assertEqual(self.put(pid).status_code, 409)
            r = self.client.put(
                f"/api/projects/{pid}/workspace",
                json={"mode": "managed", "expected_revision": 1},
            )
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(
                r.json()["workspace"], dict(mode="managed", local_path=None, revision=2)
            )

    def test_failed_preflight_preserves_source(self):
        self.login()
        pid = self.create()
        with patch(
            "app.execution.client.RunnerClient.validate_workspace",
            side_effect=RunnerUnavailable("missing"),
        ):
            self.assertEqual(self.put(pid).status_code, 503)
        self.assertEqual(
            self.client.get(f"/api/projects/{pid}").json()["workspace"]["revision"], 0
        )
        self.assertNotIn(pid, transfers)

    def test_authorization_busy_and_bad_path(self):
        self.login()
        pid = self.create()
        transfers.add(pid)
        try:
            self.assertEqual(self.put(pid).status_code, 409)
        finally:
            transfers.discard(pid)
        self.assertEqual(
            self.client.put(
                f"/api/projects/{pid}/workspace",
                json={
                    "mode": "local",
                    "local_path": "relative",
                    "expected_revision": 0,
                },
            ).status_code,
            422,
        )
        self.login("bob", "Bob password 123!")
        self.assertEqual(self.put(pid).status_code, 404)
        self.login("admin", "Admin password 123!")
        self.assertEqual(self.put(pid).status_code, 403)

    def test_uncertain_probe_cleanup_keeps_project_reserved(self):
        from app.execution.lifecycle import retry_pending

        self.login()
        pid = self.create()
        with (
            patch(
                "app.execution.client.RunnerClient.validate_workspace",
                side_effect=RunnerUnavailable("disconnected"),
            ),
            patch(
                "app.execution.client.RunnerClient.release_validation",
                side_effect=RunnerUnavailable("cleanup not confirmed"),
                create=True,
            ),
        ):
            self.assertEqual(self.put(pid).status_code, 503)
            self.assertIn(pid, transfers)
            self.assertEqual(self.put(pid).status_code, 409)
            self.assertEqual(
                self.client.get(f"/api/projects/{pid}").json()["workspace"]["revision"],
                0,
            )
        with patch(
            "app.execution.client.RunnerClient.release_validation",
            return_value={"removed": True},
            create=True,
        ):
            retry_pending()
        self.assertNotIn(pid, transfers)

    def test_cancelled_endpoint_waits_for_probe_and_keeps_old_binding(self):
        import asyncio
        import threading
        from types import SimpleNamespace
        from app.platform.workspaces import configure, WorkspaceInput
        from app.platform.db import DB

        self.login()
        pid = self.create()
        entered, finish = threading.Event(), threading.Event()

        def probe(*args):
            entered.set()
            self.assertTrue(finish.wait(2))
            return {"ready": True}

        async def exercise():
            with DB.begin() as db:
                task = asyncio.create_task(
                    configure(
                        pid,
                        WorkspaceInput(
                            mode="local", local_path="/host/demo", expected_revision=0
                        ),
                        SimpleNamespace(cookies=dict(self.client.cookies)),
                        (db, None, None),
                    )
                )
                self.assertTrue(await asyncio.to_thread(entered.wait, 1))
                task.cancel()
                await asyncio.sleep(0.01)
                self.assertIn(pid, transfers)
                self.assertFalse(task.done())
                finish.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task

        with patch(
            "app.execution.client.RunnerClient.validate_workspace", side_effect=probe
        ):
            asyncio.run(exercise())
        self.assertNotIn(pid, transfers)
        self.assertEqual(
            self.client.get(f"/api/projects/{pid}").json()["workspace"]["revision"], 0
        )
