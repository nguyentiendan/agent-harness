import support
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from app.platform.db import Base, engine, DB, Run, Chat
from app.context.runtime import ContextRuntime


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        with DB.begin() as db:
            from app.platform.db import User

            db.add(
                User(
                    id="u",
                    username="runtime-test",
                    password_hash="not-a-password",
                    role="user",
                    active=True,
                    must_change_password=False,
                )
            )
            db.flush()
            db.add(Chat(id="s", owner_id="u", title="test"))
            db.flush()
            db.add(
                Run(
                    id="r",
                    session_id="s",
                    request_id="r",
                    body_hash="x",
                    model="fake",
                    event_offset=0,
                )
            )

    async def test_every_call_prepared_with_output_cap(self):
        runtime = ContextRuntime("u", "r")
        metadata = {"context_window": 8000, "max_output_tokens": 1000, "revision": "v"}
        with patch("app.billing.catalog.lookup", return_value=metadata):
            out = await runtime.prepare(
                [
                    {"role": "system", "content": "policy"},
                    {"role": "user", "content": "hello"},
                ],
                "fake",
                [],
                4096,
                None,
            )
        self.assertEqual(out.effective_max_output_tokens, 1000)
        self.assertEqual(out.messages[-1]["content"], "hello")
        self.assertEqual(out.manifest["model_revision"], "v")

    async def test_unknown_model_not_dispatched(self):
        runtime = ContextRuntime("u", "r")
        with (
            patch("app.billing.catalog.lookup", return_value=None),
            patch("app.billing.gateway.priced_models", return_value={}),
        ):
            with self.assertRaises(ValueError):
                await runtime.prepare(
                    [{"role": "user", "content": "hello"}], "missing", [], 1000, None
                )

    async def test_summary_publication_and_reuse(self):
        import json
        from app.context.history import append_records
        from app.context.types import ConversationRecord
        from app.platform.db import ContextSummary
        from sqlalchemy import select

        with DB.begin() as db:
            for i in range(6):
                append_records(
                    db,
                    "s",
                    "old",
                    [
                        ConversationRecord(
                            str(i),
                            "user",
                            {"role": "user", "content": "x" * 800},
                            str(i),
                        )
                    ],
                )
        runtime = ContextRuntime("u", "r")
        from dataclasses import replace

        runtime.policy = replace(runtime.policy, summary_trigger=0.2)
        calls = []

        async def summarize(messages, **kwargs):
            records = json.loads(messages[1]["content"])
            source = records[0]["source_ids"][0]
            payload = {
                k: [{"text": "historical fact", "source_ids": [source]}]
                for k in (
                    "goal",
                    "constraints",
                    "decisions",
                    "unresolved_work",
                    "evidence",
                )
            }
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=json.dumps(payload))
                    )
                ]
            )

        metadata = {"context_window": 20000, "max_output_tokens": 2000, "revision": "v"}
        with patch("app.billing.catalog.lookup", return_value=metadata):
            first = await runtime.prepare(
                [{"role": "user", "content": "corrected requirement"}],
                "fake",
                [],
                2000,
                summarize,
            )
            second = await runtime.prepare(
                [{"role": "user", "content": "corrected requirement"}],
                "fake",
                [],
                2000,
                summarize,
            )
        self.assertEqual(len(calls), 1)
        self.assertTrue(first.manifest["summary_used"])
        self.assertTrue(second.manifest["summary_used"])
        self.assertEqual(second.messages[-1]["content"], "corrected requirement")
        with DB() as db:
            self.assertEqual(len(list(db.scalars(select(ContextSummary)))), 1)

    async def test_old_active_tool_groups_can_be_omitted(self):
        runtime = ContextRuntime("u", "r")
        messages = [{"role": "user", "content": "current task"}]
        for i in range(3):
            messages += [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": str(i),
                            "function": {"name": "read_file", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": str(i), "content": "x" * 1600},
            ]
        with patch(
            "app.billing.catalog.lookup",
            return_value={
                "context_window": 5000,
                "max_output_tokens": 500,
                "revision": "v",
            },
        ):
            prepared = await runtime.prepare(messages, "fake", [], 500, None)
        self.assertTrue(prepared.manifest["omitted"])
        self.assertTrue(any(m.get("tool_call_id") == "2" for m in prepared.messages))
        self.assertTrue(
            any(m.get("content") == "current task" for m in prepared.messages)
        )

    async def test_catalog_change_blocks_before_dispatch(self):
        from app.billing.completion import complete
        from app.billing.gateway import GatewayError

        with patch(
            "app.billing.catalog.lookup",
            return_value={
                "revision": "new",
                "context_window": 8000,
                "max_output_tokens": 1000,
            },
        ):
            with self.assertRaises(GatewayError):
                await complete(
                    "u",
                    "r",
                    [{"role": "user", "content": "hi"}],
                    "fake",
                    None,
                    0.2,
                    1000,
                    expected_model_revision="old",
                )
