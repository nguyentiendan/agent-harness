"""Offline regression runner: one isolated process and evidence log per case."""

import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import time
import hashlib
from dataclasses import asdict
from .dataset import load_dataset
from .verifiers import compare_reports
from app.context.policy import load_context_policy
from app.config import load_config


def run_offline(dataset_path: str, output: str, baseline: str | None = None) -> dict:
    dataset = load_dataset(dataset_path)
    root = Path(output).resolve()
    root.mkdir(parents=True, exist_ok=True)
    backend = Path(__file__).resolve().parents[2]
    revision = os.environ.get("APP_REVISION")
    dirty = b""
    if shutil.which("git"):
        revision = (
            subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=backend,
                capture_output=True,
                text=True,
            ).stdout.strip()
            or revision
        )
        dirty = subprocess.run(
            ["git", "diff", "HEAD"], cwd=backend, capture_output=True
        ).stdout
    policy = load_context_policy(load_config(strict=True))
    from app.prompts import system, chat, project, summarization, evaluation

    specs = [
        system.SYSTEM_PROMPT_SPEC,
        chat.CHAT_PROMPT_SPEC,
        project.PROJECT_PROMPT_SPEC,
        summarization.SUMMARY_PROMPT_SPEC,
        evaluation.JUDGE_PROMPT_SPEC,
    ]
    report = {
        "mode": "offline",
        "dataset_version": dataset.version,
        "dataset_hash": dataset.content_hash,
        "git_revision": revision,
        "tracked_diff_hash": hashlib.sha256(dirty).hexdigest(),
        "policy": asdict(policy),
        "policy_hash": policy.content_hash,
        "prompts": [
            {"id": p.id, "version": p.version, "hash": p.content_hash} for p in specs
        ],
        "model": "scripted/external-transport-fixtures",
        "cost": None,
        "tokens": None,
        "cases": [],
    }
    env = dict(os.environ)
    env["PYTHONPATH"] = str(backend) + os.pathsep + str(backend / "tests")
    # Every child creates its own disposable SQLite DB unless a test DB was
    # explicitly chosen. Never inherit an application database URL.
    env.pop("PLATFORM_DATABASE_URL", None)
    env.pop("TEST_DATABASE_URL", None)
    for case in dataset.cases:
        started = time.monotonic()
        log = root / (case["id"] + ".log")
        try:
            result = subprocess.run(
                [sys.executable, "-m", "unittest", case["test"], "-v"],
                cwd=backend,
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
            )
            text = result.stdout + result.stderr
            passed = result.returncode == 0 and "Ran 0 tests" not in text
        except subprocess.TimeoutExpired:
            text = "Verifier timed out after 120 seconds"
            passed = False
        log.write_text(text)
        report["cases"].append(
            {
                **case,
                "passed": passed,
                "duration_seconds": time.monotonic() - started,
                "evidence": str(log),
                "evidence_hash": hashlib.sha256(text.encode()).hexdigest(),
            }
        )
    report["passed"] = all(c["passed"] for c in report["cases"])
    report["categories"] = {
        category: {
            "passed": sum(
                c["passed"] for c in report["cases"] if c["category"] == category
            ),
            "total": sum(c["category"] == category for c in report["cases"]),
        }
        for category in sorted({c["category"] for c in report["cases"]})
    }
    if baseline:
        report["comparison"] = compare_reports(
            json.loads(Path(baseline).read_text()), report
        )
    (root / "report.json").write_text(json.dumps(report, indent=2))
    return report
