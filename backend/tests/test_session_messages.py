"""Regression coverage for multi-message sessions; uses an isolated disk store."""
import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

# Reuse the existing test suite's temporary data/workspace configuration.
from support import app, config, store, sessions_service, runtime, runs, workspaces, schemas, HTTPException
from app.services import files


class FakeHarness:
    calls = []

    def __init__(self, **kwargs):
        self.events = kwargs['events']

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        self.events.append({'type': 'assistant', 'text': 'Reply: ' + kwargs['goal']})
        return SimpleNamespace(text='done')

    def request_stop(self):
        pass

    def is_stop_requested(self):
        return False


class SessionMessageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        store.sessions.clear()
        runtime.active_sessions.clear()
        FakeHarness.calls.clear()
        self.mock = patch.object(runs, 'Harness', FakeHarness)
        self.mock.start()

    async def asyncTearDown(self):
        self.mock.stop()
        store.sessions.clear()
        runtime.active_sessions.clear()
        store._save_sessions([])

    async def send(self, sid, text):
        result = await runs.start_harness(schemas.StartHarnessRequest(
            session_id=sid, goal=text, workspace_path='default'))
        await runtime.active_sessions[sid]['task']
        return result

    async def test_followups_keep_history_and_title_after_restart(self):
        await self.send('conversation', 'First message')
        runtime.active_sessions.clear()  # Simulate loading from persisted history.
        second = await self.send('conversation', 'Second message')
        self.assertEqual(len(store.sessions), 1)
        self.assertEqual(store.sessions[0]['goal'], 'First message')
        events = (await sessions_service.get_session('conversation'))['events']
        self.assertEqual([e['text'] for e in events if e['type'] == 'user'],
                         ['First message', 'Second message'])
        self.assertEqual(FakeHarness.calls[-1]['history'], [
            {'role': 'user', 'content': 'First message'},
            {'role': 'assistant', 'content': 'Reply: First message'}])
        response = await runs.stream_progress('conversation', after=second['event_offset'])
        chunks = [chunk async for chunk in response.body_iterator]
        streamed = [json.loads(chunk.removeprefix('data: ').strip()) for chunk in chunks]
        self.assertEqual(streamed[0], {'type': 'user', 'text': 'Second message'})
        self.assertEqual(len(streamed), 3)
        await self.send('separate', 'New conversation')
        self.assertEqual(len(store.sessions), 2)
        self.assertEqual(FakeHarness.calls[-1]['history'], [])

    async def test_followup_in_memory_and_metrics_only_count_current_run(self):
        await self.send('in_memory', 'First')
        runtime.active_sessions['in_memory']['events'].append({'type': 'tool_call', 'name': 'old_tool'})
        await self.send('in_memory', 'Second')
        self.assertEqual(len(FakeHarness.calls[-1]['history']), 2)
        self.assertEqual(store.sessions[0]['metrics']['tool_calls'], 0)
        self.assertEqual(len(store.sessions), 1)

    async def test_workspace_files_available_after_restart(self):
        await self.send('files', 'First')
        ws = workspaces.resolve_workspace('default')
        ws.mkdir(parents=True, exist_ok=True)
        (ws / 'example.txt').write_text('preserved')
        runtime.active_sessions.clear()
        result = await files.read_workspace_file('files', 'example.txt')
        self.assertEqual(result['content'], 'preserved')
        await sessions_service.delete_session('files')
        self.assertTrue((ws / 'example.txt').exists())

    async def test_immediate_stop_persists_terminal_event(self):
        await runs.start_harness(schemas.StartHarnessRequest(session_id='immediate_stop',goal='First'))
        await runs.stop_harness('immediate_stop')
        await asyncio.sleep(0)
        self.assertEqual(store.sessions[0]['status'], 'stopped')
        events=json.loads(store.session_events_path('immediate_stop').read_text())
        self.assertEqual(events[-1]['type'], 'status')
        self.assertEqual(events[-1]['status'], 'stopped')
        await sessions_service.delete_session('immediate_stop')
        self.assertEqual(store.sessions, [])

    async def test_invalid_session_id_rejected(self):
        with self.assertRaises(HTTPException) as raised:
            await self.send('../escape', 'Message')
        self.assertEqual(raised.exception.status_code, 400)

    async def test_finalizing_run_blocks_delete_and_followup(self):
        task = asyncio.create_task(asyncio.sleep(60))
        runtime.active_sessions['finishing'] = {'status': 'stopped', 'task': task}
        try:
            with self.assertRaises(HTTPException) as raised:
                await sessions_service.delete_session('finishing')
            self.assertEqual(raised.exception.status_code, 409)
            with self.assertRaises(HTTPException):
                await self.send('finishing', 'Next')
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def test_running_session_rejects_another_message(self):
        runtime.active_sessions['busy'] = {'status': 'running'}
        with self.assertRaises(HTTPException) as raised:
            await self.send('busy', 'Do not overlap')
        self.assertEqual(raised.exception.status_code, 409)

    async def test_legacy_history_keeps_original_user_message(self):
        store._record_session('legacy', goal='Original', status='completed', workspace='default')
        store._save_session_events('legacy', [{'type': 'assistant', 'text': 'Old reply'}])
        await self.send('legacy', 'Follow-up')
        self.assertEqual(FakeHarness.calls[-1]['history'][0], {'role': 'user', 'content': 'Original'})


if __name__ == '__main__':
    unittest.main()
