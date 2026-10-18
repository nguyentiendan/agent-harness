"""Restricted Docker runtime with immutable run bindings and durable tombstones."""

from contextlib import contextmanager
import json
import os
import re
import sqlite3
import shutil
import threading
import time
import uuid
import docker
from docker.errors import NotFound
from docker.types import LogConfig


class RunnerError(Exception):
    pass


def identifier(value):
    if str(uuid.UUID(value)) != value:
        raise ValueError("Invalid canonical UUID.")
    return value


class Runtime:
    def __init__(self, client=None):
        self.client = client or docker.from_env(timeout=15)
        self.image = os.environ.get("SANDBOX_IMAGE", "harness-sandbox:local")
        self.namespace = os.environ.get("RUNNER_NAMESPACE", "harness")
        if not re.fullmatch("[a-z0-9-]{1,40}", self.namespace):
            raise ValueError("Invalid namespace")
        self.path = os.environ.get("RUNNER_DB", "/state/registry.db")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.lock = threading.RLock()
        self.busy = set()
        self.helpers = threading.BoundedSemaphore(2)
        with self.db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS scopes (id TEXT PRIMARY KEY, owner TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, scope TEXT, owner TEXT, closed INTEGER NOT NULL, expires REAL NOT NULL)"
            )

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def labels(self):
        return {"harness.runner": self.namespace}

    def name(self, rid):
        return f"{self.namespace}-run-{identifier(rid)}"

    def volume(self, sid):
        return f"{self.namespace}-files-{identifier(sid)}"

    def scope(self, db, owner, sid):
        identifier(owner)
        identifier(sid)
        row = db.execute(
            "SELECT owner,deleted FROM scopes WHERE id=?", (sid,)
        ).fetchone()
        if row and (row[0] != owner or row[1]):
            raise RunnerError("Scope is unavailable.")
        if not row:
            db.execute("INSERT INTO scopes(id,owner) VALUES(?,?)", (sid, owner))

    def options(self, volume, readonly=False):
        return dict(
            user="10001:10001",
            working_dir="/workspace",
            network_mode="none",
            read_only=True,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            mem_limit="1g",
            memswap_limit="1g",
            nano_cpus=1000000000,
            pids_limit=128,
            tmpfs={"/tmp": "rw,noexec,nosuid,nodev,size=67108864,mode=1777"},
            volumes={
                volume: {"bind": "/workspace", "mode": "ro" if readonly else "rw"}
            },
            environment={"HOME": "/tmp", "LANG": "C.UTF-8"},
            log_config=LogConfig(type="none"),
            init=True,
        )

    def ensure(self, rid, owner, sid):
        identifier(rid)
        with self.lock, self.db() as db:
            self.scope(db, owner, sid)
            row = db.execute(
                "SELECT scope,owner,closed,expires FROM runs WHERE id=?", (rid,)
            ).fetchone()
            if row:
                if row[:2] != (sid, owner) or row[2] or row[3] <= time.time():
                    raise RunnerError("Run is unavailable.")
                try:
                    container = self.client.containers.get(self.name(rid))
                    if container.status != "running":
                        raise RunnerError("Sandbox exited; start a new run.")
                    return {"ready": True}
                except NotFound:
                    raise RunnerError("Sandbox was lost; start a new run.")
            if shutil.disk_usage(os.path.dirname(self.path)).free < 1024**3:
                raise RunnerError(
                    "Sandbox storage reserve is low; new runs are blocked."
                )
            # Image ID is immutable for this run even if a local tag changes later.
            image = self.client.images.get(self.image).id
            volume = self.volume(sid)
            try:
                v = self.client.volumes.get(volume)
                if (
                    v.attrs.get("Labels", {}).get("owner") != owner
                    or v.attrs.get("Labels", {}).get("harness.runner") != self.namespace
                ):
                    raise RunnerError("Volume ownership mismatch.")
            except NotFound:
                self.client.volumes.create(
                    name=volume, labels={**self.labels(), "owner": owner, "scope": sid}
                )
            expires = time.time() + 900
            db.execute(
                "INSERT INTO runs VALUES(?,?,?,?,?)", (rid, sid, owner, 0, expires)
            )
            # Commit registry BEFORE Docker create: restart cleanup can find uncertain creates.
            db.commit()
            try:
                self.client.containers.run(
                    image,
                    ["sleep", "900"],
                    name=self.name(rid),
                    detach=True,
                    labels={**self.labels(), "run": rid, "expires": str(expires)},
                    **self.options(volume),
                )
            except Exception:
                db.execute("UPDATE runs SET closed=1 WHERE id=?", (rid,))
                raise
            return {"ready": True}

    def destroy(self, rid):
        identifier(rid)
        with self.lock:
            registry_error = None
            try:
                with self.db() as db:
                    db.execute(
                        "INSERT INTO runs(id,closed,expires) VALUES(?,1,?) ON CONFLICT(id) DO UPDATE SET closed=1",
                        (rid, time.time()),
                    )
            except Exception as error:
                registry_error = error
            # Even a full registry disk must not prevent killing execution.
            try:
                c = self.client.containers.get(self.name(rid))
                if c.labels.get("harness.runner") != self.namespace:
                    raise RunnerError("Container ownership mismatch.")
                c.remove(force=True)
            except NotFound:
                pass
            if registry_error:
                raise registry_error
        return {"removed": True}

    def execute(self, rid, payload):
        identifier(rid)
        with self.lock, self.db() as db:
            row = db.execute(
                "SELECT closed,expires FROM runs WHERE id=?", (rid,)
            ).fetchone()
            if not row or row[0] or row[1] <= time.time() or rid in self.busy:
                raise RunnerError("Run unavailable or busy.")
            self.busy.add(rid)
        timeout = min(120, max(1, payload.get("timeout", 120)))
        timer = threading.Timer(timeout + 3, lambda: self.destroy(rid))
        timer.daemon = True
        timer.start()
        try:
            container = self.client.containers.get(self.name(rid))
            result = self.invoke(container, payload)
            if result.get("timed_out"):
                self.destroy(rid)
            return result
        finally:
            timer.cancel()
            with self.lock:
                self.busy.discard(rid)

    def invoke(self, container, payload):
        encoded = json.dumps(payload, ensure_ascii=False)
        if len(encoded.encode()) > 100000:
            raise RunnerError("Tool arguments too large.")
        eid = self.client.api.exec_create(
            container.id,
            ["python3", "-I", "/opt/worker.py", encoded],
            user="10001:10001",
            workdir="/workspace",
        )["Id"]
        chunks = bytearray()
        stream = self.client.api.exec_start(eid, stream=True)
        try:
            for chunk in stream:
                chunks.extend(chunk)
                if len(chunks) > 8 * 1024 * 1024:
                    container.remove(force=True)
                    raise RunnerError("Tool output exceeded the limit.")
        finally:
            stream.close()
        try:
            result = json.loads(chunks)
        except (ValueError, UnicodeError):
            raise RunnerError("Sandbox exited or returned invalid output.")
        if not isinstance(result, dict):
            raise RunnerError("Invalid tool result.")
        return result

    def files(self, owner, sid, payload):
        if not self.helpers.acquire(blocking=False):
            raise RunnerError("File gateway is busy; retry shortly.")
        try:
            return self._files(owner, sid, payload)
        finally:
            self.helpers.release()

    def _files(self, owner, sid, payload):
        if payload["op"] not in ("list_dir", "read_file"):
            raise RunnerError("Gateway is read-only.")
        with self.lock, self.db() as db:
            self.scope(db, owner, sid)
            try:
                volume = self.client.volumes.get(self.volume(sid))
            except NotFound:
                if payload["op"] == "list_dir":
                    return {"files": []}
                raise RunnerError("File not found.")
            if volume.attrs.get("Labels", {}).get("owner") != owner:
                raise RunnerError("Volume ownership mismatch.")
            image = self.client.images.get(self.image).id
            helper = self.client.containers.run(
                image,
                ["sleep", "30"],
                detach=True,
                labels={
                    **self.labels(),
                    "helper": "true",
                    "expires": str(time.time() + 30),
                },
                **self.options(volume.name, readonly=True),
            )
        timer = threading.Timer(15, lambda: helper.remove(force=True))
        timer.daemon = True
        timer.start()
        try:
            return self.invoke(helper, payload)
        finally:
            timer.cancel()
            try:
                helper.remove(force=True)
            except NotFound:
                pass

    def purge(self, owner, sid):
        with self.lock, self.db() as db:
            identifier(owner)
            identifier(sid)
            row = db.execute("SELECT owner FROM scopes WHERE id=?", (sid,)).fetchone()
            if row and row[0] != owner:
                raise RunnerError("Scope ownership mismatch.")
            active = db.execute(
                "SELECT id FROM runs WHERE scope=? AND closed=0", (sid,)
            ).fetchone()
            if active:
                raise RunnerError("Scope still has an active run.")
            # Retain a tombstone; stale requests must not recreate a deleted scope.
            db.execute(
                "INSERT INTO scopes VALUES(?,?,1) ON CONFLICT(id) DO UPDATE SET deleted=1",
                (sid, owner),
            )
            db.commit()
            try:
                volume = self.client.volumes.get(self.volume(sid))
                if volume.attrs.get("Labels", {}).get("owner") != owner:
                    raise RunnerError("Volume ownership mismatch.")
                volume.remove()
            except NotFound:
                pass
        return {"removed": True}

    def sweep(self, all_runs=False):
        # Docker labels also cover crashes between create and registry update.
        low_disk = shutil.disk_usage(os.path.dirname(self.path)).free < 256 * 1024**2
        for c in self.client.containers.list(
            all=True, filters={"label": f"harness.runner={self.namespace}"}
        ):
            if (
                all_runs
                or low_disk
                or float(c.labels.get("expires", "0")) <= time.time()
                or c.status != "running"
            ):
                rid = c.labels.get("run")
                if rid:
                    self.destroy(rid)
                else:
                    try:
                        c.remove(force=True)
                    except NotFound:
                        pass
