"""Strict local regression datasets; test paths are trusted repository code."""

from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import re


@dataclass(frozen=True)
class EvaluationDataset:
    version: str
    content_hash: str
    cases: list[dict]


def load_dataset(path: str) -> EvaluationDataset:
    raw = Path(path).read_bytes()
    cases = []
    seen = set()
    for line in raw.decode().splitlines():
        if not line.strip():
            continue
        case = json.loads(line)
        if not isinstance(case, dict) or not {
            "id",
            "category",
            "severity",
            "test",
        }.issubset(case):
            raise ValueError(
                "Each case needs an ID, category, severity and executable verifier"
            )
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", case["id"]) or case["id"] in seen:
            raise ValueError("Invalid or duplicate case ID")
        if case["severity"] not in ("critical", "quality"):
            raise ValueError("Invalid severity")
        if not re.fullmatch(r"test_\w+\.\w+\.test_\w+", case["test"]):
            raise ValueError("Invalid verifier path")
        seen.add(case["id"])
        cases.append(case)
    if not cases:
        raise ValueError("Dataset is empty")
    return EvaluationDataset("regression-v1", hashlib.sha256(raw).hexdigest(), cases)
