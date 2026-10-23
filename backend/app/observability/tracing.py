"""Best-effort local metadata tracing; never retries application actions."""

from contextlib import contextmanager
from contextvars import ContextVar
import logging
import time
import uuid
from sqlalchemy import select
from app.platform.db import DB, TraceRecord, TraceSpan

current_trace = ContextVar("trace_id", default=None)
current_span = ContextVar("span_id", default=None)
ALLOWED = {
    "scope_id",
    "model",
    "purpose",
    "policy_hash",
    "policy_version",
    "prompt_versions",
    "instruction_hash",
    "model_revision",
    "estimated_tokens",
    "estimator_id",
    "approximate",
    "input_budget",
    "output_cap",
    "safety_reserve",
    "selected_group_ids",
    "omitted",
    "summary_used",
    "summary_reason",
    "tool_name",
    "tool_call_id",
    "error_code",
    "input_tokens",
    "output_tokens",
    "gateway_request_id",
    "workspace_revision",
    "telemetry_incomplete",
}
log = logging.getLogger(__name__)


def safe_metadata(data):
    return {k: v for k, v in data.items() if k in ALLOWED}


def best_effort(fn):
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            log.error("telemetry_write_failed operation=%s", fn.__name__)
            return None

    return wrapped


@best_effort
def start_trace(run_id: str, metadata: dict) -> str:
    with DB.begin() as db:
        if not db.get(TraceRecord, run_id):
            db.add(
                TraceRecord(
                    id=run_id,
                    scope_id=metadata["scope_id"],
                    data=safe_metadata(metadata),
                )
            )
    return run_id


@best_effort
def start_span(
    trace_id: str, parent_id: str | None, kind: str, metadata: dict
) -> str | None:
    if not trace_id:
        return None
    sid = str(uuid.uuid4())
    with DB.begin() as db:
        db.add(
            TraceSpan(
                id=sid,
                trace_id=trace_id,
                parent_id=parent_id,
                kind=kind,
                data=safe_metadata(metadata),
            )
        )
    return sid


@best_effort
def finish_span(span_id: str, status: str, metadata: dict) -> None:
    if not span_id:
        return
    with DB.begin() as db:
        row = db.get(TraceSpan, span_id)
        if row:
            row.status = status
            row.finished_at = time.time()
            row.data = {**row.data, **safe_metadata(metadata)}


@best_effort
def link_usage(span_id: str, usage_id: str) -> None:
    if not span_id:
        return
    with DB.begin() as db:
        row = db.get(TraceSpan, span_id)
        if row:
            row.usage_id = usage_id


@best_effort
def finish_trace(run_id: str, status: str) -> None:
    with DB.begin() as db:
        row = db.get(TraceRecord, run_id)
        if row:
            row.status = status
            row.finished_at = time.time()


@best_effort
def interrupt_unfinished():
    with DB.begin() as db:
        for model in (TraceSpan, TraceRecord):
            for row in db.scalars(select(model).where(model.status == "running")):
                row.status = "interrupted"
                row.finished_at = time.time()


@contextmanager
def span(kind: str, metadata: dict | None = None):
    sid = start_span(current_trace.get(), current_span.get(), kind, metadata or {})
    token = current_span.set(sid)
    try:
        yield sid
    except BaseException as error:
        finish_span(
            sid,
            "cancelled" if type(error).__name__ == "CancelledError" else "failed",
            {"error_code": type(error).__name__},
        )
        raise
    else:
        finish_span(sid, "completed", {})
    finally:
        current_span.reset(token)


@best_effort
def capture_payload(trace_id, span_id, payload, policy):
    if not trace_id or not policy.trace_payloads:
        return
    import json
    from app.security.redaction import redact_text
    from app.context.history import bounded_output
    from app.platform.db import TracePayload

    safe = json.loads(redact_text(bounded_output(payload, policy.trace_payload_chars)))
    with DB.begin() as db:
        db.add(
            TracePayload(
                id=str(uuid.uuid4()),
                trace_id=trace_id,
                payload={"span_id": span_id, "data": safe},
            )
        )
