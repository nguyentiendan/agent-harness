import hashlib
import os
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone
from cryptography.fernet import Fernet
import httpx
from sqlalchemy import select
from app.platform.db import DB, GatewayKey, UserLimit, User


class GatewayError(RuntimeError):
    pass


key_lock = threading.RLock()


def cipher():
    try:
        return Fernet(os.environ["PLATFORM_KEY_ENCRYPTION_KEY"].encode())
    except Exception:
        raise GatewayError("Gateway key encryption is not configured.") from None


def call(method, path, body=None, params=None):
    try:
        base = os.environ["LITELLM_PROXY_URL"]
        master = os.environ["LITELLM_MASTER_KEY"]
        if not master:
            raise ValueError()
        with httpx.Client(
            base_url=base,
            headers={"Authorization": "Bearer " + master},
            timeout=20,
            trust_env=False,
        ) as c:
            r = c.request(method, path, json=body, params=params)
        if not r.is_success:
            raise GatewayError("AI gateway management request failed.")
        return r.json()
    except (httpx.HTTPError, KeyError, ValueError):
        raise GatewayError("AI gateway management is unavailable.") from None


def priced_models():
    result = {}
    for item in call("GET", "/model/info").get("data", []):
        info = item.get("model_info") or {}
        fields = [
            "input_cost_per_token",
            "output_cost_per_token",
            "max_input_tokens",
            "max_output_tokens",
        ]
        if info.get("mode") != "chat" or any(info.get(k) is None for k in fields):
            continue
        from decimal import Decimal

        try:
            a, b = (Decimal(str(info[k])) for k in fields[:2])
            i, o = (int(info[k]) for k in fields[2:])
            if (
                not a.is_finite()
                or not b.is_finite()
                or min(a, b) < 0
                or min(i, o) <= 0
            ):
                continue
            # Reserve full context to avoid undercounting schemas, reasoning and cached input.
            ceiling = a * i + b * o
            if not ceiling.is_finite() or ceiling <= 0:
                continue
            result[item["model_name"]] = {
                "ceiling": str(ceiling),
                "max_input_tokens": i,
                "max_output_tokens": o,
            }
        except (ValueError, TypeError, ArithmeticError):
            continue
    return result


def revoke_pending(uid=None):
    with key_lock:
        with DB() as db:
            q = select(GatewayKey).where(GatewayKey.state == "revoke_pending")
            if uid:
                q = q.where(GatewayKey.user_id == uid)
            keys = [(k.id, k.key_hash) for k in db.scalars(q)]
        for kid, hashed in keys:
            # Hashed key accepted by the pinned gateway API; no raw key in URLs/logs.
            call("POST", "/key/block", {"key": hashed})
            with DB.begin() as db:
                k = db.get(GatewayKey, kid)
                if k:
                    k.state = "revoked"


def mark_revoke(db, uid):
    for k in db.scalars(
        select(GatewayKey).where(
            GatewayKey.user_id == uid, GatewayKey.state.in_(["active", "provisioning"])
        )
    ):
        k.state = "revoke_pending"


def user_key(uid, period, budget, model=None, metadata=None):
    with key_lock:
        revoke_pending(uid)
        with DB.begin() as db:
            user = db.get(User, uid)
            limit = db.get(UserLimit, uid)
            free = bool(metadata and metadata.get("price_class") == "free")
            revision = limit.revision if limit else 0
            scope = (
                metadata["revision"]
                if metadata
                else (hashlib.sha256(model.encode()).hexdigest() if model else "legacy")
            )
            if not user or not user.active or (not free and not limit):
                raise GatewayError("User has no AI access.")
            key = db.scalar(
                select(GatewayKey).where(
                    GatewayKey.user_id == uid,
                    GatewayKey.period == period,
                    GatewayKey.revision == revision,
                    GatewayKey.model_scope == scope,
                    GatewayKey.state.in_(["active", "provisioning"]),
                )
            )
            if not key:
                now = datetime.now(timezone.utc)
                end = (
                    now.replace(
                        year=now.year + 1,
                        month=1,
                        day=1,
                        hour=0,
                        minute=0,
                        second=0,
                        microsecond=0,
                    )
                    if now.month == 12
                    else now.replace(
                        month=now.month + 1,
                        day=1,
                        hour=0,
                        minute=0,
                        second=0,
                        microsecond=0,
                    )
                )
                raw = "sk-" + secrets.token_urlsafe(32)
                key = GatewayKey(
                    id=str(uuid.uuid4()),
                    user_id=uid,
                    period=period,
                    revision=revision,
                    model_scope=scope,
                    encrypted_key=cipher().encrypt(raw.encode()).decode(),
                    key_hash=hashlib.sha256(raw.encode()).hexdigest(),
                    expires_at=end.timestamp(),
                )
                db.add(key)
                db.flush()
            snapshot = {
                k: getattr(key, k)
                for k in ["id", "key_hash", "encrypted_key", "state", "expires_at"]
            }
            models = [model] if model else list(limit.models)
            parallel = 4 if free else limit.concurrent_runs
        raw = cipher().decrypt(snapshot["encrypted_key"].encode()).decode()
        if snapshot["state"] != "active":
            # A known key is persisted before remote create, so retries never create unlimited orphan keys.
            try:
                call(
                    "POST",
                    "/key/generate",
                    {
                        "key": raw,
                        "key_alias": "platform-" + snapshot["id"],
                        "user_id": "platform-" + uid,
                        "models": models,
                        "max_budget": None if free else float(budget),
                        "duration": str(
                            max(1, int(snapshot["expires_at"] - time.time()))
                        )
                        + "s",
                        "max_parallel_requests": parallel,
                        "metadata": {"platform_user_id": uid, "period": period},
                    },
                )
            except GatewayError:
                info = call(
                    "GET", "/key/info", params={"key": snapshot["key_hash"]}
                ).get("info", {})
                if (
                    info.get("blocked")
                    or info.get("user_id") != "platform-" + uid
                    or set(info.get("models", [])) != set(models)
                ):
                    raise GatewayError(
                        "Gateway key provisioning could not be confirmed."
                    )
            with DB.begin() as db:
                k = db.get(GatewayKey, snapshot["id"])
                if k.state != "provisioning":
                    raise GatewayError("Gateway key revoked during provisioning.")
                k.state = "active"
        return raw
