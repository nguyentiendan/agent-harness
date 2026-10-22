import support
import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock, AsyncMock
from app.platform.db import DB, Base, engine, Chat, Run, Project
from app.platform.identity import create_user
from app.platform import chat


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        with DB.begin() as db:
            owner = create_user(
                db, "sandboxuser", "Sandbox password 123!", "user", False
            )["id"]
            db.add(
                Project(
                    id="00000000-0000-0000-0000-000000000001",
                    owner_id=owner,
                    name="tools",
                )
            )
            db.flush()
            db.add(
                Chat(
                    id="00000000-0000-0000-0000-000000000001",
                    owner_id=owner,
                    title="tools",
                    project_id="00000000-0000-0000-0000-000000000001",
                    status="running",
                )
            )
            db.flush()
            db.add(
                Run(
                    id="00000000-0000-0000-0000-000000000002",
                    session_id="00000000-0000-0000-0000-000000000001",
                    request_id="test",
                    body_hash="test",
                    model="fake",
                    event_offset=0,
                )
            )
            db.flush()
            chat.append_event(
                db,
                "00000000-0000-0000-0000-000000000001",
                "00000000-0000-0000-0000-000000000002",
                {"type": "user", "text": "write a file"},
            )
        self.rid = "00000000-0000-0000-0000-000000000002"

    async def test_tools_use_runner_and_finally_destroy(self):
        tool = SimpleNamespace(
            id="call1",
            function=SimpleNamespace(
                name="write_file", arguments='{"path":"hello.txt","content":"hello"}'
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
        runner = MagicMock()
        runner.execute.return_value = {"bytes": 5}
        with (
            patch.dict(os.environ, {"SANDBOX_ENABLED": "true"}),
            patch("app.execution.client.RunnerClient", return_value=runner),
            patch(
                "app.platform.chat.LiteLLMClientWrapper.complete",
                new=AsyncMock(side_effect=responses),
            ),
        ):
            await chat.execute(self.rid)
        runner.ensure.assert_called_once()
        runner.execute.assert_called_once()
        runner.destroy.assert_called_once()
        with DB() as db:
            self.assertEqual(db.get(Run, self.rid).status, "completed")
            self.assertTrue(
                any(
                    e["type"] == "tool_result"
                    for e in chat.events_for(db, "00000000-0000-0000-0000-000000000001")
                )
            )

    async def test_cleanup_failure_keeps_lease_until_retry(self):
        from app.execution.lifecycle import acquire, settle
        from app.platform.db import RunLease

        with DB.begin() as db:
            acquire(db, db.get(Run, self.rid))
        with DB.begin() as db:
            db.get(
                RunLease, "00000000-0000-0000-0000-000000000001"
            ).sandbox_started = True
        runner = MagicMock()
        runner.destroy.side_effect = RuntimeError("down")
        with patch("app.execution.client.RunnerClient", return_value=runner):
            self.assertFalse(settle(self.rid, "stopped"))
            with DB() as db:
                self.assertIsNotNone(
                    db.get(RunLease, "00000000-0000-0000-0000-000000000001")
                )
            runner.destroy.side_effect = None
            self.assertTrue(settle(self.rid, "stopped"))
        with DB() as db:
            self.assertIsNone(db.get(RunLease, "00000000-0000-0000-0000-000000000001"))
            self.assertEqual(db.get(Run, self.rid).status, "stopped")

    def test_cancelled_run_cannot_lazily_create_sandbox(self):
        from app.execution.lifecycle import prepare

        with DB.begin() as db:
            db.get(Run, self.rid).status = "stopped"
        runner = MagicMock()
        with patch("app.execution.client.RunnerClient", return_value=runner):
            with self.assertRaises(RuntimeError):
                prepare(self.rid)
        runner.ensure.assert_not_called()

    def test_disabled_tools_still_purge_existing_files(self):
        from app.execution.lifecycle import prepare, settle
        from app.cli.cleanup import purge

        runner = MagicMock()
        with patch("app.execution.client.RunnerClient", return_value=runner):
            prepare(self.rid)
            settle(self.rid, "completed")
            with DB.begin() as db:
                db.get(Chat, "00000000-0000-0000-0000-000000000001").deleted_at = 1
                db.get(Project, "00000000-0000-0000-0000-000000000001").deleted_at = 1
            with patch.dict(os.environ, {"SANDBOX_ENABLED": "false"}):
                self.assertEqual(purge(), 1)
        runner.purge.assert_called_once()

    async def test_plain_chat_does_not_allocate_container(self):
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content="hello", tool_calls=[]))
            ]
        )
        runner = MagicMock()
        with (
            patch.dict(os.environ, {"SANDBOX_ENABLED": "true"}),
            patch("app.execution.client.RunnerClient", return_value=runner),
            patch(
                "app.platform.chat.LiteLLMClientWrapper.complete",
                new=AsyncMock(return_value=response),
            ),
        ):
            await chat.execute(self.rid)
        runner.ensure.assert_not_called()
        runner.destroy.assert_not_called()
        with DB() as db:
            self.assertEqual(db.get(Run, self.rid).status, "completed")
