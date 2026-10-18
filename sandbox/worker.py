"""Runs only inside an unprivileged sandbox/helper. Never imports platform secrets."""

import json
import os
from pathlib import PurePosixPath
import selectors
import signal
import stat
import subprocess
import sys
import time

MAX_FILE = 1024 * 1024
MAX_WRITE = 60000


def parent_fd(root, path, create=False):
    parts = PurePosixPath(path).parts
    if (
        not parts
        or path.startswith("/")
        or any(p in (".", "..") for p in parts)
        or "\x00" in path
    ):
        raise ValueError("Use a relative path inside /workspace.")
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            if create:
                try:
                    os.mkdir(part, dir_fd=fd)
                except FileExistsError:
                    pass
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        return fd, parts[-1]
    except BaseException:
        os.close(fd)
        raise


def file_op(root, op, path="", content="", old="", new=""):
    if op == "list_dir":
        files = []
        seen = 0
        # fwalk keeps directory descriptors stable; symlink targets are never followed.
        for directory, dirs, names, fd in os.fwalk(root, follow_symlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for name in sorted(names):
                seen += 1
                if seen > 10000 or len(files) >= 500:
                    return {"files": files, "truncated": True}
                s = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISREG(s.st_mode):
                    files.append(
                        {
                            "path": os.path.relpath(
                                os.path.join(directory, name), root
                            ),
                            "size": s.st_size,
                        }
                    )
        return {"files": files, "truncated": False}
    if op not in ("read_file", "write_file", "edit_file"):
        raise ValueError("Unknown file operation.")
    parent, name = parent_fd(root, path, create=op == "write_file")
    try:
        flags = (
            os.O_RDWR | os.O_CREAT
            if op == "write_file"
            else os.O_RDWR
            if op == "edit_file"
            else os.O_RDONLY
        )
        fd = os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=parent)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("Only regular, non-linked files are supported.")
            if op in ("read_file", "edit_file"):
                raw = os.read(fd, MAX_FILE + 1)
                if b"\0" in raw:
                    raise ValueError("Binary file preview is unavailable.")
                text = raw[:MAX_FILE].decode("utf-8")
                if op == "read_file":
                    return {
                        "path": path,
                        "content": text,
                        "truncated": len(raw) > MAX_FILE,
                    }
                if len(raw) > MAX_FILE:
                    raise ValueError("File too large to edit.")
                if not old or text.count(old) != 1:
                    raise ValueError("Old text must occur exactly once.")
                content = text.replace(old, new, 1)
            data = content.encode("utf-8")
            if len(data) > MAX_WRITE:
                raise ValueError(
                    "Write exceeds 60000 bytes; write smaller sections using bash."
                )
            os.lseek(fd, 0, os.SEEK_SET)
            os.ftruncate(fd, 0)
            with os.fdopen(os.dup(fd), "wb") as out:
                out.write(data)
            return {"path": path, "bytes": len(data)}
        finally:
            os.close(fd)
    finally:
        os.close(parent)


def shell(command, timeout):
    process = subprocess.Popen(
        ["bash", "-c", command],
        cwd="/workspace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env={
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": "/tmp",
            "LANG": "C.UTF-8",
            "TMPDIR": "/tmp",
        },
    )
    sel = selectors.DefaultSelector()
    sel.register(process.stdout, selectors.EVENT_READ)
    data = bytearray()
    deadline = time.monotonic() + timeout
    timed_out = False
    truncated = False
    try:
        while sel.get_map():
            if time.monotonic() >= deadline:
                timed_out = True
                break
            for key, _ in sel.select(min(0.2, max(0, deadline - time.monotonic()))):
                chunk = os.read(key.fileobj.fileno(), 8192)
                if not chunk:
                    sel.unregister(key.fileobj)
                    continue
                room = 65536 - len(data)
                data.extend(chunk[:room])
                truncated |= len(chunk) > room
        if not timed_out:
            try:
                process.wait(timeout=max(0.01, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                timed_out = True
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        sel.close()
        process.stdout.close()
    return {
        "output": data.decode("utf-8", errors="replace"),
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "truncated": truncated,
    }


if __name__ == "__main__":
    try:
        payload = json.loads(sys.argv[1])
        op = payload.pop("op")
        result = (
            shell(**payload) if op == "bash" else file_op("/workspace", op, **payload)
        )
        print(json.dumps(result, ensure_ascii=True))
    except (ValueError, OSError, UnicodeError) as exc:
        print(json.dumps({"error": str(exc)[:500]}))
        sys.exit(2)
