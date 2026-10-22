import support
import unittest
from unittest.mock import patch
import test_platform_auth as auth_tests
from app.execution.client import RunnerUnavailable
from app.execution.lifecycle import transfers


class WorkspaceTests(unittest.TestCase):
    setUp = auth_tests.PlatformTests.setUp
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
