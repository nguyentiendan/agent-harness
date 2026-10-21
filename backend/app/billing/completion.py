"""One reserved attempt, one per-user gateway request, no retry or master-key fallback."""

import asyncio
import os
from types import SimpleNamespace
import httpx
from . import policy, gateway, catalog
from app.platform.db import DB, Run
from fastapi import HTTPException


def objectify(value):
    if isinstance(value, dict):
        return SimpleNamespace(**{k: objectify(v) for k, v in value.items()})
    if isinstance(value, list):
        return [objectify(v) for v in value]
    return value


async def complete(uid, rid, messages, model, tools, temperature, max_tokens):
    if not uid or not rid:
        raise gateway.GatewayError("AI calls require an authenticated user and run.")
    metadata = await asyncio.to_thread(catalog.lookup, model)
    if metadata:
        price = metadata
        route = await asyncio.to_thread(catalog.ensure_route, metadata)
    else:
        prices = await asyncio.to_thread(gateway.priced_models)
        if model not in prices:
            raise gateway.GatewayError(
                "This model has no verified pricing/context limits; AI call blocked."
            )
        price = prices[model]
        route = model
    reservation = policy.reserve(uid, rid, model, price["ceiling"], metadata=metadata)
    # Provisioning failure has no provider call; release precisely this reservation.
    try:
        key = await asyncio.to_thread(
            gateway.user_key,
            uid,
            reservation["period"],
            reservation["budget"],
            route,
            metadata,
        )
        if metadata:
            current = await asyncio.to_thread(catalog.lookup, model)
            if current["revision"] != metadata["revision"]:
                raise gateway.GatewayError(
                    "Model catalog changed before dispatch. Try again."
                )
        with DB() as db:
            policy.access(db, uid, model, check_budget=False, metadata=metadata)
            if db.get(Run, rid).status != "running":
                raise HTTPException(409, "Run stopped before model call.")
    except BaseException:
        policy.settle(reservation["id"], 0, status="not_dispatched")
        raise
    payload = {
        "model": route,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": min(
            max_tokens, reservation["max_tokens"], price.get("max_output_tokens", 4096)
        ),
        "metadata": {"platform_usage_id": reservation["id"], "platform_run_id": rid},
        "user": "platform-" + uid,
    }
    if tools:
        payload["tools"] = tools
    try:
        async with httpx.AsyncClient(
            base_url=os.environ["LITELLM_PROXY_URL"],
            headers={"Authorization": "Bearer " + key},
            timeout=180,
            trust_env=False,
        ) as client:
            r = await client.post("/chat/completions", json=payload)
        try:
            data = r.json()
        except ValueError:
            data = {}
        # Provider cost is independent evidence: a zero gateway estimate must
        # never hide an actual charge on a supposedly Free route.
        costs = []
        for evidence in (
            r.headers.get("x-litellm-response-cost"),
            (data.get("usage") or {}).get("cost"),
        ):
            if evidence is not None:
                try:
                    costs.append(policy.money(evidence))
                except ValueError:
                    pass
        cost = max(costs) if costs else None
        request_id = data.get("id")
        policy.settle(reservation["id"], cost, request_id, data.get("usage"))
        if metadata and metadata["price_class"] == "free" and cost is not None:
            try:
                if policy.money(cost) > 0:
                    catalog.disable(model)
                    raise gateway.GatewayError(
                        "Unexpected cost on a Free route. This route has been disabled."
                    )
            except ValueError:
                pass
        if r.status_code == 429:
            raise gateway.GatewayError(
                "Model provider or gateway rate limit reached. Wait before trying again; no automatic retry was sent."
            )
        if not r.is_success:
            raise gateway.GatewayError(
                "AI gateway rejected the request. Usage may remain pending until reconciled."
            )
        for choice in data.get("choices", []):
            choice["message"].setdefault("tool_calls", [])
        if not data.get("choices"):
            raise gateway.GatewayError("AI gateway returned no completion.")
        return objectify(data)
    except (httpx.HTTPError, KeyError, ValueError):
        # Gateway may have dispatched upstream even if no response arrived. Keep reserve.
        raise gateway.GatewayError(
            "AI gateway unavailable. This call remains pending; no retry was sent."
        ) from None
