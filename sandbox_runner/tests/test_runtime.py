import os
import tempfile
import unittest
import uuid
from unittest.mock import MagicMock, patch
from app.runtime import Runtime, RunnerError


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        with patch.dict(os.environ, {"RUNNER_DB": self.tmp.name + "/db"}):
            self.rt = Runtime(client=MagicMock())

    def test_gateway_rejects_excess_helpers(self):
        self.rt.helpers.acquire()
        self.rt.helpers.acquire()
        with self.assertRaises(RunnerError):
            self.rt.files(str(uuid.uuid4()), str(uuid.uuid4()), {"op": "list_dir"})
        self.rt.client.containers.run.assert_not_called()

    def test_registry_failure_still_kills_container(self):
        container = self.rt.client.containers.get.return_value
        container.labels = {"harness.runner": self.rt.namespace}
        with patch.object(self.rt, "db", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.rt.destroy(str(uuid.uuid4()))
        container.remove.assert_called_once_with(force=True)


class TransferCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_waits_for_copy_before_closing_spool(self):
        import asyncio, threading

        with patch.dict(os.environ, {"RUNNER_TOKEN": "test-token-" * 8}):
            from app import main
        done = threading.Event()

        def worker():
            import time

            time.sleep(0.04)
            done.set()

        task = asyncio.create_task(main.run_sync(worker))
        await asyncio.sleep(0.01)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(done.is_set())
