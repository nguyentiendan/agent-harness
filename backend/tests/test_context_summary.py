import support
import unittest
import json
from dataclasses import replace
from app.context.summarization import validate_summary, maybe_summarize
from app.context.types import ContextRequest, ConversationGroup
from app.context.limits import ModelLimits
from app.context.policy import ContextPolicy


class SummaryTests(unittest.IsolatedAsyncioTestCase):
    def payload(self):
        return {
            key: [{"text": "fact", "source_ids": ["a"]}]
            for key in (
                "goal",
                "constraints",
                "decisions",
                "unresolved_work",
                "evidence",
            )
        }

    def test_unknown_source_rejected(self):
        with self.assertRaises(ValueError):
            validate_summary(self.payload(), {"b"})

    def test_schema_rejected(self):
        with self.assertRaises(ValueError):
            validate_summary({"goal": "free text"}, {"a"})

    async def test_call_allowance_and_failure(self):
        req = ContextRequest(
            "s",
            "r",
            "fake",
            ModelLimits(20000, None, 2000, "r"),
            1000,
            [{"role": "user", "content": "now"}],
            [ConversationGroup("g", [{"role": "user", "content": "old facts"}], ["a"])],
        )
        calls = []

        async def complete(messages, **kwargs):
            calls.append(kwargs)
            raise ValueError("invalid response")

        outcome = await maybe_summarize(req, ContextPolicy(), complete, 2)
        self.assertEqual(calls, [])
        self.assertEqual(outcome.reason, "allowance_exhausted")
        outcome = await maybe_summarize(req, ContextPolicy(), complete, 0)
        self.assertIsNone(outcome.summary)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["purpose"], "summary")
        self.assertEqual(calls[0]["max_tokens"], 1000)
