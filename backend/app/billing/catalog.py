"""Verified OpenRouter text/tool catalog; metadata alone never grants Paid access."""

import hashlib
import json
import os
import re
import threading
import time
from decimal import Decimal, InvalidOperation
import httpx
from sqlalchemy import select
from app.platform.db import DB, CatalogModel, GatewayKey
from . import gateway

TTL = 300
_lock = threading.RLock()


def enabled():
    return os.environ.get("OPENROUTER_ENABLED", "false").lower() == "true"


def normalize(item):
    try:
        mid = item["id"]
        if (
            not isinstance(mid, str)
            or len(mid) > 100
            or not re.fullmatch(r"[A-Za-z0-9_-]+/[A-Za-z0-9_.:-]+", mid)
        ):
            return None
        arch = item.get("architecture") or {}
        if (
            "text" not in arch.get("output_modalities", [])
            or "text" not in arch.get("input_modalities", [])
            or "tools" not in item.get("supported_parameters", [])
        ):
            return None
        pricing = item["pricing"]
        a, b = Decimal(pricing["prompt"]), Decimal(pricing["completion"])
        all_prices = [Decimal(str(v)) for v in pricing.values()]
        if any(not v.is_finite() or v < 0 for v in all_prices):
            return None
        # V1 only handles token pricing; nonzero request/search/image surcharges excluded.
        if any(
            Decimal(str(v)) != 0
            for k, v in pricing.items()
            if k
            not in ("prompt", "completion", "input_cache_read", "input_cache_write")
        ):
            return None
        i, o = (
            int(item["context_length"]),
            int(item["top_provider"]["max_completion_tokens"]),
        )
        if min(i, o) <= 0 or max(i, o) > 10000000:
            return None
        free = all(v == 0 for v in all_prices)
        if free and not mid.endswith(":free"):
            return None
        if mid.endswith(":free") and not free:
            return None
        input_price = max(
            [a]
            + [
                Decimal(str(v))
                for k, v in pricing.items()
                if k.startswith("input_cache_")
            ]
        )
        result = {
            "id": "openrouter/" + mid,
            "provider_model": mid,
            "name": str(item.get("name") or mid)[:200],
            "provider": "OpenRouter",
            "price_class": "free" if free else "paid",
            "input_price": str(input_price),
            "output_price": str(b),
            "max_input_tokens": i,
            "max_output_tokens": o,
            "ceiling": str(input_price * i + b * o),
        }
        result["revision"] = hashlib.sha256(
            json.dumps(result, sort_keys=True).encode()
        ).hexdigest()
        return result
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return None


def fetch():
    try:
        with httpx.Client(timeout=20, trust_env=False) as c:
            r = c.get("https://openrouter.ai/api/v1/models")
            r.raise_for_status()
            data = r.json()["data"]
        if not isinstance(data, list) or len(data) > 10000:
            raise ValueError()
        normalized = [value for item in data if (value := normalize(item))]
        if not normalized:
            raise ValueError()
        return normalized
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        raise gateway.GatewayError(
            "Model catalog is unavailable. Try again later."
        ) from None


def refresh():
    if not enabled():
        return
    with _lock:
        items = fetch()
        now = time.time()
        with DB.begin() as db:
            previous = {r.id: r for r in db.scalars(select(CatalogModel))}
            for item in items:
                row = previous.pop(item["id"], None)
                if row is None:
                    row = CatalogModel(id=item["id"])
                    db.add(row)
                if row.payload and row.payload.get("revision") != item["revision"]:
                    for key in db.scalars(
                        select(GatewayKey).where(
                            GatewayKey.model_scope == row.payload["revision"],
                            GatewayKey.state.in_(["active", "provisioning"]),
                        )
                    ):
                        key.state = "revoke_pending"
                row.payload = item
                row.updated_at = now
                row.present = True
            for row in previous.values():
                row.present = False
                for key in db.scalars(
                    select(GatewayKey).where(
                        GatewayKey.model_scope == row.payload["revision"],
                        GatewayKey.state.in_(["active", "provisioning"]),
                    )
                ):
                    key.state = "revoke_pending"


def entries():
    if not enabled():
        return []
    with DB() as db:
        rows = list(
            db.scalars(select(CatalogModel).where(CatalogModel.present == True))
        )
    if not rows or min(r.updated_at for r in rows) < time.time() - TTL:
        refresh()
        with DB() as db:
            rows = list(
                db.scalars(select(CatalogModel).where(CatalogModel.present == True))
            )
    return [
        {**r.payload, "blocked": r.blocked, "updated_at": r.updated_at} for r in rows
    ]


def lookup(model):
    if not model.startswith("openrouter/"):
        return None
    item = next((m for m in entries() if m["id"] == model), None)
    if not item or item["blocked"]:
        raise gateway.GatewayError("This model is unavailable or disabled.")
    return item


def disable(model):
    with DB.begin() as db:
        row = db.get(CatalogModel, model)
        if row:
            row.blocked = True
            for key in db.scalars(
                select(GatewayKey).where(
                    GatewayKey.model_scope == row.payload["revision"],
                    GatewayKey.state.in_(["active", "provisioning"]),
                )
            ):
                key.state = "revoke_pending"


def ensure_route(item):
    """Immutable route revision; changed pricing never reuses old key/route permissions."""
    route = "platform-or-" + item["revision"]
    with _lock:
        with DB() as db:
            row = db.get(CatalogModel, item["id"])
            if (
                not row
                or row.blocked
                or not row.present
                or row.payload["revision"] != item["revision"]
            ):
                raise gateway.GatewayError("Model catalog changed. Try again.")
            if row.route_revision == item["revision"]:
                return route
        provider = {"allow_fallbacks": False, "require_parameters": True}
        if item["price_class"] == "free":
            provider["max_price"] = {"prompt": 0, "completion": 0, "request": 0}
        else:
            provider["max_price"] = {
                "prompt": float(Decimal(item["input_price"]) * 1000000),
                "completion": float(Decimal(item["output_price"]) * 1000000),
                "request": 0,
            }
        body = {
            "model_name": route,
            "litellm_params": {
                "model": item["id"],
                "api_key": "os.environ/OPENROUTER_API_KEY",
                "provider": provider,
                "input_cost_per_token": float(item["input_price"]),
                "output_cost_per_token": float(item["output_price"]),
            },
            "model_info": {
                "id": route,
                "mode": "chat",
                "max_input_tokens": item["max_input_tokens"],
                "max_output_tokens": item["max_output_tokens"],
                "input_cost_per_token": float(item["input_price"]),
                "output_cost_per_token": float(item["output_price"]),
            },
        }
        try:
            gateway.call("POST", "/model/new", body)
        except gateway.GatewayError:
            existing = gateway.call("GET", "/model/info", params={"model": route}).get(
                "data", []
            )
            if not any(
                r.get("model_name") == route
                and (r.get("model_info") or {}).get("id") == route
                for r in existing
            ):
                raise
        with DB.begin() as db:
            row = db.get(CatalogModel, item["id"])
            if row and row.payload["revision"] == item["revision"]:
                row.route_revision = item["revision"]
        return route
