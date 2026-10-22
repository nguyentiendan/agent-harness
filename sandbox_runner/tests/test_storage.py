import os
import tempfile
import unittest
import uuid
from unittest.mock import MagicMock, patch
from app.runtime import Runtime, RunnerError


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        with patch.dict(os.environ, {"RUNNER_DB": self.tmp.name + "/db"}):
            self.rt = Runtime(client=MagicMock())
        self.owner, self.sid = str(uuid.uuid4()), str(uuid.uuid4())
        self.binding = dict(
            mode="local", local_path='/tmp/my folder "literal"', revision=1
        )

    def test_bind_mount_never_uses_auto_create_volume(self):
        from app.storage import mount_options

        opts = mount_options(self.binding, "unused", False)
        self.assertNotIn("volumes", opts)
        self.assertEqual(opts["mounts"][0]["Type"], "bind")
        self.assertEqual(opts["mounts"][0]["Source"], self.binding["local_path"])
        self.assertEqual(opts["mounts"][0]["Target"], "/workspace")

    def test_revision_owner_and_busy_guards(self):
        self.rt.configure(self.owner, self.sid, self.binding)
        self.assertEqual(self.rt.binding_for(self.owner, self.sid, 1), self.binding)
        self.rt.configure(self.owner, self.sid, self.binding)
        for changed in [
            dict(self.binding, revision=0),
            dict(self.binding, local_path="/other"),
        ]:
            with self.assertRaises(RunnerError):
                self.rt.configure(self.owner, self.sid, changed)
        with self.assertRaises(RunnerError):
            self.rt.binding_for(self.owner, self.sid, 0)
        with self.assertRaises(RunnerError):
            self.rt.configure(str(uuid.uuid4()), self.sid, self.binding)
        with self.rt.db() as db:
            db.execute(
                "INSERT INTO runs(id,scope,owner,closed,expires) VALUES(?,?,?,0,9999999999)",
                (str(uuid.uuid4()), self.sid, self.owner),
            )
        self.rt.configure(self.owner, self.sid, self.binding)
        with self.assertRaises(RunnerError):
            self.rt.configure(self.owner, self.sid, dict(self.binding, revision=2))

    def test_local_ensure_pins_revision_and_never_creates_volume(self):
        self.rt.configure(self.owner, self.sid, self.binding)
        self.rt.ensure(str(uuid.uuid4()), self.owner, self.sid, 1)
        self.rt.client.volumes.create.assert_not_called()
        self.assertIn("mounts", self.rt.client.containers.run.call_args.kwargs)

    def test_probe_cleanup_on_failure(self):
        from app.storage import probe_local

        helper = self.rt.client.containers.run.return_value
        helper.wait.return_value = {"StatusCode": 1}
        with self.assertRaises(ValueError):
            probe_local(self.rt.client, "image", self.binding)
        helper.remove.assert_called_once_with(force=True)
        opts = self.rt.client.containers.run.call_args.kwargs
        self.assertEqual(opts["user"], "10001:10001")
        self.assertEqual(opts["network_mode"], "none")

    def test_missing_source_never_falls_back(self):
        self.rt.configure(self.owner, self.sid, self.binding)
        self.rt.client.containers.run.side_effect = RuntimeError("source missing")
        rid = str(uuid.uuid4())
        with self.assertRaises(RuntimeError):
            self.rt.ensure(rid, self.owner, self.sid, 1)
        with self.rt.db() as db:
            self.assertEqual(
                db.execute("SELECT closed FROM runs WHERE id=?", (rid,)).fetchone()[0],
                1,
            )
        self.rt.client.volumes.create.assert_not_called()

    def test_cancel_before_validate_tombstones_operation(self):
        from docker.errors import NotFound

        operation = str(uuid.uuid4())
        self.rt.client.containers.get.side_effect = NotFound("absent")
        self.rt.release_validation(self.owner, self.sid, operation)
        with self.assertRaises(RunnerError):
            self.rt.validate_workspace(self.binding, self.owner, self.sid, operation)
        self.rt.client.containers.run.assert_not_called()

    def test_failed_probe_cleanup_blocks_scope_until_acknowledged(self):
        from docker.errors import NotFound

        operation = str(uuid.uuid4())
        self.rt.client.containers.get.side_effect = RuntimeError("transport lost")
        with self.assertRaises(RuntimeError):
            self.rt.release_validation(self.owner, self.sid, operation)
        with self.assertRaises(RunnerError):
            self.rt.configure(self.owner, self.sid, self.binding)
        with self.assertRaises(RunnerError):
            self.rt.ensure(str(uuid.uuid4()), self.owner, self.sid)
        self.rt.client.containers.get.side_effect = NotFound("absent")
        self.rt.release_validation(self.owner, self.sid, operation)
        self.rt.configure(self.owner, self.sid, self.binding)

    def test_failed_file_helper_cleanup_keeps_scope_busy(self):
        from app.transfers import helper

        self.rt.configure(self.owner, self.sid, self.binding)
        container = self.rt.client.containers.run.return_value
        container.remove.side_effect = RuntimeError("cleanup failed")
        with self.assertRaises(RuntimeError):
            with helper(self.rt, self.owner, self.sid, workspace_revision=1):
                pass
        with self.assertRaises(RunnerError):
            self.rt.configure(self.owner, self.sid, dict(self.binding, revision=2))
        container.remove.side_effect = None
        self.rt.sweep()
        self.rt.configure(self.owner, self.sid, dict(self.binding, revision=2))
