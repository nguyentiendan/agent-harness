"""Opt-in HTTP -> real runner -> Docker binary/project lifecycle test. Test DB only."""

import os, sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import support

os.environ["SANDBOX_ENABLED"] = "true"
from test_platform_auth import PlatformTests
from app.platform.db import DB, Chat, Project, ExecutionScope
from app.cli.cleanup import purge
from unittest.mock import patch, AsyncMock
from types import SimpleNamespace
import unittest, time


class ProjectFileSmoke(PlatformTests):
    # Reuse setup/helpers only; the suite below explicitly selects this one test.
    def test_real_project_files(self):
        self.login()
        pid = self.client.post(
            "/api/projects", json={"name": "Files smoke", "instructions": "Be concise."}
        ).json()["id"]
        ids = [
            self.client.post("/api/sessions", json={"project_id": pid}).json()[
                "session_id"
            ]
            for _ in range(2)
        ]
        standalone = self.new_chat()
        data = b"\x00\xffbinary\r\n<script>alert(1)</script>"
        path = "report.bin"
        r = self.client.post(
            f"/api/projects/{pid}/files/upload?path={path}",
            content=data,
            headers={"Content-Type": "application/octet-stream"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        for sid in ids:
            self.assertEqual(
                self.client.get(f"/api/sessions/{sid}/files").json()["files"][0][
                    "path"
                ],
                path,
            )
            r = self.client.get(f"/api/sessions/{sid}/files/download?path={path}")
            self.assertEqual(r.content, data)
            self.assertIn("attachment", r.headers["content-disposition"])
            self.assertEqual(r.headers["content-type"], "application/octet-stream")
        self.assertEqual(
            self.client.get(f"/api/sessions/{standalone}/files").json()["files"], []
        )
        self.assertEqual(
            self.client.get(f"/api/projects/{pid}/read?path={path}").status_code, 409
        )
        self.assertEqual(
            self.client.post(
                f"/api/projects/{pid}/files/upload?path=../escape", content=b"bad"
            ).status_code,
            409,
        )
        self.login("bob", "Bob password 123!")
        self.assertEqual(
            self.client.get(
                f"/api/projects/{pid}/files/download?path={path}"
            ).status_code,
            404,
        )
        self.login("admin", "Admin password 123!")
        self.assertEqual(
            self.client.get(f"/api/projects/{pid}/files/download?path={path}").content,
            data,
        )
        self.login()
        # Fake the model only; agent tool execution goes through real runner and same project volume.
        tool = SimpleNamespace(
            id="call1",
            function=SimpleNamespace(
                name="bash", arguments='{"command":"printf agent > agent.txt"}'
            ),
        )
        responses = [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=None, tool_calls=[tool])
                    )
                ]
            ),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="done", tool_calls=[])
                    )
                ]
            ),
        ]
        with patch(
            "app.platform.chat.LiteLLMClientWrapper.complete",
            new=AsyncMock(side_effect=responses),
        ):
            r = self.client.post(
                "/api/harness/start",
                json={
                    "session_id": ids[0],
                    "request_id": "smoke",
                    "goal": "create agent.txt",
                },
            )
            self.assertEqual(r.status_code, 200, r.text)
            for _ in range(100):
                state = self.client.get("/api/harness/status/" + ids[0]).json()[
                    "status"
                ]
                if state != "running":
                    break
                time.sleep(0.1)
            self.assertEqual(state, "completed")
        self.assertEqual(
            self.client.get(
                f"/api/sessions/{ids[1]}/files/download?path=agent.txt"
            ).content,
            b"agent",
        )
        self.assertEqual(
            self.client.get("/api/sessions/" + ids[1]).json()["events"], []
        )
        self.client.delete("/api/sessions/" + ids[0])
        with DB.begin() as db:
            db.get(Chat, ids[0]).deleted_at = 1
        self.assertEqual(purge(), 1)
        self.assertEqual(
            self.client.get(
                f"/api/sessions/{ids[1]}/files/download?path={path}"
            ).content,
            data,
        )
        r = self.client.delete(f"/api/projects/{pid}/files?path={path}")
        self.assertEqual(r.status_code, 200, r.text)
        self.client.delete("/api/projects/" + pid)
        with DB.begin() as db:
            db.get(Project, pid).deleted_at = 1
            db.get(Chat, ids[1]).deleted_at = 1
        self.assertEqual(purge(), 1)
        self.assertEqual(purge(), 0)
        with DB() as db:
            self.assertIsNone(db.get(ExecutionScope, pid))
        print(
            "PASS: HTTP binary upload/download + agent shared files + isolated history + Admin/User + trash purge"
        )


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.TestSuite([ProjectFileSmoke("test_real_project_files")])
    )
    sys.exit(not result.wasSuccessful())
