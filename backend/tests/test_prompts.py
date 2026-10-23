import support

import unittest
from app.prompts.types import PromptSpec
from app.prompts.registry import render_prompt


class PromptTests(unittest.TestCase):
    def test_missing_and_extra_variables_rejected(self):
        spec = PromptSpec('test', '1', 'Hello {name}', ('name',))
        for values in ({}, {'name': 'A', 'extra': 1}):
            with self.assertRaises(ValueError):
                render_prompt(spec, values)

    def test_inserted_braces_not_reinterpreted(self):
        spec = PromptSpec('test', '1', 'Hello {name}', ('name',))
        self.assertEqual(render_prompt(spec, {'name': '{secret}'}).text, 'Hello {secret}')

    def test_content_change_changes_hash(self):
        a = PromptSpec('test', '1', 'A', ())
        b = PromptSpec('test', '1', 'B', ())
        self.assertNotEqual(a.content_hash, b.content_hash)

    def test_attribute_access_rejected(self):
        with self.assertRaises(ValueError):
            PromptSpec('test', '1', '{name.__class__}', ('name',))

    def test_project_runtime_limits_rendered(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import patch
        from app.execution.agent import run
        captured = []
        class LLM:
            async def complete(self, messages, **kwargs):
                captured.extend(messages)
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='done', tool_calls=[]))])
        with patch('app.execution.agent.load_config', return_value={'context': {'max_turns': 7}}):
            asyncio.run(run(LLM(), 'run', [], 'test', lambda event: None))
        self.assertIn('Maximum 7', captured[0]['content'])
        self.assertIn('/workspace', captured[0]['content'])
        self.assertIn('untrusted data', captured[0]['content'])
