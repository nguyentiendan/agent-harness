"""Resolve only positively matched gateway spend evidence; absence is never zero."""

from sqlalchemy import select
from app.platform.db import DB, UsageRecord
from . import gateway, policy


_cursor = ""


def reconcile():
    global _cursor
    gateway.revoke_pending()
    with DB() as db:
        q = (
            select(UsageRecord)
            .where(
                UsageRecord.cost.is_(None), UsageRecord.gateway_request_id.is_not(None)
            )
            .order_by(UsageRecord.id)
            .limit(100)
        )
        records = list(db.scalars(q.where(UsageRecord.id > _cursor)))
        if not records:
            records = list(db.scalars(q))
        _cursor = records[-1].id if records else ""
        rows = [(r.id, r.user_id, r.gateway_request_id, r.period) for r in records]
    for cid, uid, request_id, period in rows:
        if not request_id:
            continue  # Admin can reconcile ambiguous dispatch from gateway logs explicitly.
        try:
            logs = gateway.call("GET", "/spend/logs", params={"request_id": request_id})
            if isinstance(logs, dict):
                logs = logs.get("data", logs.get("logs", []))
            matches = [
                r
                for r in logs
                if r.get("request_id") == request_id
                and r.get("user") == "platform-" + uid
            ]
            if len(matches) == 1 and matches[0].get("spend") is not None:
                policy.settle(
                    cid,
                    matches[0]["spend"],
                    usage={
                        "prompt_tokens": matches[0].get("prompt_tokens"),
                        "completion_tokens": matches[0].get("completion_tokens"),
                    },
                )
        except (gateway.GatewayError, ValueError, TypeError):
            continue
