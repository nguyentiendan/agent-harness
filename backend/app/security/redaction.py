"""Redact secrets from text before persisting/observing (adapted from the
redaction idea in NousResearch/hermes-agent's agent/redact.py)."""

import os
import re

_SECRET_KEY_RE = re.compile(
    r"(?i)(api[_-]?key|auth[_-]?token|secret|password|passwd|credential|private[_-]?key)"
)
_SK_KEY_RE = re.compile(r"(?i)(sk-[A-Za-z0-9_-]{8,})")
_BEARER_RE = re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]{8,}")


def _secret_env_values():
    values = set()
    for key, val in os.environ.items():
        if _SECRET_KEY_RE.search(key) and val and len(val) > 4:
            values.add(val)
    return values


def redact_text(text: str) -> str:
    """Mask secret values, `sk-...` keys and `Bearer ...` tokens."""
    if not text:
        return text
    out = text
    for val in _secret_env_values():
        if val in out:
            out = out.replace(val, "[REDACTED]")
    out = _SK_KEY_RE.sub(lambda m: m.group(0)[:6] + "[REDACTED]", out)
    out = _BEARER_RE.sub(r"\1[REDACTED]", out)
    return out
