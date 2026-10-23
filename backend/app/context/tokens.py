"""Payload accounting with explicit conservative fallback for unknown models."""

import json
from .types import TokenEstimate


def count_payload(messages: list[dict], tools: list[dict], model: str) -> TokenEstimate:
    payload = json.dumps(
        {"messages": messages, "tools": tools},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    # UTF-8 bytes bound ordinary byte-tokenizers conservatively. Framing is
    # included in the serialization; this still is not provider exact usage.
    count = len(payload.encode("utf-8"))
    return TokenEstimate(count, "utf8-json-upper-estimate-v1", True)
