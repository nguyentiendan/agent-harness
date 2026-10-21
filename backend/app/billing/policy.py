import os
import time
import uuid
import threading
from types import SimpleNamespace
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import select
from fastapi import HTTPException
from app.platform.db import (
    DB,
    User,
    UserLimit,
    UsageRecord,
    Run,
    Chat,
    CatalogModel,
    GatewayKey,
)

lock = threading.RLock()


def enabled():
    return os.environ.get("PLATFORM_LIMITS_ENABLED", "true").lower() == "true"


def month(at=None):
    return datetime.fromtimestamp(
        time.time() if at is None else at, timezone.utc
    ).strftime("%Y-%m")


def money(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0:
            raise ValueError()
        return result
    except Exception:
        raise ValueError("Invalid nonnegative cost.") from None


def totals(db, uid, period):
    rows = list(
        db.scalars(
            select(UsageRecord).where(
                UsageRecord.user_id == uid, UsageRecord.period == period
            )
        )
    )
    confirmed = sum((money(r.cost) for r in rows if r.cost is not None), Decimal(0))
    pending = sum((money(r.reserved) for r in rows if r.cost is None), Decimal(0))
    return confirmed, pending, len([r for r in rows if r.cost is None])


def access(db, uid, model=None, check_budget=True, metadata=None):
    user = db.get(User, uid)
    limit = db.get(UserLimit, uid)
    if not user or not user.active or user.must_change_password:
        raise HTTPException(403, "AI access is disabled for this account.")
    if (
        metadata
        and metadata.get("id") == model
        and metadata.get("price_class") == "free"
    ):
        return SimpleNamespace(
            monthly_budget=Decimal(0),
            models=[model],
            concurrent_runs=4,
            max_output_tokens=limit.max_output_tokens if limit else 4096,
        )
    if (
        not limit
        or limit.monthly_budget is None
        or limit.monthly_budget <= 0
        or not limit.models
    ):
        raise HTTPException(
            403, "Ask an administrator to assign your AI budget and models first."
        )
    if model and model not in limit.models:
        raise HTTPException(403, "This model is not allowed for your account.")
    confirmed, pending, _ = totals(db, uid, month())
    if check_budget and confirmed + pending >= money(limit.monthly_budget):
        raise HTTPException(
            429, "Monthly AI budget is exhausted or reserved by pending calls."
        )
    return limit


def reserve(uid, rid, model, ceiling, metadata=None):
    with lock, DB.begin() as db:
        db.scalar(select(UserLimit).where(UserLimit.user_id == uid).with_for_update())
        limit = access(db, uid, model, metadata=metadata)
        run = db.get(Run, rid)
        if (
            not run
            or run.status != "running"
            or db.get(Chat, run.session_id).owner_id != uid
        ):
            raise HTTPException(409, "Run is no longer active.")
        period = month()
        confirmed, pending, _ = totals(db, uid, period)
        bound = money(ceiling)
        free = bool(
            metadata
            and metadata.get("id") == model
            and metadata.get("price_class") == "free"
        )
        if (free and bound != 0) or (
            not free
            and (
                bound <= 0 or confirmed + pending + bound > money(limit.monthly_budget)
            )
        ):
            raise HTTPException(
                429, "Not enough unreserved budget for this model call."
            )
        record = UsageRecord(
            id=str(uuid.uuid4()),
            user_id=uid,
            run_id=rid,
            period=period,
            model=model,
            reserved=bound,
            price_snapshot=metadata,
        )
        db.add(record)
        db.flush()
        return {
            "id": record.id,
            "period": period,
            "budget": None
            if free
            else money(limit.monthly_budget) - confirmed - pending,
            "max_tokens": limit.max_output_tokens,
        }


def settle(call_id, cost=None, request_id=None, usage=None, status="settled"):
    with lock, DB.begin() as db:
        row = db.scalar(
            select(UsageRecord).where(UsageRecord.id == call_id).with_for_update()
        )
        if not row or row.cost is not None:
            return
        if request_id:
            row.gateway_request_id = str(request_id)[:200]
        if usage:
            for k, field in [
                ("prompt_tokens", "input_tokens"),
                ("completion_tokens", "output_tokens"),
            ]:
                n = usage.get(k)
                if isinstance(n, int) and 0 <= n < 2**31:
                    setattr(row, field, n)
        if cost is not None:
            row.cost = money(cost)
            row.status = status
            block_free_anomaly(db, row)


def limit_dto(db, uid):
    limit = db.get(UserLimit, uid)
    return {
        "user_id": uid,
        "monthly_budget": float(limit.monthly_budget)
        if limit and limit.monthly_budget is not None
        else None,
        "models": limit.models if limit else [],
        "concurrent_runs": limit.concurrent_runs if limit else 1,
        "max_output_tokens": limit.max_output_tokens if limit else 4096,
        "sandbox_profile": "1 CPU / 1 GiB RAM / 128 PIDs / offline",
    }


def summary(db, uid, period=None):
    period = period or month()
    confirmed, pending, count = totals(db, uid, period)
    data = limit_dto(db, uid)
    budget = data["monthly_budget"]
    rows = list(
        db.scalars(
            select(UsageRecord)
            .where(UsageRecord.user_id == uid, UsageRecord.period == period)
            .order_by(UsageRecord.created_at.desc())
            .limit(100)
        )
    )
    return {
        **data,
        "period": period,
        "enforced": enabled(),
        "confirmed_cost": float(confirmed),
        "pending_reserved": float(pending),
        "pending_calls": count,
        "available": max(0, float(money(budget) - confirmed - pending))
        if budget is not None
        else None,
        "overspend": max(0, float(confirmed - money(budget)))
        if budget is not None
        else 0,
        "calls": [
            {
                "id": r.id,
                "run_id": r.run_id,
                "model": r.model,
                "price_class": (r.price_snapshot or {}).get("price_class", "paid"),
                "cost": float(r.cost) if r.cost is not None else None,
                "reserved": float(r.reserved),
                "status": r.status,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "created_at": r.created_at,
            }
            for r in rows
        ],
    }


def concurrency_cap(requested, active_caps):
    return min([requested, *active_caps])


def block_free_anomaly(db, usage):
    if (
        (usage.price_snapshot or {}).get("price_class") != "free"
        or usage.cost is None
        or usage.cost <= 0
    ):
        return
    model = db.get(CatalogModel, usage.model)
    if model:
        model.blocked = True
        for key in db.scalars(
            select(GatewayKey).where(
                GatewayKey.model_scope == model.payload["revision"],
                GatewayKey.state.in_(["active", "provisioning"]),
            )
        ):
            key.state = "revoke_pending"
