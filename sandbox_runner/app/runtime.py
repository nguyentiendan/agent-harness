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
from .storage import validate_binding, mount_options, probe_local, profile


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
        self.active_helpers = set()
        self.pending_helpers = {}
        self.helpers = threading.BoundedSemaphore(2)
        with self.db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS scopes (id TEXT PRIMARY KEY, owner TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, scope TEXT, owner TEXT, closed INTEGER NOT NULL, expires REAL NOT NULL)"
            )

            db.execute(
                "CREATE TABLE IF NOT EXISTS probes (id TEXT PRIMARY KEY, owner TEXT NOT NULL, scope TEXT NOT NULL, closed INTEGER NOT NULL DEFAULT 0)"
            )
            for table, columns in {
                "scopes": {
                    "mode": "TEXT NOT NULL DEFAULT 'managed'",
                    "local_path": "TEXT",
                    "revision": "INTEGER NOT NULL DEFAULT 0",
                },
                "runs": {"revision": "INTEGER NOT NULL DEFAULT 0"},
            }.items():
                present = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
                for name, definition in columns.items():
                    if name not in present:
                        db.execute(
                            f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
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

    def options(self, volume, readonly=False, binding=None):
        return {
            **profile(),
            **mount_options(
                binding or dict(mode="managed", local_path=None, revision=0),
                volume,
                readonly,
            ),
        }

    def validate_workspace(self, binding, owner=None, sid=None, operation=None):
        if not owner or not sid or not operation:
            raise RunnerError("Scoped validation is required.")
        identifier(owner)
        identifier(sid)
        identifier(operation)
        binding = validate_binding(binding)
        if not self.helpers.acquire(blocking=False):
            raise RunnerError("File gateway is busy.")
        try:
            with self.lock:
                with self.db() as db:
                    if db.execute(
                        "SELECT id FROM probes WHERE id=?", (operation,)
                    ).fetchone():
                        raise RunnerError("Validation already completed or cancelled.")
                    self.check_probe_idle(db, sid)
                    row = db.execute(
                        "SELECT owner,deleted FROM scopes WHERE id=?", (sid,)
                    ).fetchone()
                    if row and (row[0] != owner or row[1]):
                        raise RunnerError("Scope unavailable.")
                    if (
                        sid in self.active_helpers
                        or db.execute(
                            "SELECT id FROM runs WHERE scope=? AND closed=0", (sid,)
                        ).fetchone()
                    ):
                        raise RunnerError("Scope busy.")
                    db.execute(
                        "INSERT INTO probes(id,owner,scope) VALUES(?,?,?)",
                        (operation, owner, sid),
                    )
                try:
                    return probe_local(
                        self.client,
                        self.image,
                        binding,
                        self.labels(),
                        self.namespace + "-probe-" + operation,
                    )
                finally:
                    self.release_validation(owner, sid, operation)
        finally:
            self.helpers.release()

    def check_probe_idle(self, db, sid):
        if db.execute(
            "SELECT id FROM probes WHERE scope=? AND closed=0", (sid,)
        ).fetchone():
            raise RunnerError("Workspace validation cleanup is pending.")

    def release_validation(self, owner, sid, operation):
        identifier(owner)
        identifier(sid)
        identifier(operation)
        with self.lock, self.db() as db:
            row = db.execute(
                "SELECT owner,scope FROM probes WHERE id=?", (operation,)
            ).fetchone()
            if row and row != (owner, sid):
                raise RunnerError("Validation ownership mismatch.")
            # A cancellation arriving before validate is a tombstone for that operation.
            db.execute(
                "INSERT INTO probes(id,owner,scope) VALUES(?,?,?) ON CONFLICT(id) DO NOTHING",
                (operation, owner, sid),
            )
            db.commit()
            try:
                c = self.client.containers.get(self.namespace + "-probe-" + operation)
                if c.labels.get("harness.runner") != self.namespace:
                    raise RunnerError("Container ownership mismatch.")
                c.remove(force=True)
            except NotFound:
                pass
            db.execute("UPDATE probes SET closed=1 WHERE id=?", (operation,))
        return {"removed": True}

    def _binding(self, db, owner, sid, revision):
        self.scope(db, owner, sid)
        self.check_probe_idle(db, sid)
        row = db.execute(
            "SELECT mode,local_path,revision FROM scopes WHERE id=?", (sid,)
        ).fetchone()
        if row[2] != revision:
            raise RunnerError("Workspace changed. Refresh and retry.")
        return dict(mode=row[0], local_path=row[1], revision=row[2])

    def binding_for(self, owner, sid, revision):
        with self.lock, self.db() as db:
            return self._binding(db, owner, sid, revision)

    def configure(self, owner, sid, binding):
        binding = validate_binding(binding)
        with self.lock, self.db() as db:
            self.scope(db, owner, sid)
            self.check_probe_idle(db, sid)
            row = db.execute(
                "SELECT mode,local_path,revision FROM scopes WHERE id=?", (sid,)
            ).fetchone()
            current = dict(mode=row[0], local_path=row[1], revision=row[2])
            if binding == current:
                return {"ready": True}
            if binding["revision"] <= current["revision"]:
                raise RunnerError("Workspace changed. Refresh and retry.")
            if (
                sid in self.active_helpers
                or db.execute(
                    "SELECT id FROM runs WHERE scope=? AND closed=0", (sid,)
                ).fetchone()
            ):
                raise RunnerError("Workspace is busy.")
            db.execute(
                "UPDATE scopes SET mode=?,local_path=?,revision=? WHERE id=?",
                (binding["mode"], binding["local_path"], binding["revision"], sid),
            )
        return {"ready": True}

    def managed_volume(self, owner, sid, create=True):
        try:
            v = self.client.volumes.get(self.volume(sid))
        except NotFound:
            if not create:
                raise
            v = self.client.volumes.create(
                name=self.volume(sid),
                labels={**self.labels(), "owner": owner, "scope": sid},
            )
        if (
            v.attrs.get("Labels", {}).get("owner") != owner
            or v.attrs.get("Labels", {}).get("harness.runner") != self.namespace
        ):
            raise RunnerError("Volume ownership mismatch.")
        return v

    def ensure(self, rid, owner, sid, workspace_revision=0):
        identifier(rid)
        with self.lock, self.db() as db:
            binding = self._binding(db, owner, sid, workspace_revision)
            if sid in self.active_helpers:
                raise RunnerError("Workspace file operation is busy.")
            row = db.execute(
                "SELECT scope,owner,closed,expires,revision FROM runs WHERE id=?",
                (rid,),
            ).fetchone()
            if row:
                if (
                    row[:2] != (sid, owner)
                    or row[2]
                    or row[3] <= time.time()
                    or row[4] != workspace_revision
                ):
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
            if binding["mode"] == "managed":
                self.managed_volume(owner, sid)
            expires = time.time() + 900
            db.execute(
                "INSERT INTO runs(id,scope,owner,closed,expires,revision) VALUES(?,?,?,?,?,?)",
                (rid, sid, owner, 0, expires, workspace_revision),
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
                    **self.options(volume, binding=binding),
                )
            except Exception:
                db.execute("UPDATE runs SET closed=1 WHERE id=?", (rid,))
                db.commit()  # Keep failure tombstone even when the outer transaction rolls back.
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

    def files(self, owner, sid, payload, workspace_revision=0):
        if payload["op"] not in ("list_dir", "read_file"):
            raise RunnerError("Gateway is read-only.")
        from .transfers import helper

        try:
            with helper(
                self, owner, sid, workspace_revision=workspace_revision
            ) as container:
                return self.invoke(container, payload)
        except NotFound:
            if payload["op"] == "list_dir":
                return {"files": []}
            raise RunnerError("File not found.")

    def purge(self, owner, sid):
        with self.lock, self.db() as db:
            identifier(owner)
            identifier(sid)
            self.check_probe_idle(db, sid)
            row = db.execute("SELECT owner FROM scopes WHERE id=?", (sid,)).fetchone()
            if row and row[0] != owner:
                raise RunnerError("Scope ownership mismatch.")
            active = db.execute(
                "SELECT id FROM runs WHERE scope=? AND closed=0", (sid,)
            ).fetchone()
            if active or sid in self.active_helpers:
                raise RunnerError("Scope still has an active run.")
            # Retain a tombstone; stale requests must not recreate a deleted scope.
            db.execute(
                "INSERT INTO scopes(id,owner,deleted) VALUES(?,?,1) ON CONFLICT(id) DO UPDATE SET deleted=1",
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

        with self.lock:
            for sid, name in list(self.pending_helpers.items()):
                try:
                    self.client.containers.get(name).remove(force=True)
                except NotFound:
                    pass
                self.pending_helpers.pop(sid, None)
                self.active_helpers.discard(sid)
        with self.db() as db:
            probes = db.execute(
                "SELECT owner,scope,id FROM probes WHERE closed=0"
            ).fetchall()
        for owner, sid, operation in probes:
            self.release_validation(owner, sid, operation)
