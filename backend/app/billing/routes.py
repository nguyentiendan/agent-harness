import asyncio
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import Field
from sqlalchemy import select
from app.platform.auth import Input, context, admin_context, COOKIE
from app.platform.identity import authenticate, audit
from app.platform.db import DB, User, UserLimit, UsageRecord, Run, Chat
from app.platform import chat
from . import policy, gateway

router = APIRouter()


class Limits(Input):
    monthly_budget: Decimal | None = Field(
        default=None, ge=0, le=1000000, max_digits=18, decimal_places=10
    )
    models: list[str] = Field(default_factory=list, max_length=32)
    concurrent_runs: int = Field(default=1, ge=1, le=4)
    max_output_tokens: int = Field(default=4096, ge=1, le=4096)


class Resolution(Input):
    cost: Decimal = Field(ge=0, le=1000000, max_digits=18, decimal_places=10)
    reason: str = Field(min_length=10, max_length=500)


@router.get("/api/usage")
def mine(ctx=Depends(context)):
    return policy.summary(ctx[0], ctx[1].id)


@router.get("/api/admin/models")
async def models(ctx=Depends(admin_context)):
    try:
        prices = await asyncio.to_thread(gateway.priced_models)
    except gateway.GatewayError:
        raise HTTPException(503, "Cannot verify model pricing with the gateway.")
    allowed = chat.allowed_models()
    return {
        "models": [
            {"name": name, **prices[name]} for name in allowed if name in prices
        ],
        "unpriced": [name for name in allowed if name not in prices],
    }


@router.get("/api/admin/users/{uid}/limits")
def get_limits(uid: str, ctx=Depends(admin_context)):
    if not ctx[0].get(User, uid):
        raise HTTPException(404, "User not found.")
    return policy.limit_dto(ctx[0], uid)


@router.get("/api/admin/users/{uid}/usage")
def get_usage(uid: str, ctx=Depends(admin_context)):
    if not ctx[0].get(User, uid):
        raise HTTPException(404, "User not found.")
    audit(ctx[0], ctx[1], "usage.read", uid)
    return policy.summary(ctx[0], uid)


@router.put("/api/admin/users/{uid}/limits")
async def update(uid: str, body: Limits, request: Request, ctx=Depends(admin_context)):
    try:
        prices = await asyncio.to_thread(gateway.priced_models) if body.models else {}
    except gateway.GatewayError:
        raise HTTPException(503, "Cannot verify model pricing with the gateway.")
    if any(m not in prices or m not in chat.allowed_models() for m in body.models):
        raise HTTPException(422, "Choose a server-enabled model with verified pricing.")
    async with chat.admission:
        db, _, _ = ctx
        db.expire_all()
        actor, _ = authenticate(db, request.cookies.get(COOKIE))
        if actor.role != "admin" or actor.must_change_password:
            raise HTTPException(403, "Administrator access required.")
        if not db.get(User, uid):
            raise HTTPException(404, "User not found.")
        if db.scalar(
            select(Run.id)
            .join(Chat, Run.session_id == Chat.id)
            .where(Chat.owner_id == uid, Run.status == "running")
            .limit(1)
        ):
            raise HTTPException(409, "Stop this user’s runs before changing limits.")
        limit = db.get(UserLimit, uid)
        if not limit:
            limit = UserLimit(user_id=uid)
            db.add(limit)
            db.flush()
        limit.monthly_budget = body.monthly_budget
        limit.models = list(dict.fromkeys(body.models))
        limit.concurrent_runs = body.concurrent_runs
        limit.max_output_tokens = body.max_output_tokens
        limit.revision += 1
        gateway.mark_revoke(db, uid)
        audit(db, actor, "limits.update", uid)
        db.commit()
        return policy.limit_dto(db, uid)


@router.post("/api/admin/users/{uid}/key/rotate")
async def rotate(uid: str, request: Request, ctx=Depends(admin_context)):
    async with chat.admission:
        db, _, _ = ctx
        db.expire_all()
        actor, _ = authenticate(db, request.cookies.get(COOKIE))
        if actor.role != "admin" or actor.must_change_password:
            raise HTTPException(403, "Administrator access required.")
        limit = db.get(UserLimit, uid)
        if not limit:
            raise HTTPException(404, "Limits not configured.")
        if db.scalar(
            select(Run.id)
            .join(Chat)
            .where(Chat.owner_id == uid, Run.status == "running")
            .limit(1)
        ):
            raise HTTPException(409, "Stop this user’s runs first.")
        gateway.mark_revoke(db, uid)
        limit.revision += 1
        audit(db, actor, "key.rotate", uid)
        db.commit()
        return {
            "state": "revocation_queued",
            "message": "Old keys will be blocked; a new key is provisioned on the next permitted call.",
        }


@router.post("/api/admin/usage/{cid}/resolve")
def resolve(cid: str, body: Resolution, ctx=Depends(admin_context)):
    db, actor, _ = ctx
    row = db.scalar(select(UsageRecord).where(UsageRecord.id == cid).with_for_update())
    if not row:
        raise HTTPException(404, "Usage record not found.")
    if row.cost is not None:
        raise HTTPException(409, "This call is already settled.")
    run = db.get(Run, row.run_id)
    if run and run.status == "running":
        raise HTTPException(409, "Stop or finish the run before reconciliation.")
    row.cost = body.cost
    row.status = "admin_reconciled"
    row.reconciliation_note = body.reason
    audit(db, actor, "usage.resolve", cid)
    return {"id": cid, "status": row.status}


@router.get("/api/admin/users/{uid}/pending")
def pending(uid: str, offset: int = Query(default=0, ge=0), ctx=Depends(admin_context)):
    db, actor, _ = ctx
    if not db.get(User, uid):
        raise HTTPException(404, "User not found.")
    rows = list(
        db.scalars(
            select(UsageRecord)
            .where(UsageRecord.user_id == uid, UsageRecord.cost.is_(None))
            .order_by(UsageRecord.created_at, UsageRecord.id)
            .offset(offset)
            .limit(101)
        )
    )
    audit(db, actor, "usage.pending.read", uid)
    return {
        "has_more": len(rows) > 100,
        "calls": [
            {
                "id": r.id,
                "period": r.period,
                "model": r.model,
                "cost": None,
                "reserved": float(r.reserved),
                "status": r.status,
                "created_at": r.created_at,
            }
            for r in rows[:100]
        ],
    }
