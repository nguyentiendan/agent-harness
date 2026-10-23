"""Opt-in live experiments on an explicitly disposable platform database."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import time
from urllib.parse import urlsplit
import uuid
from .budget import ExperimentBudget, current_budget


def validate_live_environment(database_url: str, cap: str):
    name = urlsplit(database_url).path.rsplit("/", 1)[-1]
    if not name or "test" not in name.lower():
        raise ValueError(
            "Live evaluation requires an explicit disposable test database"
        )
    return ExperimentBudget(cap)


async def run_live(dataset_path, output, model, user_id, max_cost_usd, repetitions=3):
    budget = validate_live_environment(
        os.environ.get("PLATFORM_DATABASE_URL", ""), max_cost_usd
    )
    if type(repetitions) is not int or repetitions <= 0:
        raise ValueError("Positive repetition count required")
    from app.platform.db import DB, User, Chat, Project, Run, UsageRecord
    from app.platform import chat
    from app.platform.routes import Start
    from app.billing import policy
    from app.execution import client, lifecycle
    from sqlalchemy import select

    if not policy.enabled():
        raise ValueError("Billing enforcement must be enabled for live evaluation")
    with DB() as db:
        user = db.get(User, user_id)
        if not user or not user.active or user.must_change_password:
            raise ValueError("An active pre-provisioned evaluation user is required")
    raw = Path(dataset_path).read_bytes()
    cases = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    seen = set()
    for case in cases:
        if not {"id", "turns", "expect"}.issubset(case) or case["id"] in seen:
            raise ValueError("Invalid live dataset")
        if (
            not isinstance(case["turns"], list)
            or not case["turns"]
            or any(not isinstance(t, str) or not t.strip() for t in case["turns"])
        ):
            raise ValueError("Invalid live turns")
        if not isinstance(case["expect"], dict) or not case["expect"]:
            raise ValueError("Live cases require explicit verifiers")
        if set(case["expect"]) - {"contains", "not_contains", "files"}:
            raise ValueError("Unknown live verifier")
        if case.get("mode", "chat") not in ("chat", "project"):
            raise ValueError("Unknown live mode")
        if case.get("mode") == "project" and not client.enabled():
            raise ValueError("Project evaluation requires the real sandbox runner")
        seen.add(case["id"])
    root = Path(output).resolve()
    root.mkdir(parents=True, exist_ok=True)
    report = {
        "mode": "live",
        "model": model,
        "dataset_hash": hashlib.sha256(raw).hexdigest(),
        "max_cost_usd": str(budget.cap),
        "repetitions": repetitions,
        "cases": [],
    }
    token = current_budget.set(budget)
    try:
        for case in cases:
            for repetition in range(repetitions):
                sid = str(uuid.uuid4())
                pid = str(uuid.uuid4()) if case.get("mode") == "project" else None
                row = {
                    "id": case["id"],
                    "repetition": repetition,
                    "passed": False,
                    "runs": [],
                    "severity": case.get("severity", "quality"),
                }
                started = time.monotonic()
                try:
                    with DB.begin() as db:
                        if pid:
                            db.add(
                                Project(
                                    id=pid, owner_id=user_id, name="Evaluation fixture"
                                )
                            )
                            db.flush()
                        c = Chat(
                            id=sid,
                            owner_id=user_id,
                            title="Evaluation fixture",
                            project_id=pid,
                        )
                        db.add(c)
                        db.flush()
                        if pid:
                            lifecycle.scope_for_chat(db, c)
                    for index, goal in enumerate(case["turns"]):
                        with DB() as db:
                            c = db.get(Chat, sid)
                            result = await chat.start(
                                db,
                                c,
                                Start(
                                    session_id=sid,
                                    request_id=str(index),
                                    goal=goal,
                                    primary_model=model,
                                ),
                            )
                        rid = result["run_id"]
                        row["runs"].append(rid)
                        task = chat.running.get(rid)
                        if task:
                            await task
                        with DB() as db:
                            if db.get(Run, rid).status != "completed":
                                raise ValueError("Evaluation run did not complete")
                    with DB() as db:
                        events = chat.events_for(db, sid)
                        text = "\n".join(
                            e["text"] for e in events if e.get("type") == "assistant"
                        )
                        usages = list(
                            db.scalars(
                                select(UsageRecord).where(
                                    UsageRecord.run_id.in_(row["runs"])
                                )
                            )
                        )
                        row["usage"] = [
                            {
                                "id": u.id,
                                "status": u.status,
                                "cost": str(u.cost) if u.cost is not None else None,
                                "input_tokens": u.input_tokens,
                                "output_tokens": u.output_tokens,
                            }
                            for u in usages
                        ]
                    expected = case["expect"]
                    checks = []
                    for value in expected.get("contains", []):
                        checks.append(value in text)
                    for value in expected.get("not_contains", []):
                        checks.append(value not in text)
                    row["file_evidence"] = []
                    for path, content in expected.get("files", {}).items():
                        if not pid:
                            raise ValueError("File verifier requires Project mode")
                        evidence = await asyncio.to_thread(
                            client.RunnerClient().files, user_id, pid, "read", path
                        )
                        actual = evidence.get("content")
                        passed = actual == content
                        row["file_evidence"].append(
                            {
                                "path": path,
                                "passed": passed,
                                "sha256": hashlib.sha256(
                                    str(actual).encode()
                                ).hexdigest(),
                            }
                        )
                        checks.append(passed)
                    row["passed"] = bool(checks) and all(checks)
                    row["response"] = text
                except Exception as error:
                    row["error_code"] = type(error).__name__
                finally:
                    row["duration_seconds"] = time.monotonic() - started
                    report["cases"].append(row)
                    # Preserve DB evidence for review; remove only this experiment's workspace.
                    if pid:
                        try:
                            await asyncio.to_thread(
                                client.RunnerClient().purge, user_id, pid
                            )
                        except Exception:
                            row["cleanup_pending"] = True
                    report["committed_or_pending_usd"] = str(budget.committed)
                    report["passed"] = all(c["passed"] for c in report["cases"])
                    (root / "report.json").write_text(json.dumps(report, indent=2))
    finally:
        current_budget.reset(token)
    return report
