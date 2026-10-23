"""Validated operational settings, snapshotted per run."""

from dataclasses import dataclass, asdict
import hashlib
import json


@dataclass(frozen=True)
class ContextPolicy:
    version: str = "context-v1"
    max_turns: int = 15
    max_tool_calls: int = 16
    safety_tokens: int = 1024
    safety_fraction: float = 0.05
    summary_enabled: bool = True
    summary_trigger: float = 0.8
    summary_model: str = ""
    summary_output_tokens: int = 1024
    summary_max_calls: int = 2
    summary_input_fraction: float = 0.25
    recent_groups: int = 4
    tool_output_chars: int = 70000
    workspace: str = "/workspace"
    trace_payloads: bool = False
    trace_payload_chars: int = 16000
    trace_retention_days: int = 30
    payload_retention_days: int = 7
    export_max_attempts: int = 5

    @property
    def content_hash(self):
        return hashlib.sha256(
            json.dumps(asdict(self), sort_keys=True).encode()
        ).hexdigest()


def load_context_policy(config: dict) -> ContextPolicy:
    raw = config.get("context", {})
    if not isinstance(raw, dict):
        raise ValueError("context must be a mapping")
    try:
        policy = ContextPolicy(**raw)
    except TypeError as error:
        raise ValueError("Unknown context configuration") from error
    for key, value in asdict(policy).items():
        default = getattr(ContextPolicy(), key)
        if type(default) is bool:
            valid = type(value) is bool
        elif type(default) is int:
            valid = type(value) is int and value > 0
        elif type(default) is float:
            valid = type(value) in (int, float) and 0 < value < 1
        else:
            valid = isinstance(value, str) and (
                bool(value.strip()) or key == "summary_model"
            )
        if not valid:
            raise ValueError(f"Invalid context setting: {key}")
    return policy
