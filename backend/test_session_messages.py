"""Regression coverage for multi-message sessions; uses an isolated disk store."""
import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

# Reuse the existing test suite's temporary data/workspace configuration.
from test_delete_session import api_server


class FakeHarness:
    calls = []

    def __init__(self, **kwargs):
        self.events = kwargs['events']

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        self.events.append({'type': 'assistant', 'text': 'Reply: ' + kwargs['goal']})
        return SimpleNamespace(text='done')

    def is_stop_requested(self):
        return False


class SessionMessageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        api_server.sessions.clear()
        api_server.active_sessions.clear()
        FakeHarness.calls.clear()
        self.mock = patch.object(api_server, 'Harness', FakeHarness)
        self.mock.start()

    async def asyncTearDown(self):
        self.mock.stop()
        api_server.sessions.clear()
        api_server.active_sessions.clear()
        api_server._save_sessions([])

    async def send(self, sid, text):
        result = await api_server.start_harness(api_server.StartHarnessRequest(
            session_id=sid, goal=text, workspace_path='default'))
        await api_server.active_sessions[sid]['task']
        return result

    async def test_followups_keep_history_and_title_after_restart(self):
        await self.send('conversation', 'First message')
        api_server.active_sessions.clear()  # Simulate loading from persisted history.
        second = await self.send('conversation', 'Second message')
        self.assertEqual(len(api_server.sessions), 1)
        self.assertEqual(api_server.sessions[0]['goal'], 'First message')
        events = (await api_server.get_session('conversation'))['events']
        self.assertEqual([e['text'] for e in events if e['type'] == 'user'],
                         ['First message', 'Second message'])
        self.assertEqual(FakeHarness.calls[-1]['history'], [
            {'role': 'user', 'content': 'First message'},
            {'role': 'assistant', 'content': 'Reply: First message'}])
        response = await api_server.stream_progress('conversation', after=second['event_offset'])
        chunks = [chunk async for chunk in response.body_iterator]
        streamed = [json.loads(chunk.removeprefix('data: ').strip()) for chunk in chunks]
        self.assertEqual(streamed[0], {'type': 'user', 'text': 'Second message'})
        self.assertEqual(len(streamed), 3)
        await self.send('separate', 'New conversation')
        self.assertEqual(len(api_server.sessions), 2)
        self.assertEqual(FakeHarness.calls[-1]['history'], [])

    async def test_followup_in_memory_and_metrics_only_count_current_run(self):
        await self.send('in_memory', 'First')
        api_server.active_sessions['in_memory']['events'].append({'type': 'tool_call', 'name': 'old_tool'})
        await self.send('in_memory', 'Second')
        self.assertEqual(len(FakeHarness.calls[-1]['history']), 2)
        self.assertEqual(api_server.sessions[0]['metrics']['tool_calls'], 0)
        self.assertEqual(len(api_server.sessions), 1)

    async def test_workspace_files_available_after_restart(self):
        await self.send('files', 'First')
        ws = api_server.resolve_workspace('default')
        ws.mkdir(parents=True, exist_ok=True)
        (ws / 'example.txt').write_text('preserved')
        api_server.active_sessions.clear()
        result = await api_server.read_workspace_file('files', 'example.txt')
        self.assertEqual(result['content'], 'preserved')
        await api_server.delete_session('files')
        self.assertTrue((ws / 'example.txt').exists())

    async def test_invalid_session_id_rejected(self):
        with self.assertRaises(api_server.HTTPException) as raised:
            await self.send('../escape', 'Message')
        self.assertEqual(raised.exception.status_code, 400)

    async def test_finalizing_run_blocks_delete_and_followup(self):
        task = asyncio.create_task(asyncio.sleep(60))
        api_server.active_sessions['finishing'] = {'status': 'stopped', 'task': task}
        try:
            with self.assertRaises(api_server.HTTPException) as raised:
                await api_server.delete_session('finishing')
            self.assertEqual(raised.exception.status_code, 409)
            with self.assertRaises(api_server.HTTPException):
                await self.send('finishing', 'Next')
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def test_running_session_rejects_another_message(self):
        api_server.active_sessions['busy'] = {'status': 'running'}
        with self.assertRaises(api_server.HTTPException) as raised:
            await self.send('busy', 'Do not overlap')
        self.assertEqual(raised.exception.status_code, 409)

    async def test_legacy_history_keeps_original_user_message(self):
        api_server._record_session('legacy', goal='Original', status='completed', workspace='default')
        api_server._save_session_events('legacy', [{'type': 'assistant', 'text': 'Old reply'}])
        await self.send('legacy', 'Follow-up')
        self.assertEqual(FakeHarness.calls[-1]['history'][0], {'role': 'user', 'content': 'Original'})


if __name__ == '__main__':
    unittest.main()
