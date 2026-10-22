import support
import unittest
import test_platform_auth as auth_tests
from app.platform.db import DB, ExecutionScope


class BindingTests(unittest.TestCase):
    setUp = auth_tests.PlatformTests.setUp
    login = auth_tests.PlatformTests.login

    def test_default_and_immutable_snapshot(self):
        from app.execution.bindings import snapshot

        self.login()
        p = self.client.post("/api/projects", json={"name": "Files"}).json()
        self.assertEqual(
            p["workspace"], {"mode": "managed", "local_path": None, "revision": 0}
        )
        with DB.begin() as db:
            s = db.get(ExecutionScope, p["id"])
            saved = snapshot(s)
            s.workspace_mode = "local"
            s.local_path = "/tmp/my project"
            s.workspace_revision = 1
            self.assertEqual(saved, p["workspace"])
            self.assertEqual(s.id, p["id"])

    def test_validation(self):
        from app.execution.bindings import validate

        for path in ["relative", "~user/project", "/tmp/a\n", "/tmp/a\x00", "C:\\tmp"]:
            with self.assertRaises(ValueError):
                validate({"mode": "local", "local_path": path, "revision": 0})
        self.assertEqual(
            validate({"mode": "local", "local_path": '/a b/"c', "revision": 2})[
                "revision"
            ],
            2,
        )
