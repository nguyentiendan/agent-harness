"""Behavior checks for the extracted backend packages; no external model calls."""
import asyncio
import importlib
import json
import os
import pkgutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

from support import app, store, runtime, workspaces, schemas
from fastapi.testclient import TestClient
from app.agent.harness import Harness
from app.agent.session import AgentSession
from app.agent.state import StateManager
from app.agent.goals import GoalController
from app.agent.types import GoalEvaluation
from app.security.permissions import PermissionManager, PermissionDenied, ApprovalRequired
from app.security.paths import check_bash_paths
from app.security.redaction import redact_text
from app.tools.registry import ToolRegistry
from app.services import commands, approvals, sessions, runs
from app.capabilities import tasks, memory, skills, background, scheduler, teams, workflow


class BackendContracts(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)
        store.sessions.clear()
        runtime.active_sessions.clear()

    async def asyncTearDown(self):
        store.sessions.clear()
        runtime.active_sessions.clear()
        store._save_sessions([])
        self.tmp.cleanup()

    def test_all_packages_import_and_legacy_entry_points_work(self):
        import app as package
        for module in pkgutil.walk_packages(package.__path__, 'app.'):
            importlib.import_module(module.name)
        self.assertIs(importlib.import_module('api_server').app, app)
        self.assertIs(importlib.import_module('harness_core').Harness, Harness)

    def test_http_contracts_and_validation(self):
        client = TestClient(app)
        spec = client.get('/openapi.json').json()
        expected = {
            '/api/config': ['get'], '/api/workspaces': ['get', 'post'],
            '/api/sessions': ['get'], '/api/sessions/{session_id}': ['get', 'delete'],
            '/api/harness/start': ['post'], '/api/harness/stop/{session_id}': ['post'],
            '/api/harness/status/{session_id}': ['get'], '/api/harness/files/{session_id}': ['get'],
            '/api/harness/list/{session_id}': ['get'], '/api/harness/read/{session_id}': ['get'],
            '/api/harness/approvals/{session_id}': ['get'], '/api/harness/approve': ['post'],
            '/api/harness/stream/{session_id}': ['get'], '/api/command': ['post'],
        }
        for path, methods in expected.items():
            for method in methods:
                self.assertIn(method, spec['paths'][path], path)
        self.assertEqual(client.post('/api/harness/start', json={}).status_code, 422)
        self.assertEqual(client.post('/api/workspaces', json={'name': '../outside'}).status_code, 400)
        self.assertEqual(client.get('/api/sessions').status_code, 200)
        self.assertIn('/compact', client.post('/api/command', json={'command':'help'}).json()['output'])
        cursor = spec['paths']['/api/harness/stream/{session_id}']['get']['parameters']
        self.assertTrue(any(p['name'] == 'after' for p in cursor))

    async def test_native_tools_and_path_guards(self):
        registry = ToolRegistry(self.ws)
        self.assertEqual(set(registry.tools), {'bash','read_file','write_file','edit_file','glob','list_dir','todo_write','web_fetch'})
        await registry.execute('write_file', {'path':'nested/a.txt','content':'hello'})
        self.assertEqual(await registry.execute('read_file', {'path':'nested/a.txt'}), 'hello')
        await registry.execute('edit_file', {'path':'nested/a.txt','old_text':'hello','new_text':'world'})
        self.assertEqual(await registry.execute('read_file', {'path':'nested/a.txt'}), 'world')
        self.assertIn('nested/a.txt', await registry.execute('glob', {'pattern':'**/*.txt'}))
        with self.assertRaises(PermissionDenied):
            await registry.execute('read_file', {'path':'../outside'})
        outside = self.ws.parent/'outside-refactor-test'
        (self.ws/'escape').symlink_to(outside)
        with self.assertRaises(PermissionDenied): registry.safe_path('escape')
        self.assertIn('exit_code=0', await registry.execute('bash', {'command':'printf hello'}))
        with patch.dict(os.environ, {'OPENAI_API_KEY':'private-value'}):
            self.assertNotIn('OPENAI_API_KEY', registry.bash_env())
        self.assertIn('non-public', await registry.execute('web_fetch', {'url':'http://127.0.0.1'}))

    def test_permission_pipeline_preserved(self):
        permission = PermissionManager(self.ws, approval_mode='ask')
        self.assertIsInstance(permission.check('bash', {'command':'rm example.txt'}), ApprovalRequired)
        self.assertIn('denied', permission.check('bash', {'command':'sudo ls'}))
        self.assertIn('denied', permission.check('read_file', {'path':'../private.txt'}))
        self.assertIsNone(permission.check('read_file', {'path':'local.txt'}))
        self.assertIsNotNone(check_bash_paths('cd ../other', self.ws, []))

    async def test_approval_broker(self):
        broker = approvals.ApprovalBroker()
        aid = broker.create('s', 'bash', {'command':'rm test'}, 'destructive')
        self.assertEqual(len(broker.list_pending('s')), 1)
        self.assertTrue(broker.resolve(aid, False))
        self.assertFalse(await broker.wait(aid))
        self.assertEqual(broker.list_pending(), [])

    async def test_agent_loop_uses_history_tools_and_checkpoints(self):
        registry = ToolRegistry(self.ws)
        await registry.execute('write_file', {'path':'a.txt','content':'evidence'})
        class LLM:
            def __init__(self): self.requests = []
            async def complete(inner, messages, **kwargs):
                inner.requests.append(list(messages))
                tc = SimpleNamespace(id='tool1', type='function', function=SimpleNamespace(name='read_file', arguments='{"path":"a.txt"}'))
                msg = SimpleNamespace(content='done', tool_calls=[tc] if len(inner.requests)==1 else [])
                return SimpleNamespace(choices=[SimpleNamespace(message=msg)])
        class Evaluator:
            async def evaluate(self, condition, messages): return GoalEvaluation(True,'verified')
        llm=LLM();events=[]
        session=AgentSession(llm, registry, PermissionManager(self.ws), StateManager(self.ws),
                             GoalController(Evaluator()), 'fake', 'system', max_turns=3, emit=events.append)
        result=await session.run('Read it', history=[{'role':'user','content':'earlier'}])
        self.assertEqual(result.status, 'achieved')
        self.assertEqual(llm.requests[0][1]['content'], 'earlier')
        self.assertTrue(any(e['type']=='tool_result' and e['output']=='evidence' for e in events))
        self.assertEqual(StateManager(self.ws).load_state()['turn'], 2)

    async def test_harness_wiring_without_provider_calls(self):
        fake_mcp = SimpleNamespace(initialize_all_servers=AsyncMock(), shutdown=AsyncMock(), get_registered_tool_schemas=lambda: [])
        fake_result = SimpleNamespace(text='done', status='achieved', reason='verified')
        with patch('app.agent.harness.MCPManager', return_value=fake_mcp), patch.object(AgentSession,'run',new=AsyncMock(return_value=fake_result)) as run:
            harness=Harness(config_file=str(self.ws/'missing.yaml'),workspace_path=str(self.ws))
            result=await harness.run('goal', history=[{'role':'user','content':'previous'}])
        self.assertEqual(result.text,'done')
        self.assertEqual(run.call_args.kwargs['history'][0]['content'],'previous')
        fake_mcp.shutdown.assert_awaited_once()

    async def test_capability_registration_and_storage(self):
        registry=ToolRegistry(self.ws)
        task_store=tasks.register(registry,self.ws/'.tasks')
        memory.register(registry,self.ws/'.memory')
        skills.register(registry,self.ws/'skills')
        background.register(registry,self.ws)
        scheduler.register(registry,self.ws/'.schedule.json')
        teams.register(registry,task_store,self.ws/'worktrees',AsyncMock())
        workflow.register(registry,SimpleNamespace(),'fake',self.ws/'.runtime')
        self.assertGreater(len(registry.tools), 15)
        self.assertIn('create_task',registry.tools)
        self.assertIn('remember',registry.tools)
        result=await registry.execute('create_task',{'subject':'Verify refactor','description':'test'})
        self.assertIn('Verify refactor',result)
        self.assertEqual(len(task_store.list()),1)

    def test_persisted_events_redact_secrets(self):
        with patch.dict(os.environ, {'TEST_API_KEY': 'test-sensitive-credential'}):
            store._save_session_events('redaction', [{'type':'assistant', 'text':'test-sensitive-credential'}])
        saved=store.session_events_path('redaction').read_text()
        self.assertNotIn('test-sensitive-credential',saved)
        self.assertIn('[REDACTED]',saved)

    async def test_compact_preserves_dialogue_and_updates_memory(self):
        events=[{'type':'user','text':'keep question'},{'type':'tool_result','output':'x'*1000},{'type':'assistant','text':'keep answer'}]
        store._record_session('compact',goal='title',status='completed',workspace='default')
        store._save_session_events('compact',events)
        runtime.active_sessions['compact']={'status':'completed','events':events}
        result=await commands.run_command(schemas.CommandRequest(session_id='compact',command='compact'))
        self.assertIn('Compacted',result['output'])
        saved=(await sessions.get_session('compact'))['events']
        self.assertEqual(saved[0]['text'],'keep question')
        self.assertEqual(saved[2]['text'],'keep answer')
        self.assertLess(len(saved[1]['output']),1000)
        self.assertEqual(json.loads(store.session_events_path('compact').read_text()),saved)


if __name__=='__main__': unittest.main()
