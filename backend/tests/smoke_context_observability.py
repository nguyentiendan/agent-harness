"""Authenticated public orchestration with real Docker runner, no provider calls."""

import support
import os
import time
import unittest
from unittest.mock import patch, AsyncMock
import test_platform_auth as auth
from test_evaluation_tasks import answer
from app.execution.client import RunnerClient


class ContextSmoke(unittest.TestCase):
    setUp = auth.PlatformTests.setUp
    login = auth.PlatformTests.login

    def test_project_context_trace_and_file_evidence(self):
        self.login()
        pid = self.client.post("/api/projects", json={"name": "Context smoke"}).json()[
            "id"
        ]
        sid = self.client.post("/api/sessions", json={"project_id": pid}).json()[
            "session_id"
        ]
        runner = RunnerClient()
        try:
            with (
                patch.dict(os.environ, SANDBOX_ENABLED="true"),
                patch(
                    "app.billing.catalog.lookup",
                    return_value={
                        "context_window": 64000,
                        "max_output_tokens": 4096,
                        "revision": "smoke",
                    },
                ),
                patch(
                    "app.integrations.llm.LiteLLMClientWrapper._dispatch",
                    new=AsyncMock(
                        side_effect=[
                            answer(
                                calls=[
                                    (
                                        "write_file",
                                        {
                                            "path": "evidence.txt",
                                            "content": "actual-file",
                                        },
                                    )
                                ]
                            ),
                            answer("Written and verified"),
                            answer(calls=[("read_file", {"path": "evidence.txt"})]),
                            answer("actual-file"),
                        ]
                    ),
                ) as model,
            ):
                runs = []
                for i, goal in enumerate(
                    ["Create the evidence file", "Read it again and report its content"]
                ):
                    result = self.client.post(
                        "/api/harness/start",
                        json={"session_id": sid, "request_id": str(i), "goal": goal},
                    )
                    self.assertEqual(result.status_code, 200, result.text)
                    rid = result.json()["run_id"]
                    runs.append(rid)
                    for _ in range(300):
                        status = self.client.get(f"/api/harness/status/{sid}").json()[
                            "status"
                        ]
                        if status != "running":
                            break
                        time.sleep(0.05)
                    self.assertEqual(status, "completed")
                    trace = self.client.get(f"/api/sessions/{sid}/runs/{rid}/trace")
                    self.assertEqual(trace.status_code, 200, trace.text)
                    self.assertTrue(
                        any(s["kind"] == "tool" for s in trace.json()["spans"])
                    )
                self.assertTrue(
                    any(
                        m.get("role") == "tool"
                        and "bytes" in str(m.get("content"))
                        for m in model.call_args_list[2].args[0]
                    )
                )
                self.assertEqual(
                    runner.files(self.a, pid, "read_file", "evidence.txt")["content"],
                    "actual-file",
                )
            self.assertEqual(
                self.client.delete(f"/api/sessions/{sid}").status_code, 200
            )
            self.assertEqual(
                self.client.get(
                    f"/api/sessions/{sid}/runs/{runs[0]}/trace"
                ).status_code,
                404,
            )
        finally:
            runner.purge(self.a, pid)


if __name__ == "__main__":
    unittest.main()
