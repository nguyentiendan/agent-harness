"""Regression checks for recoverable and rolling public-run compaction."""

import support
import json
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
import test_context_integration
from app.context.runtime import ContextRuntime
from app.platform.db import DB, Chat
from app.context.history import append_records
from app.context.types import ConversationRecord


class CompactionTests(unittest.IsolatedAsyncioTestCase):
    setUp = test_context_integration.RuntimeTests.setUp

    def runtime(self):
        runtime = ContextRuntime("u", "r")
        runtime.policy = replace(
            runtime.policy,
            recent_groups=1,
            safety_tokens=128,
            summary_output_tokens=256,
            summary_input_fraction=0.7,
        )
        return runtime

    async def summarize(self, messages, **kwargs):
        records = json.loads(messages[1]["content"])
        self.calls.append(records)
        ids = [i for r in records for i in r["source_ids"]]
        data = {
            k: []
            for k in ("goal", "constraints", "decisions", "unresolved_work", "evidence")
        }
        data["decisions"] = [
            {"text": "completed steps; do not repeat writes", "source_ids": ids}
        ]
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(data)))]
        )

    def groups(self, count=6, size=900):
        messages = [{"role": "user", "content": "CURRENT correction: use blue"}]
        for i in range(count):
            messages.extend(
                [
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": str(i),
                                "function": {"name": "bash", "arguments": "{}"},
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": str(i),
                        "content": json.dumps({"stdout": "x" * size, "exit_code": 0}),
                    },
                ]
            )
        return messages

    async def prepare(self, runtime, messages):
        with patch(
            "app.billing.catalog.lookup",
            return_value={
                "context_window": 6000,
                "max_output_tokens": 500,
                "revision": "v",
            },
        ):
            return await runtime.prepare(messages, "fake", [], 500, self.summarize)

    async def test_active_run_compacts_and_does_not_duplicate_covered_groups(self):
        self.calls = []
        out = await self.prepare(self.runtime(), self.groups())
        self.assertTrue(self.calls, "Completed active groups must be summarized")
        self.assertTrue(out.manifest["summary_used"])
        covered = set(out.manifest["summary_source_ids"])
        self.assertTrue(covered)
        self.assertTrue(any(m.get("tool_call_id") == "5" for m in out.messages))
        self.assertEqual(
            sum(
                m.get("content") == "CURRENT correction: use blue" for m in out.messages
            ),
            1,
        )
        self.assertFalse(
            set(out.manifest["selected_group_ids"])
            & set(out.manifest["summary_group_ids"])
        )

    async def test_newest_large_tool_result_is_recoverable(self):
        self.calls = []
        out = await self.prepare(self.runtime(), self.groups(count=1, size=12000))
        content = next(m["content"] for m in out.messages if m["role"] == "tool")
        envelope = json.loads(content)
        from app.context.outputs import read_output

        recovered = read_output(
            "r", envelope["output_id"], 0, 100, self.runtime().policy
        )
        self.assertEqual(
            recovered["content"],
            json.dumps({"stdout": "x" * 12000, "exit_code": 0})[:100],
        )
        self.assertLessEqual(
            out.manifest["estimated_tokens"], out.manifest["input_budget"]
        )

    async def test_summary_refreshes_when_uncovered_history_grows(self):
        self.calls = []
        runtime = self.runtime()

        def add(start, end):
            with DB.begin() as db:
                for i in range(start, end):
                    append_records(
                        db,
                        "s",
                        "old",
                        [
                            ConversationRecord(
                                str(i),
                                "user",
                                {"role": "user", "content": str(i) + "x" * 900},
                                str(i),
                            )
                        ],
                    )

        add(0, 6)
        first = await self.prepare(runtime, [{"role": "user", "content": "now"}])
        add(6, 12)
        second = await self.prepare(runtime, [{"role": "user", "content": "now"}])
        self.assertEqual(len(self.calls), 2)
        self.assertGreater(
            len(second.manifest["summary_source_ids"]),
            len(first.manifest["summary_source_ids"]),
        )

    async def test_output_scope_revision_deletion_and_pagination(self):
        from app.context.outputs import store_output, read_output
        from app.platform.db import Run

        policy = self.runtime().policy
        key = store_output("r", "test", "abcdef", policy)
        self.assertEqual(read_output("r", key, 1, 2, policy)["content"], "bc")
        with DB.begin() as db:
            db.add(Chat(id="other", owner_id="u", title="other"))
            db.flush()
            db.add(
                Run(
                    id="other-run",
                    session_id="other",
                    request_id="other",
                    body_hash="x",
                    model="fake",
                    event_offset=0,
                )
            )
        with self.assertRaises(ValueError):
            read_output("other-run", key, 0, 10, policy)
        with DB.begin() as db:
            db.get(Run, "r").workspace_snapshot = {"revision": 2}
        with self.assertRaises(ValueError):
            read_output("r", key, 0, 10, policy)
        with DB.begin() as db:
            db.get(Chat, "s").deleted_at = 1
        with self.assertRaises(ValueError):
            read_output("r", key, 0, 10, policy)

    async def test_output_quota_and_retention(self):
        from app.context.outputs import store_output, read_output
        from app.observability.storage import retain, purge_scopes
        import time

        policy = replace(
            self.runtime().policy, output_max_chars=10, output_scope_chars=15
        )
        key = store_output("r", "one", "a" * 10, policy)
        with self.assertRaises(ValueError):
            store_output("r", "two", "b" * 10, policy)
        with DB.begin() as db:
            retain(db, time.time() + (policy.output_retention_days + 1) * 86400, policy)
        with self.assertRaises(ValueError):
            read_output("r", key, 0, 10, policy)
        key = store_output("r", "three", "c", policy)
        with DB.begin() as db:
            purge_scopes(db, ["s"])
        with self.assertRaises(ValueError):
            read_output("r", key, 0, 10, policy)

    async def test_repeated_compaction_preserves_coverage_and_current_correction(self):
        self.calls = []
        runtime = self.runtime()
        runtime.policy = replace(runtime.policy, summary_max_calls=4)
        covered = set()
        for step in range(3):
            out = await self.prepare(runtime, self.groups(count=6 + step * 2))
            self.assertTrue(out.manifest["summary_used"])
            self.assertTrue(covered.issubset(out.manifest["summary_source_ids"]))
            covered = set(out.manifest["summary_source_ids"])
            self.assertTrue(
                any(
                    m.get("content") == "CURRENT correction: use blue"
                    for m in out.messages
                )
            )
            calls = [c["id"] for m in out.messages for c in m.get("tool_calls", [])]
            results = [m["tool_call_id"] for m in out.messages if m["role"] == "tool"]
            self.assertEqual(sorted(calls), sorted(results))
        self.assertEqual(len(self.calls), 3)

    async def test_summary_failure_keeps_previous_summary_and_explains_omissions(self):
        self.calls = []
        runtime = self.runtime()
        await self.prepare(runtime, self.groups())

        async def failed(*args, **kwargs):
            raise ValueError("provider failed")

        self.summarize = failed
        out = await self.prepare(runtime, self.groups(count=9))
        self.assertTrue(out.manifest["summary_used"])
        self.assertEqual(out.manifest["summary_reason"], "summary_failed")
        self.assertTrue(out.manifest["omitted"])

    async def test_cancellation_does_not_publish_summary(self):
        import asyncio
        from app.platform.db import ContextSummary
        from sqlalchemy import select

        runtime = self.runtime()

        async def cancel(*args, **kwargs):
            raise asyncio.CancelledError()

        self.summarize = cancel
        with self.assertRaises(asyncio.CancelledError):
            await self.prepare(runtime, self.groups())
        with DB() as db:
            self.assertEqual(list(db.scalars(select(ContextSummary))), [])

    async def test_public_tool_loop_recovers_output_without_repeating_action(self):
        from app.execution.agent import run
        from app.context.policy import ContextPolicy
        from unittest.mock import MagicMock
        from test_evaluation_tasks import answer

        observed = []

        async def complete(messages, **kwargs):
            observed.append(messages.copy())
            if len(observed) == 1:
                return answer(calls=[("bash", {"command": "printf large-output"})])
            if len(observed) == 2:
                result = json.loads(messages[-1]["content"])
                return answer(
                    calls=[
                        (
                            "read_tool_output",
                            {
                                "output_id": result["output_id"],
                                "offset": 4000,
                                "limit": 80,
                            },
                        )
                    ]
                )
            page = json.loads(messages[-1]["content"])
            self.assertEqual(page["content"], "x" * 80)
            return answer("verified archived output")

        runner = MagicMock()
        runner.execute.return_value = {"stdout": "x" * 12000, "exit_code": 0}
        with (
            patch("app.execution.agent.lifecycle.prepare"),
            patch("app.execution.agent.client.RunnerClient", return_value=runner),
        ):
            await run(
                SimpleNamespace(complete=complete),
                "r",
                [{"role": "user", "content": "inspect output"}],
                "fake",
                lambda e: None,
            )
        self.assertEqual(runner.execute.call_count, 1)

    async def test_output_quota_blocks_tool_before_execution(self):
        from app.execution.agent import run
        from unittest.mock import MagicMock
        from test_evaluation_tasks import answer

        async def complete(*args, **kwargs):
            return answer(calls=[("bash", {"command": "side effect"})])

        runner = MagicMock()
        with patch(
            "app.execution.agent.load_config",
            return_value={"context": {"output_scope_chars": 8000000}},
        ):
            from app.context.outputs import store_output

            store_output("r", "existing", "already stored", self.runtime().policy)
            with patch("app.execution.agent.client.RunnerClient", return_value=runner):
                with self.assertRaisesRegex(ValueError, "no tool was executed"):
                    await run(
                        SimpleNamespace(complete=complete),
                        "r",
                        [],
                        "fake",
                        lambda e: None,
                    )
        runner.execute.assert_not_called()

    async def test_recovery_pages_keep_offsets(self):
        await self._recover_without_write(64000)

    async def test_recovery_works_at_full_quota(self):
        await self._recover_without_write(12000)

    async def _recover_without_write(self, capacity):
        from app.execution.agent import run
        from app.context.outputs import store_output
        from app.platform.db import ToolOutput
        from sqlalchemy import select
        from unittest.mock import patch
        from test_evaluation_tasks import answer

        policy = replace(
            self.runtime().policy, output_max_chars=12000, output_scope_chars=capacity
        )
        key = store_output("r", "full", "x" * 12000, policy)
        count = 0

        async def complete(messages, **kwargs):
            nonlocal count
            count += 1
            if count == 1:
                return answer(
                    calls=[
                        (
                            "read_tool_output",
                            {"output_id": key, "offset": 100, "limit": 8000},
                        )
                    ]
                )
            page = json.loads(messages[-1]["content"])
            self.assertEqual(page["output_id"], key)
            self.assertEqual(page["offset"], 100)
            self.assertEqual(page["next_offset"], 100 + len(page["content"]))
            self.assertEqual(page["total_chars"], 12000)
            return answer("read")

        with patch(
            "app.execution.agent.load_config",
            return_value={
                "context": {"output_max_chars": 12000, "output_scope_chars": capacity}
            },
        ):
            await run(
                SimpleNamespace(complete=complete), "r", [], "fake", lambda e: None
            )
        with DB() as db:
            self.assertEqual(len(list(db.scalars(select(ToolOutput)))), 1)

    async def test_active_summary_reused_in_next_run(self):
        from app.context.history import record_protocol
        from app.platform.db import Run

        self.calls = []
        messages = self.groups()
        for index in range(6):
            record_protocol(
                "r", f"turn:{index}:assistant", messages[1 + index * 2], f"turn:{index}"
            )
            record_protocol(
                "r", f"turn:{index}:{index}", messages[2 + index * 2], f"turn:{index}"
            )
        runtime = self.runtime()
        first = await self.prepare(runtime, messages)
        with DB.begin() as db:
            db.get(Run, "r").status = "completed"
            db.add(
                Run(
                    id="r2",
                    session_id="s",
                    request_id="r2",
                    body_hash="x",
                    model="fake",
                    event_offset=10,
                )
            )
        next_runtime = ContextRuntime("u", "r2")
        next_runtime.policy = runtime.policy
        with patch(
            "app.billing.catalog.lookup",
            return_value={
                "context_window": 6000,
                "max_output_tokens": 500,
                "revision": "v",
            },
        ):
            out = await next_runtime.prepare(
                [{"role": "user", "content": "next"}], "fake", [], 500, None
            )
        self.assertTrue(out.manifest["summary_used"])
        self.assertEqual(
            set(first.manifest["summary_source_ids"]),
            set(out.manifest["summary_source_ids"]),
        )
