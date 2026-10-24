"""Scoped, expiring, redacted tool artifacts with bounded paginated retrieval."""

import hashlib
import json
import time
from sqlalchemy import select, func, delete
from app.platform.db import DB, Run, Chat, Project, User, ToolOutput
from app.security.redaction import redact_text
from .types import ContextBudgetExceeded


def scope_for(db, run_id):
    run = db.get(Run, run_id)
    chat = (
        db.scalar(select(Chat).where(Chat.id == run.session_id).with_for_update())
        if run
        else None
    )
    owner = db.get(User, chat.owner_id) if chat else None
    project = db.get(Project, chat.project_id) if chat and chat.project_id else None
    if (
        not run
        or run.status != "running"
        or not chat
        or chat.deleted_at
        or not owner
        or not owner.active
        or (chat.project_id and (not project or project.deleted_at))
    ):
        raise ValueError("Tool output scope is unavailable")
    return chat.id, (run.workspace_snapshot or {}).get("revision")


def available(db, scope, policy):
    db.execute(delete(ToolOutput).where(ToolOutput.expires_at <= time.time()))
    used = db.scalar(
        select(func.coalesce(func.sum(ToolOutput.chars), 0)).where(
            ToolOutput.scope_id == scope
        )
    )
    return policy.output_scope_chars - used


def ensure_capacity(run_id, policy):
    # Admission happens before a side effect, never by retrying an executed tool.
    with DB.begin() as db:
        scope, _ = scope_for(db, run_id)
        if available(db, scope, policy) < policy.output_max_chars:
            raise ContextBudgetExceeded(
                "Tool output storage quota exhausted; no tool was executed"
            )


def store_output(run_id, key, text, policy):
    text = redact_text(text)
    if len(text) > policy.output_max_chars:
        raise ContextBudgetExceeded(
            "Executed tool output exceeds storage limit; do not repeat the action"
        )
    ident = hashlib.sha256((run_id + "\0" + key + "\0" + text).encode()).hexdigest()
    with DB.begin() as db:
        scope, revision = scope_for(db, run_id)
        row = db.get(ToolOutput, ident)
        if row and row.expires_at > time.time():
            return ident
        if row:
            db.delete(row)
            db.flush()
        if available(db, scope, policy) < len(text):
            raise ContextBudgetExceeded(
                "Executed tool output cannot be archived: quota exhausted; do not repeat the action"
            )
        db.add(
            ToolOutput(
                id=ident,
                scope_id=scope,
                run_id=run_id,
                workspace_revision=revision,
                content=text,
                chars=len(text),
                expires_at=time.time() + policy.output_retention_days * 86400,
            )
        )
    return ident


def read_output(run_id, output_id, offset, limit, policy):
    if type(offset) is not int or offset < 0 or type(limit) is not int or limit < 1:
        raise ValueError("Invalid output pagination")
    limit = min(limit, policy.output_read_chars)
    with DB.begin() as db:
        scope, revision = scope_for(db, run_id)
        row = db.get(ToolOutput, output_id)
        if (
            not row
            or row.scope_id != scope
            or row.workspace_revision != revision
            or row.expires_at <= time.time()
        ):
            raise ValueError(
                "Tool output unavailable in this scope/revision or expired"
            )
        end = min(offset + limit, row.chars)
        return fit_page(
            {
                "output_id": row.id,
                "content": row.content[offset:end],
                "offset": offset,
                "next_offset": end if end < row.chars else None,
                "total_chars": row.chars,
            },
            min(
                policy.output_read_chars,
                policy.tool_preview_chars,
                policy.tool_output_chars,
            ),
        )


def preview(text, output_id, max_chars):
    # Preserve execution status even when the bulk content moves out of context.
    data = {
        "output_id": output_id,
        "truncated": True,
        "original_chars": len(text),
        "preview": "",
    }
    try:
        original = json.loads(text)
        if isinstance(original, dict):
            for key in ("exit_code", "timed_out", "truncated"):
                if key in original and type(original[key]) in (int, bool, type(None)):
                    data["upstream_" + key if key == "truncated" else key] = original[
                        key
                    ]
    except (ValueError, TypeError):
        pass
    base = len(json.dumps(data, ensure_ascii=False))
    if base > max_chars:
        raise ContextBudgetExceeded(
            "Tool output reference cannot fit the context budget"
        )
    fragment = text[: max_chars - base]
    data["preview"] = fragment
    while len(json.dumps(data, ensure_ascii=False)) > max_chars:
        fragment = fragment[: len(fragment) // 2]
        data["preview"] = fragment
    return json.dumps(data, ensure_ascii=False)


def is_page(data):
    return (
        isinstance(data, dict)
        and isinstance(data.get("content"), str)
        and isinstance(data.get("output_id"), str)
        and type(data.get("offset")) is int
        and type(data.get("total_chars")) is int
        and "next_offset" in data
    )


def fit_page(data, max_chars):
    """Bound the serialized page while retaining a cursor into the original artifact."""
    data = dict(data)
    while len(json.dumps(data, ensure_ascii=False)) > max_chars:
        if not data["content"]:
            raise ContextBudgetExceeded(
                "Tool output page metadata exceeds context budget"
            )
        data["content"] = data["content"][: len(data["content"]) // 2]
        end = data["offset"] + len(data["content"])
        data["next_offset"] = end if end < data["total_chars"] else None
    return data
