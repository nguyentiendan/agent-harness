"""Retention and scope deletion for context and trace records."""

from sqlalchemy import select, delete
from app.platform.db import (
    ContextMessage,
    ContextSummary,
    TraceRecord,
    TraceSpan,
    TracePayload,
    TraceExportJob,
)


def purge_traces(db, ids):
    for model in (TraceSpan, TracePayload, TraceExportJob):
        db.execute(delete(model).where(model.trace_id.in_(ids)))
    db.execute(delete(TraceRecord).where(TraceRecord.id.in_(ids)))


def purge_scopes(db, ids):
    traces = list(
        db.scalars(select(TraceRecord.id).where(TraceRecord.scope_id.in_(ids)))
    )
    purge_traces(db, traces)
    for model in (ContextMessage, ContextSummary):
        db.execute(delete(model).where(model.scope_id.in_(ids)))


def retain(db, now, policy):
    ids = list(
        db.scalars(
            select(TraceRecord.id).where(
                TraceRecord.created_at < now - policy.trace_retention_days * 86400,
                TraceRecord.status != "running",
            )
        )
    )
    purge_traces(db, ids)
    db.execute(
        delete(TracePayload).where(
            TracePayload.created_at < now - policy.payload_retention_days * 86400
        )
    )
