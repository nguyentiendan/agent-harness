import unittest
from app.context.policy import load_context_policy
from app.context.limits import resolve_model_limits, effective_output_cap


class ContextPolicyTests(unittest.TestCase):
    def test_invalid_policy_rejected(self):
        for settings in (
            {"summary_trigger": 1.1},
            {"max_turns": 0},
            {"recent_groups": -1},
            {"summary_enabled": "false"},
            {"unknown": 1},
        ):
            with self.assertRaises(ValueError):
                load_context_policy({"context": settings})

    def test_limits_and_output(self):
        policy = load_context_policy({})
        limits = resolve_model_limits(
            {
                "context_window": 8000,
                "max_input_tokens": 7000,
                "max_output_tokens": 2000,
                "revision": "a",
            },
            policy,
        )
        self.assertEqual(limits.context_window, 8000)
        self.assertEqual(limits.max_input_tokens, 7000)
        self.assertEqual(effective_output_cap(4000, 1000, limits), 1000)

    def test_old_catalog_is_combined_window(self):
        limits = resolve_model_limits(
            {
                "provider": "OpenRouter",
                "max_input_tokens": 8000,
                "max_output_tokens": 2000,
            },
            load_context_policy({}),
        )
        self.assertEqual(limits.context_window, 8000)
        self.assertIsNone(limits.max_input_tokens)

    def test_unknown_limits_block(self):
        with self.assertRaises(ValueError):
            resolve_model_limits({}, load_context_policy({}))
