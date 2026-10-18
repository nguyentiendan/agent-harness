"""Opt-in: real runner + platform_test only; kill worker process then reconcile."""

import os
import subprocess
import sys
import uuid
from sqlalchemy.engine import make_url

url = make_url(os.environ["PLATFORM_DATABASE_URL"])
if url.database != "platform_test":
    raise RuntimeError("This smoke test requires platform_test.")
from app.platform.db import DB, Chat, Run, RunLease
from app.platform.identity import create_user
from app.platform.chat import reconcile
from app.execution.client import RunnerClient, RunnerUnavailable
from app.cli.cleanup import purge

sid, rid = str(uuid.uuid4()), str(uuid.uuid4())
with DB.begin() as db:
    owner = create_user(
        db, "restart-" + uuid.uuid4().hex[:10], "Smoke password 123!", "user", False
    )["id"]
    db.add(Chat(id=sid, owner_id=owner, title="restart smoke", status="running"))
    db.flush()
    db.add(
        Run(
            id=rid,
            session_id=sid,
            request_id=rid,
            body_hash="smoke",
            model="fake",
            event_offset=0,
        )
    )
script = """
import os,signal
from app.execution.lifecycle import prepare
from app.execution.client import RunnerClient
rid=os.environ['SMOKE_RUN']
prepare(rid)
result=RunnerClient().execute(rid,{'op':'bash','command':'echo retained > marker; sleep 500 &','timeout':5})
assert result['exit_code']==0,result
os.kill(os.getpid(),signal.SIGKILL)
"""
result = subprocess.run(
    [sys.executable, "-c", script], env={**os.environ, "SMOKE_RUN": rid}
)
assert result.returncode == -9, result.returncode
reconcile()
with DB() as db:
    assert db.get(Run, rid).status == "failed"
    assert db.get(RunLease, sid) is None
runner = RunnerClient()
assert runner.files(owner, sid, "read_file", "marker")["content"].strip() == "retained"
try:
    runner.execute(rid, {"op": "bash", "command": "echo unexpected"})
    raise AssertionError("Stopped sandbox was executable")
except RunnerUnavailable:
    pass
with DB.begin() as db:
    db.get(Chat, sid).deleted_at = 1
purge()
with DB() as db:
    assert db.get(Chat, sid) is None
print(
    "PASS: SIGKILL backend worker -> restart cleanup -> file retained -> trash volume purge"
)
