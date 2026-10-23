"""Scripted model transport, real public orchestration, actual temporary files."""

import support
import asyncio
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, AsyncMock, MagicMock
from test_execution import ExecutionTests
from app.platform import chat
from app.platform.db import DB, Run
from app.evaluation.verifiers import verify_file


def answer(text="", calls=()):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=text,
                    tool_calls=[
                        SimpleNamespace(
                            id=f"call-{i}",
                            function=SimpleNamespace(
                                name=name, arguments=json.dumps(args)
                            ),
                        )
                        for i, (name, args) in enumerate(calls)
                    ],
                )
            )
        ]
    )


class EvidenceTasks(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ExecutionTests.setUp(self)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.outputs = []

    def tool(self, rid, payload):
        op = payload["op"]
        path = (self.root / payload.get("path", ".")).resolve()
        if not path.is_relative_to(self.root):
            return {"error": "outside workspace"}
        if op == "write_file":
            path.write_text(payload["content"])
            result = {"bytes": path.stat().st_size}
        elif op == "read_file":
            result = {"content": path.read_text()}
        elif op == "edit_file":
            text = path.read_text()
            if text.count(payload["old"]) != 1:
                result = {"error": "not unique"}
            else:
                path.write_text(text.replace(payload["old"], payload["new"], 1))
                result = {"edited": True}
        elif op == "list_dir":
            result = {"files": [p.name for p in self.root.iterdir()]}
        elif op == "bash":
            process = subprocess.run(
                ["sh", "-c", payload["command"]],
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=3,
            )
            result = {
                "stdout": process.stdout,
                "stderr": process.stderr,
                "exit_code": process.returncode,
            }
        else:
            raise AssertionError("unexpected tool")
        self.outputs.append(result)
        return result

    async def execute(self, responses):
        runner = MagicMock()
        runner.execute.side_effect = self.tool
        with (
            patch.dict("os.environ", SANDBOX_ENABLED="true"),
            patch("app.execution.client.RunnerClient", return_value=runner),
            patch(
                "app.billing.catalog.lookup",
                return_value={
                    "context_window": 64000,
                    "max_output_tokens": 4096,
                    "revision": "fixture",
                },
            ),
            patch(
                "app.integrations.llm.LiteLLMClientWrapper._dispatch",
                new=AsyncMock(side_effect=responses),
            ),
        ):
            await chat.execute(self.rid)
        with DB() as db:
            self.assertEqual(db.get(Run, self.rid).status, "completed")

    async def test_write_unicode(self):
        await self.execute(
            [
                answer(
                    calls=[
                        ("write_file", {"path": "a.txt", "content": "Xin chào 日本"})
                    ]
                ),
                answer("done"),
            ]
        )
        self.assertTrue(verify_file(str(self.root), "a.txt", "Xin chào 日本")["passed"])

    async def test_read_existing(self):
        (self.root / "a.txt").write_text("original")
        await self.execute(
            [answer(calls=[("read_file", {"path": "a.txt"})]), answer("original")]
        )
        self.assertEqual(self.outputs[0]["content"], "original")

    async def test_edit_exact(self):
        (self.root / "a.txt").write_text("before")
        await self.execute(
            [
                answer(
                    calls=[
                        (
                            "edit_file",
                            {"path": "a.txt", "old": "before", "new": "after"},
                        )
                    ]
                ),
                answer("done"),
            ]
        )
        self.assertTrue(verify_file(str(self.root), "a.txt", "after")["passed"])

    async def test_list_actual_files(self):
        (self.root / "a.txt").write_text("a")
        await self.execute([answer(calls=[("list_dir", {})]), answer("a.txt")])
        self.assertEqual(self.outputs[0]["files"], ["a.txt"])

    async def test_command_exit_evidence(self):
        await self.execute(
            [
                answer(calls=[("bash", {"command": "printf success; exit 0"})]),
                answer("done"),
            ]
        )
        self.assertEqual(self.outputs[0]["exit_code"], 0)
        self.assertEqual(self.outputs[0]["stdout"], "success")

    async def test_false_success_not_accepted(self):
        await self.execute([answer("I created a.txt successfully")])
        self.assertFalse(verify_file(str(self.root), "a.txt", "expected")["passed"])

    async def test_multiple_calls_preserve_results(self):
        await self.execute(
            [
                answer(
                    calls=[
                        ("write_file", {"path": "a.txt", "content": "one"}),
                        ("write_file", {"path": "b.txt", "content": "two"}),
                    ]
                ),
                answer("done"),
            ]
        )
        self.assertTrue(verify_file(str(self.root), "a.txt", "one")["passed"])
        self.assertTrue(verify_file(str(self.root), "b.txt", "two")["passed"])
        from app.context.history import load_groups

        with DB() as db:
            groups = load_groups(db, db.get(Run, self.rid).session_id)
        self.assertTrue(
            any(sum(m["role"] == "tool" for m in g.messages) == 2 for g in groups)
        )

    async def test_failed_edit_preserves_file(self):
        (self.root / "a.txt").write_text("same same")
        await self.execute(
            [
                answer(
                    calls=[
                        ("edit_file", {"path": "a.txt", "old": "same", "new": "new"})
                    ]
                ),
                answer("cannot edit uniquely"),
            ]
        )
        self.assertIn("error", self.outputs[0])
        self.assertTrue(verify_file(str(self.root), "a.txt", "same same")["passed"])
