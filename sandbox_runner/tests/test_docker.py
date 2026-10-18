"""Opt-in Docker integration: only randomly-namespaced test resources are touched."""

import os
import tempfile
import unittest
import uuid


@unittest.skipUnless(
    os.environ.get("RUN_DOCKER_TESTS") == "1", "Docker integration is opt-in"
)
class DockerIsolationTests(unittest.TestCase):
    def setUp(self):
        from app.runtime import Runtime

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.namespace = "harness-test-" + uuid.uuid4().hex[:10]
        os.environ["RUNNER_NAMESPACE"] = self.namespace
        os.environ["RUNNER_DB"] = self.tmp.name + "/registry.db"
        self.rt = Runtime()
        self.addCleanup(self.rt.client.close)
        self.owner = str(uuid.uuid4())
        self.scope = str(uuid.uuid4())
        self.run = str(uuid.uuid4())
        self.rt.ensure(self.run, self.owner, self.scope)

    def tearDown(self):
        self.rt.sweep(True)
        for v in self.rt.client.volumes.list(
            filters={"label": f"harness.runner={self.namespace}"}
        ):
            v.remove()

    def execute(self, command, timeout=5):
        return self.rt.execute(
            self.run, {"op": "bash", "command": command, "timeout": timeout}
        )

    def test_profile_and_cross_scope_files(self):
        c = self.rt.client.containers.get(self.rt.name(self.run))
        cfg = c.attrs["HostConfig"]
        self.assertEqual(cfg["NetworkMode"], "none")
        self.assertTrue(cfg["ReadonlyRootfs"])
        self.assertEqual(cfg["Memory"], 1073741824)
        self.assertEqual(cfg["PidsLimit"], 128)
        self.assertEqual(cfg["NanoCpus"], 1000000000)
        self.assertEqual(c.attrs["Config"]["User"], "10001:10001")
        self.assertEqual(len(c.attrs["Mounts"]), 1)
        self.assertIn("no-new-privileges:true", cfg["SecurityOpt"])
        self.assertEqual(self.execute("printf secret > marker.txt")["exit_code"], 0)
        self.assertNotEqual(self.execute("touch /outside")["exit_code"], 0)
        self.assertNotEqual(
            self.execute("test -S /var/run/docker.sock")["exit_code"], 0
        )
        self.assertNotIn("LITELLM", self.execute("env")["output"])
        self.assertEqual(self.execute("node --version")["exit_code"], 0)
        self.assertEqual(
            self.execute(
                "python3 -c 'import socket; s=socket.socket(); s.settimeout(1); print(s.connect_ex((\"1.1.1.1\",443)))'"
            )["output"].strip()
            != "0",
            True,
        )
        other = str(uuid.uuid4())
        other_scope = str(uuid.uuid4())
        other_owner = str(uuid.uuid4())
        self.rt.ensure(other, other_owner, other_scope)
        result = self.rt.execute(
            other, {"op": "bash", "command": "test -e marker.txt", "timeout": 5}
        )
        self.assertNotEqual(result["exit_code"], 0)
        with self.assertRaises(Exception):
            self.rt.ensure(self.run, other_owner, self.scope)
        with self.assertRaises(Exception):
            self.rt.files(other_owner, self.scope, {"op": "list_dir"})
        self.rt.destroy(self.run)
        self.rt.destroy(self.run)
        self.assertEqual(
            self.rt.files(
                self.owner, self.scope, {"op": "read_file", "path": "marker.txt"}
            )["content"],
            "secret",
        )
        with self.assertRaises(Exception):
            self.rt.ensure(self.run, self.owner, self.scope)

    def test_timeout_background_cleanup_and_persistence(self):
        self.execute("echo keep > keep.txt")
        result = self.execute("sleep 30 & wait", timeout=1)
        self.assertTrue(result["timed_out"])
        self.assertEqual(
            self.rt.client.containers.list(
                all=True, filters={"name": self.rt.name(self.run)}
            ),
            [],
        )
        self.assertEqual(
            self.rt.files(
                self.owner, self.scope, {"op": "read_file", "path": "keep.txt"}
            )["content"].strip(),
            "keep",
        )

    def test_gateway_symlink_and_restart_orphans(self):
        self.execute("ln -s /etc/passwd escape; mkfifo pipe")
        self.assertIn(
            "error",
            self.rt.files(
                self.owner, self.scope, {"op": "read_file", "path": "escape"}
            ),
        )
        self.assertIn(
            "error",
            self.rt.files(self.owner, self.scope, {"op": "read_file", "path": "pipe"}),
        )
        self.execute("echo retained > retained.txt")
        self.rt.sweep(True)
        self.assertEqual(
            self.rt.client.containers.list(
                all=True, filters={"label": f"harness.runner={self.namespace}"}
            ),
            [],
        )
        self.assertIn(
            "retained",
            self.rt.files(
                self.owner, self.scope, {"op": "read_file", "path": "retained.txt"}
            )["content"],
        )
        self.rt.purge(self.owner, self.scope)
        self.rt.purge(self.owner, self.scope)

    def test_output_flood_is_bounded(self):
        result = self.execute("python3 -c 'print(\"x\" * 200000)' ")
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(result["output"]), 65536)

    def test_memory_limit_and_missing_container_do_not_replay(self):
        from app.runtime import RunnerError

        try:
            result = self.execute(
                "python3 -c 'a=bytearray(1536*1024*1024)'", timeout=15
            )
            self.assertNotEqual(result["exit_code"], 0)
        except RunnerError:
            pass  # OOM may kill the worker as well as its child.
        self.rt.client.containers.get(self.rt.name(self.run)).remove(force=True)
        with self.assertRaises(RunnerError):
            self.rt.ensure(self.run, self.owner, self.scope)
