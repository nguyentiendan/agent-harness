import importlib.util
import os
from pathlib import Path
import tempfile
import unittest


class SafeFilesTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "worker", Path(__file__).parents[2] / "sandbox/worker.py"
        )
        self.worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.worker)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "files"
        self.root.mkdir()

    def call(self, op, **kw):
        return self.worker.file_op(self.root, op, **kw)

    def test_write_read_edit_list(self):
        self.call("write_file", path="dir/a.txt", content="hello")
        self.assertEqual(self.call("read_file", path="dir/a.txt")["content"], "hello")
        self.call("edit_file", path="dir/a.txt", old="hello", new="world")
        self.assertEqual(self.call("list_dir")["files"][0]["path"], "dir/a.txt")

    def test_traversal_symlink_and_fifo(self):
        outside = Path(self.tmp.name) / "secret"
        outside.write_text("private")
        (self.root / "link").symlink_to(outside)
        os.mkfifo(self.root / "fifo")
        for path in ("../secret", "/etc/passwd", "link", "fifo"):
            with self.assertRaises((ValueError, OSError)):
                self.call("read_file", path=path)
        with self.assertRaises((ValueError, OSError)):
            self.call("write_file", path="link", content="bad")
        self.assertEqual(outside.read_text(), "private")

    def test_limits_and_binary(self):
        (self.root / "binary").write_bytes(b"\x00\xff")
        with self.assertRaises(ValueError):
            self.call("read_file", path="binary")
        with self.assertRaises(ValueError):
            self.call("write_file", path="large", content="x" * 70000)

    def test_binary_transfer_atomic_limit_and_symlink(self):
        upload = __import__("pathlib").Path(self.tmp.name) / "upload"
        download = __import__("pathlib").Path(self.tmp.name) / "download"
        upload.write_bytes(b"\x00\xffbinary")
        self.worker.transfer_file(self.root, "import_file", "data.bin", str(upload))
        self.worker.transfer_file(self.root, "export_file", "data.bin", str(download))
        self.assertEqual(download.read_bytes(), upload.read_bytes())
        with upload.open("wb") as f:
            f.truncate(25 * 1024 * 1024 + 1)
        with self.assertRaises(ValueError):
            self.worker.transfer_file(self.root, "import_file", "data.bin", str(upload))
        self.assertEqual((self.root / "data.bin").read_bytes(), b"\x00\xffbinary")
        (self.root / "escape").symlink_to(upload)
        with self.assertRaises((ValueError, OSError)):
            self.worker.transfer_file(self.root, "export_file", "escape", str(download))
        self.worker.transfer_file(self.root, "delete_file", "data.bin", "")
        self.assertFalse((self.root / "data.bin").exists())
