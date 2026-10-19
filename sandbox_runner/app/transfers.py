"""Bounded binary copies via dedicated helpers, never host workspace paths."""

import time
import threading
from contextlib import contextmanager
from docker.errors import NotFound
from .runtime import RunnerError

LIMIT = 25 * 1024 * 1024


@contextmanager
def helper(rt, owner, sid, write=False):
    if not rt.helpers.acquire(blocking=False):
        raise RunnerError("File gateway is busy.")
    container = None
    timer = None
    try:
        # Serialize against ensure/purge while the helper has access to scope files.
        with rt.lock, rt.db() as db:
            rt.scope(db, owner, sid)
            if (
                write
                and db.execute(
                    "SELECT id FROM runs WHERE scope=? AND closed=0", (sid,)
                ).fetchone()
            ):
                raise RunnerError("Scope has a running agent.")
            try:
                volume = rt.client.volumes.get(rt.volume(sid))
            except NotFound:
                if not write:
                    raise RunnerError("File not found.")
                volume = rt.client.volumes.create(
                    name=rt.volume(sid),
                    labels={**rt.labels(), "owner": owner, "scope": sid},
                )
            if volume.attrs.get("Labels", {}).get("owner") != owner:
                raise RunnerError("Volume ownership mismatch.")
            container = rt.client.containers.run(
                rt.client.images.get(rt.image).id,
                ["sleep", "90"],
                detach=True,
                labels={
                    **rt.labels(),
                    "helper": "true",
                    "expires": str(time.time() + 90),
                },
                **rt.options(volume.name, readonly=not write),
            )

            def kill():
                try:
                    container.remove(force=True)
                except NotFound:
                    pass

            timer = threading.Timer(60, kill)
            timer.daemon = True
            timer.start()
            yield container
    finally:
        if timer:
            timer.cancel()
        try:
            if container:
                try:
                    container.remove(force=True)
                except NotFound:
                    pass
        finally:
            rt.helpers.release()


def checked(result):
    if "error" in result:
        raise RunnerError(result["error"])
    return result


def upload(rt, owner, sid, path, source):
    source.seek(0, 2)
    size = source.tell()
    source.seek(0)
    if size > LIMIT:
        raise RunnerError("File exceeds 25 MiB.")
    with helper(rt, owner, sid, True) as c:
        # Docker archive PUT rejects readonly rootfs even for /tmp tmpfs.
        # Fixed helper program reads exactly the bounded body through exec stdin.
        program = "import sys\nn=int(sys.argv[1])\nwith open('/tmp/upload','xb') as f:\n while n:\n  b=sys.stdin.buffer.read(min(n,65536))\n  if not b: raise RuntimeError('Incomplete upload')\n  f.write(b);n-=len(b)\n"
        eid = rt.client.api.exec_create(
            c.id,
            ["python3", "-I", "-c", program, str(size)],
            stdin=True,
            stdout=True,
            stderr=True,
            user="10001:10001",
        )["Id"]
        stream = rt.client.api.exec_start(eid, socket=True)
        sock = getattr(stream, "_sock", stream)
        try:
            sock.settimeout(20)
            while chunk := source.read(65536):
                sock.sendall(chunk)
            # No EOF is necessary: the helper knows the exact byte count.
            received = 0
            while chunk := sock.recv(65536):
                received += len(chunk)
                if received > 65536:
                    raise RunnerError("Upload staging failed.")
        finally:
            stream.close()
        if rt.client.api.exec_inspect(eid)["ExitCode"] != 0:
            raise RunnerError("Upload staging failed.")
        return checked(rt.invoke(c, {"op": "import_file", "path": path}))


def download(rt, owner, sid, path, destination):
    with helper(rt, owner, sid) as c:
        result = checked(rt.invoke(c, {"op": "export_file", "path": path}))
        # Docker archive GET does not see tmpfs contents; stream from the fixed staging file.
        eid = rt.client.api.exec_create(
            c.id,
            [
                "python3",
                "-I",
                "-c",
                "import shutil,sys; shutil.copyfileobj(open('/tmp/download','rb'),sys.stdout.buffer,65536)",
            ],
            stdout=True,
            stderr=False,
            user="10001:10001",
        )["Id"]
        stream = rt.client.api.exec_start(eid, stream=True)
        count = 0
        try:
            for chunk in stream:
                count += len(chunk)
                if count > LIMIT:
                    raise RunnerError("Download exceeded limit.")
                destination.write(chunk)
        finally:
            stream.close()
        if rt.client.api.exec_inspect(eid)["ExitCode"] != 0 or count != result["bytes"]:
            raise RunnerError("Incomplete download.")
        destination.seek(0)
        return result


def delete(rt, owner, sid, path):
    with helper(rt, owner, sid, True) as c:
        return checked(rt.invoke(c, {"op": "delete_file", "path": path}))
