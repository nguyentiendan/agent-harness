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

    def test_binary_upload_download_and_delete(self):
        from app import transfers
        from app.runtime import RunnerError
        import tempfile, hashlib

        self.rt.destroy(self.run)
        with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as dest:
            source.write(b"\x00\xffhello binary")
            transfers.upload(
                self.rt, self.owner, self.scope, "nested/report.bin", source
            )
            transfers.download(
                self.rt, self.owner, self.scope, "nested/report.bin", dest
            )
            self.assertEqual(dest.read(), b"\x00\xffhello binary")
            with self.assertRaises(RunnerError):
                transfers.download(
                    self.rt, str(uuid.uuid4()), self.scope, "nested/report.bin", dest
                )
            with self.assertRaises(RunnerError):
                transfers.upload(self.rt, self.owner, self.scope, "../escape", source)
            transfers.delete(self.rt, self.owner, self.scope, "nested/report.bin")
            with self.assertRaises(RunnerError):
                transfers.download(
                    self.rt, self.owner, self.scope, "nested/report.bin", dest
                )
            source.seek(0)
            source.truncate(25 * 1024 * 1024)
            transfers.upload(self.rt, self.owner, self.scope, "limit.bin", source)
            dest.seek(0)
            dest.truncate()
            transfers.download(self.rt, self.owner, self.scope, "limit.bin", dest)
            dest.seek(0, 2)
            self.assertEqual(dest.tell(), 25 * 1024 * 1024)
            source.truncate(25 * 1024 * 1024 + 1)
            with self.assertRaises(RunnerError):
                transfers.upload(self.rt, self.owner, self.scope, "too-big.bin", source)


@unittest.skipUnless(
    os.environ.get("RUN_DOCKER_TESTS") == "1", "Docker integration is opt-in"
)
class LocalWorkspaceTests(unittest.TestCase):
    def test_host_folder_lifecycle(self):
        from pathlib import Path
        from unittest.mock import patch
        import io
        from app.runtime import Runtime, RunnerError
        from app import transfers

        root = os.environ.get("HOST_TEST_ROOT")
        if not root or not Path(root).is_dir():
            self.fail(
                "HOST_TEST_ROOT must name an explicit disposable host test directory"
            )
        with (
            tempfile.TemporaryDirectory(prefix="local folder ", dir=root) as folder,
            tempfile.TemporaryDirectory() as state,
        ):
            source = Path(folder)
            sentinel = source / "sentinel.txt"
            sentinel.write_text("keep")
            # Only this disposable fixture is made writable to the sandbox UID.
            source.chmod(0o777)
            sentinel.chmod(0o666)
            namespace = "harness-test-" + uuid.uuid4().hex[:10]
            with patch.dict(
                os.environ, {"RUNNER_DB": state + "/db", "RUNNER_NAMESPACE": namespace}
            ):
                rt = Runtime()
            owner, sid, other = (str(uuid.uuid4()) for _ in range(3))
            binding = dict(mode="local", local_path=folder, revision=1)
            try:
                # Preserve the managed volume when switching to and from a local folder.
                initial = str(uuid.uuid4())
                rt.ensure(initial, owner, sid)
                rt.execute(
                    initial,
                    dict(op="write_file", path="managed.txt", content="original"),
                )
                rt.destroy(initial)
                rt.validate_workspace(binding)
                self.assertEqual(list(source.glob(".harness-probe-*")), [])
                rt.configure(owner, sid, binding)
                rt.configure(owner, other, binding)  # deliberate same-folder choice
                rid = str(uuid.uuid4())
                rt.ensure(rid, owner, sid, 1)
                result = rt.execute(
                    rid, dict(op="write_file", path="agent.txt", content="local")
                )
                self.assertNotIn("error", result)
                self.assertEqual((source / "agent.txt").read_text(), "local")
                with self.assertRaises(RunnerError):
                    rt.configure(
                        owner, sid, dict(mode="managed", local_path=None, revision=2)
                    )
                rt.destroy(rid)
                transfers.upload(
                    rt, owner, sid, "upload.bin", io.BytesIO(b"\x00demo"), 1
                )
                out = io.BytesIO()
                transfers.download(rt, owner, other, "upload.bin", out, 1)
                self.assertEqual(out.read(), b"\x00demo")
                transfers.delete(rt, owner, sid, "upload.bin", 1)
                self.assertFalse((source / "upload.bin").exists())
                rt.configure(
                    owner, sid, dict(mode="managed", local_path=None, revision=2)
                )
                self.assertEqual(
                    rt.files(owner, sid, dict(op="read_file", path="managed.txt"), 2)[
                        "content"
                    ],
                    "original",
                )
                with self.assertRaises(RunnerError):
                    rt.files(owner, sid, dict(op="list_dir"), 1)
                rt.configure(owner, sid, dict(binding, revision=3))
                rid = str(uuid.uuid4())
                rt.ensure(rid, owner, sid, 3)
                self.assertTrue(
                    rt.execute(rid, dict(op="bash", command="sleep 30", timeout=1))[
                        "timed_out"
                    ]
                )
                rid = str(uuid.uuid4())
                rt.ensure(rid, owner, sid, 3)
                rt.sweep(True)
                self.assertEqual(sentinel.read_text(), "keep")
                (source / "escape").symlink_to("/etc/passwd")
                self.assertIn(
                    "error",
                    rt.files(owner, sid, dict(op="read_file", path="escape"), 3),
                )
                moved = source.with_name(source.name + " moved")
                source.rename(moved)
                try:
                    with self.assertRaises(Exception):
                        rt.ensure(str(uuid.uuid4()), owner, sid, 3)
                    self.assertFalse(source.exists())
                finally:
                    moved.rename(source)
                rt.purge(owner, sid)
                rt.purge(owner, other)
                self.assertEqual(sentinel.read_text(), "keep")
                self.assertTrue((source / "agent.txt").exists())
            finally:
                rt.sweep(True)
                for v in rt.client.volumes.list(
                    filters={"label": f"harness.runner={namespace}"}
                ):
                    v.remove()
                rt.client.close()
