"""Evidence checks, never inferred from the assistant's success claims."""

from pathlib import Path
import hashlib


def verify_file(root: str, path: str, expected: str) -> dict:
    base = Path(root).resolve()
    file = (base / path).resolve()
    if not file.is_relative_to(base) or not file.is_file():
        return {"passed": False, "reason": "missing_or_outside_workspace"}
    raw = file.read_bytes()
    return {
        "passed": raw == expected.encode(),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
    }


def compare_reports(baseline: dict, candidate: dict) -> dict:
    old = {c["id"]: c for c in baseline["cases"]}
    new = {c["id"]: c for c in candidate["cases"]}
    missing = sorted(set(old) - set(new))
    regressions = [
        key
        for key, c in old.items()
        if c["severity"] == "critical"
        and c["passed"]
        and not new.get(key, {}).get("passed", False)
    ]
    old_rate = sum(c["passed"] for c in old.values()) / max(1, len(old))
    new_rate = sum(c["passed"] for c in new.values()) / max(1, len(new))
    return {
        "passed": not missing and not regressions and new_rate >= old_rate,
        "missing": missing,
        "critical_regressions": regressions,
        "baseline_pass_rate": old_rate,
        "candidate_pass_rate": new_rate,
    }
