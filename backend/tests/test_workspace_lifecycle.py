import support
import unittest
import asyncio
import threading
from unittest.mock import patch
import test_execution
from app.platform.db import DB, ExecutionScope, Run
from app.execution.lifecycle import acquire, prepare
from app.execution.operations import run_sync


class WorkspaceLifecycleTests(unittest.IsolatedAsyncioTestCase):
    setUp = test_execution.ExecutionTests.setUp

    def test_snapshot_pinned_before_execution(self):
        with DB.begin() as db:
            run = db.get(Run, self.rid)
            ctx = acquire(db, run)
            scope = db.get(ExecutionScope, ctx["scope_id"])
            scope.workspace_mode = "local"
            scope.local_path = "/host/project"
            scope.workspace_revision = 3
        with DB.begin() as db:
            # Explicitly make a new admission snapshot for this fixture.
            run = db.get(Run, self.rid)
            run.workspace_snapshot = None
            acquire(db, run)
        with patch("app.execution.client.RunnerClient") as client:
            prepare(self.rid)
            client.return_value.configure.assert_called_once()
            self.assertEqual(
                client.return_value.configure.call_args.args[2]["revision"], 3
            )
            self.assertEqual(
                client.return_value.ensure.call_args.args[0]["workspace_revision"], 3
            )


class CancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_waits_until_worker_finishes(self):
        import time

        done = threading.Event()

        def worker():
            time.sleep(0.04)
            done.set()

        task = asyncio.create_task(run_sync(worker))
        await asyncio.sleep(0.01)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(done.is_set())
