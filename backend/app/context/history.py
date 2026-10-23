"""Durable redacted messages; replay only complete tool protocol groups."""

import hashlib
import json
from sqlalchemy import select, func
from app.platform.db import ContextMessage
from app.security.redaction import redact_text
from .types import ConversationRecord, ConversationGroup


def bounded_output(result: dict, limit: int) -> str:
    raw = json.dumps(result, ensure_ascii=False)
    if len(raw) <= limit:
        return raw
    envelope = {
        k: result[k]
        for k in ("exit_code", "timed_out", "error")
        if k in result and isinstance(result[k], (int, bool, type(None)))
    }
    envelope.update(truncated=True, original_chars=len(raw), preview="")
    base = len(json.dumps(envelope, ensure_ascii=False))
    # Escaping may expand characters; trim until the entire envelope fits.
    preview = raw[: max(0, limit - base)]
    envelope["preview"] = preview
    while len(json.dumps(envelope, ensure_ascii=False)) > limit and preview:
        preview = preview[: len(preview) // 2]
        envelope["preview"] = preview
    return json.dumps(envelope, ensure_ascii=False)


def append_records(
    db, scope_id: str, run_id: str, records: list[ConversationRecord]
) -> list[str]:
    ids = []
    sequence = db.scalar(
        select(func.max(ContextMessage.sequence)).where(
            ContextMessage.scope_id == scope_id
        )
    )
    sequence = -1 if sequence is None else sequence
    for record in records:
        key = hashlib.sha256(f"{scope_id}:{run_id}:{record.id}".encode()).hexdigest()
        ids.append(key)
        if db.get(ContextMessage, key):
            continue
        sequence += 1
        payload = json.loads(
            redact_text(json.dumps(record.message, ensure_ascii=False))
        )
        payload_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()
        db.add(
            ContextMessage(
                id=key,
                scope_id=scope_id,
                run_id=run_id,
                sequence=sequence,
                group_id=f"{run_id}:{record.group_id}",
                payload={"message": payload, "content_hash": payload_hash},
                workspace_revision=record.workspace_revision,
            )
        )
        db.flush()
    return ids


def load_groups(
    db, scope_id: str, before_sequence: int | None = None
) -> list[ConversationGroup]:
    query = (
        select(ContextMessage)
        .where(ContextMessage.scope_id == scope_id)
        .order_by(ContextMessage.sequence)
    )
    if before_sequence is not None:
        query = query.where(ContextMessage.sequence < before_sequence)
    groups = {}
    for row in db.scalars(query):
        group = groups.setdefault(
            row.group_id,
            ConversationGroup(
                row.group_id, [], [], row.workspace_revision, row.sequence
            ),
        )
        group.messages.append(row.payload["message"])
        group.source_ids.append(row.id)
    result = []
    for group in groups.values():
        calls = [c["id"] for m in group.messages for c in m.get("tool_calls", [])]
        outputs = [m.get("tool_call_id") for m in group.messages if m["role"] == "tool"]
        if sorted(calls) == sorted(outputs) and len(calls) == len(set(calls)):
            result.append(group)
    return result


def record_protocol(run_id: str, record_id: str, message: dict, group_id: str):
    from app.platform.db import DB, Run

    with DB.begin() as db:
        run = db.get(Run, run_id)
        if run:
            append_records(
                db,
                run.session_id,
                run_id,
                [
                    ConversationRecord(
                        record_id,
                        message["role"],
                        message,
                        group_id,
                        (run.workspace_snapshot or {}).get("revision"),
                    )
                ],
            )
