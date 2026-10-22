"""Opt-in real runner + authenticated file gateway smoke, with a disposable DB and folder."""

import support
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
import test_platform_auth as auth_tests
from app.platform.db import DB, Run
from app.execution.client import RunnerClient
from app.execution.lifecycle import prepare, settle


class LocalApiSmoke(unittest.TestCase):
    setUp = auth_tests.PlatformTests.setUp
    login = auth_tests.PlatformTests.login

    def test_local_api_roundtrip(self):
        root = os.environ.get("HOST_TEST_ROOT")
        self.assertTrue(
            root and Path(root).is_dir(), "Explicit disposable HOST_TEST_ROOT required"
        )
        self.login()
        pid = self.client.post("/api/projects", json={"name": "Mount smoke"}).json()[
            "id"
        ]
        sid = self.client.post("/api/sessions", json={"project_id": pid}).json()[
            "session_id"
        ]
        rid = str(uuid.uuid4())
        runner = RunnerClient()
        with tempfile.TemporaryDirectory(prefix="api folder ", dir=root) as folder:
            source = Path(folder)
            source.chmod(0o777)
            sentinel = source / "keep.txt"
            sentinel.write_text("keep")
            sentinel.chmod(0o666)
            try:
                response = self.client.put(
                    f"/api/projects/{pid}/workspace",
                    json={
                        "mode": "local",
                        "local_path": folder,
                        "expected_revision": 0,
                    },
                )
                self.assertEqual(response.status_code, 200, response.text)
                with DB.begin() as db:
                    db.add(
                        Run(
                            id=rid,
                            session_id=sid,
                            request_id="smoke",
                            body_hash="smoke",
                            model="no-model-call",
                            event_offset=0,
                        )
                    )
                with patch.dict(os.environ, {"SANDBOX_ENABLED": "true"}):
                    prepare(rid)
                    result = runner.execute(
                        rid,
                        {
                            "op": "write_file",
                            "path": "tool.txt",
                            "content": "from tool",
                        },
                    )
                    self.assertNotIn("error", result)
                    self.assertTrue(settle(rid, "completed"))
                self.assertEqual((source / "tool.txt").read_text(), "from tool")
                response = self.client.post(
                    f"/api/projects/{pid}/files/upload?path=upload.bin",
                    content=b"\x00gateway",
                )
                self.assertEqual(response.status_code, 200, response.text)
                response = self.client.get(
                    f"/api/sessions/{sid}/files/download?path=upload.bin"
                )
                self.assertEqual(response.content, b"\x00gateway", response.text)
                self.assertEqual(
                    self.client.get(f"/api/projects/{pid}/read?path=tool.txt").json()[
                        "content"
                    ],
                    "from tool",
                )
                self.assertEqual(
                    self.client.delete(
                        f"/api/projects/{pid}/files?path=upload.bin"
                    ).status_code,
                    200,
                )
                response = self.client.put(
                    f"/api/projects/{pid}/workspace",
                    json={"mode": "managed", "expected_revision": 1},
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(
                    self.client.get(f"/api/projects/{pid}/files").json(), {"files": []}
                )
                self.assertTrue((source / "tool.txt").exists())
                self.assertEqual(sentinel.read_text(), "keep")
                self.assertEqual(
                    self.client.delete(f"/api/projects/{pid}").status_code, 200
                )
            finally:
                runner.destroy(rid)
                runner.purge(self.a, pid)
            self.assertEqual(sentinel.read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
