"""Owner/admin trace reads; raw payloads are never returned by these routes."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from app.platform.auth import context
from app.platform.identity import owned_chat
from app.platform.db import TraceRecord, TraceSpan, UsageRecord

router = APIRouter()


def trace_data(db, trace, offset=0, limit=100):
    spans = []
    for row in db.scalars(
        select(TraceSpan)
        .where(TraceSpan.trace_id == trace.id)
        .order_by(TraceSpan.created_at, TraceSpan.id)
        .offset(offset)
        .limit(limit)
    ):
        usage = db.get(UsageRecord, row.usage_id) if row.usage_id else None
        spans.append(
            {
                "id": row.id,
                "parent_id": row.parent_id,
                "kind": row.kind,
                "status": row.status,
                "started_at": row.created_at,
                "finished_at": row.finished_at,
                "metadata": row.data,
                "usage": None
                if not usage
                else {
                    "id": usage.id,
                    "status": usage.status,
                    "cost": str(usage.cost) if usage.cost is not None else None,
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "gateway_request_id": usage.gateway_request_id,
                },
            }
        )
    return {
        "id": trace.id,
        "status": trace.status,
        "metadata": trace.data,
        "spans": spans,
        "offset": offset,
        "limit": limit,
    }


def authorized(db, user, sid, rid):
    owned_chat(db, user, sid)
    trace = db.get(TraceRecord, rid)
    if not trace or trace.scope_id != sid:
        raise HTTPException(404, "Trace not found.")
    return trace


@router.get("/api/sessions/{sid}/runs/{rid}/trace")
def get_trace(
    sid: str,
    rid: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    ctx=Depends(context),
):
    db, user, _ = ctx
    data = trace_data(db, authorized(db, user, sid, rid), offset, limit)
    db.commit()
    return data


@router.get("/api/sessions/{sid}/runs/{rid}/context")
def get_context(
    sid: str,
    rid: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    ctx=Depends(context),
):
    db, user, _ = ctx
    authorized(db, user, sid, rid)
    rows = list(
        db.scalars(
            select(TraceSpan)
            .where(TraceSpan.trace_id == rid, TraceSpan.kind == "context")
            .order_by(TraceSpan.created_at, TraceSpan.id)
            .offset(offset)
            .limit(limit)
        )
    )
    data = {"manifests": [r.data for r in rows], "offset": offset, "limit": limit}
    db.commit()
    return data
