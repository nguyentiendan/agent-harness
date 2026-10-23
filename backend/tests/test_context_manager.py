import unittest
from app.context.types import ContextRequest, ConversationGroup, ContextBudgetExceeded
from app.context.limits import ModelLimits
from app.context.policy import ContextPolicy
from app.context.manager import prepare_context
from app.context.tokens import count_payload


class ContextManagerTests(unittest.TestCase):
    def request(self, window=4000):
        return ContextRequest(
            "s",
            "r",
            "unknown",
            ModelLimits(window, None, 500, "r"),
            500,
            [
                {"role": "system", "content": "policy"},
                {"role": "user", "content": "keep current correction"},
            ],
        )

    def test_mandatory_overflow(self):
        req = self.request(1200)
        with self.assertRaises(ContextBudgetExceeded):
            prepare_context(req, ContextPolicy())

    def test_groups_are_atomic_and_omissions_explained(self):
        req = self.request()
        req.groups = [
            ConversationGroup(
                "old", [{"role": "user", "content": "あ" * 3000}], ["old"]
            ),
            ConversationGroup(
                "new",
                [
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "t",
                                "function": {"name": "read_file", "arguments": "{}"},
                            }
                        ],
                    },
                    {"role": "tool", "tool_call_id": "t", "content": "ok"},
                ],
                ["a", "t"],
            ),
        ]
        out = prepare_context(req, ContextPolicy())
        self.assertEqual(out.manifest["selected_group_ids"], ["new"])
        self.assertEqual(out.manifest["omitted"][0]["id"], "old")
        self.assertEqual(out.messages[-1]["content"], "keep current correction")
        self.assertTrue(any(m.get("tool_call_id") == "t" for m in out.messages))
        self.assertLessEqual(out.manifest["estimated_tokens"] + 500 + 1024, 4000)

    def test_workspace_switch_excludes_evidence(self):
        req = self.request()
        req.workspace_revision = 2
        req.groups = [
            ConversationGroup("stale", [{"role": "user", "content": "old"}], ["a"], 1)
        ]
        out = prepare_context(req, ContextPolicy())
        self.assertEqual(out.manifest["omitted"][0]["reason"], "workspace_revision")

    def test_tools_are_counted(self):
        plain = count_payload(
            [{"role": "user", "content": "hello"}], [], "unknown"
        ).tokens
        with_tools = count_payload(
            [{"role": "user", "content": "hello"}],
            [{"description": "x" * 500}],
            "unknown",
        ).tokens
        self.assertGreater(with_tools, plain + 400)
