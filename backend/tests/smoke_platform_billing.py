"""Real gateway + PostgreSQL; mock_response prevents provider traffic. Test DB only."""

import os, sys, asyncio, time, uuid
from unittest.mock import patch
from sqlalchemy.engine import make_url

url = make_url(os.environ["PLATFORM_DATABASE_URL"]).set(database="platform_test")
os.environ["TEST_DATABASE_URL"] = url.render_as_string(hide_password=False)
sys.path.insert(0, ".")
sys.path.insert(0, "tests")
from test_usage_limits import LimitsTests
from app.billing import completion, policy, gateway, reconcile
from app.platform.db import DB, GatewayKey, UsageRecord, Run, UserLimit
from sqlalchemy import select
import httpx

case = LimitsTests()
case.setUp()
alias = "platform-mock-" + uuid.uuid4().hex
model_id = str(uuid.uuid4())
# Config-level mock plus invalid credential and loopback endpoint: cannot charge a provider.
gateway.call(
    "POST",
    "/model/new",
    {
        "model_name": alias,
        "litellm_params": {
            "model": "openai/gpt-4o-mini",
            "mock_response": "Controlled mock billing success",
            "api_key": "sk-invalid-test-only",
            "api_base": "http://127.0.0.1:9",
        },
        "model_info": {
            "id": model_id,
            "mode": "chat",
            "input_cost_per_token": 0.00000015,
            "output_cost_per_token": 0.0000006,
            "max_input_tokens": 128000,
            "max_output_tokens": 16384,
        },
    },
)
original = httpx.AsyncClient.post


async def hide_cost_header(self, path, **kw):
    response = await original(self, path, **kw)
    response.headers.pop("x-litellm-response-cost", None)
    return response


try:
    rid = case.configured_run("1")
    with DB.begin() as db:
        db.get(UserLimit, case.a).models = [alias]
        db.get(Run, rid).model = alias
    with patch.object(httpx.AsyncClient, "post", hide_cost_header):
        result = asyncio.run(
            completion.complete(
                case.a,
                rid,
                [{"role": "user", "content": "test billing"}],
                alias,
                None,
                0.2,
                16,
            )
        )
    assert result.choices[0].message.content == "Controlled mock billing success"
    with DB() as db:
        row = db.scalar(select(UsageRecord))
        assert row.cost is None and row.gateway_request_id
        key = db.scalar(select(GatewayKey))
        assert key.state == "active" and "sk-" not in key.encrypted_key
        hashed = key.key_hash
    for _ in range(20):
        reconcile.reconcile()
        with DB() as db:
            usage = policy.summary(db, case.a)
        if usage["pending_calls"] == 0:
            break
        time.sleep(1)
    assert usage["pending_calls"] == 0, "No matched spend log within 20s"
    assert 0 < usage["confirmed_cost"] < 0.001
    reconcile.reconcile()
    with DB() as db:
        assert policy.summary(db, case.a)["confirmed_cost"] == usage["confirmed_cost"]
    with DB.begin() as db:
        db.get(Run, rid).status = "completed"
    case.login("admin", "Admin password 123!")
    r = case.client.post("/api/admin/users/" + case.a + "/key/rotate")
    assert r.status_code == 200, r.status_code
    gateway.revoke_pending(case.a)
    assert gateway.call("GET", "/key/info", params={"key": hashed})["info"]["blocked"]
    replacement = gateway.user_key(case.a, policy.month(), 1)
    with DB() as db:
        assert len(list(db.scalars(select(GatewayKey)))) == 2
    r = case.client.patch("/api/admin/users/" + case.a, json={"active": False})
    assert r.status_code == 200
    gateway.revoke_pending(case.a)
    with DB() as db:
        assert all(k.state == "revoked" for k in db.scalars(select(GatewayKey)))
    print(
        "PASS: real encrypted user key / mock completion / matched spend reconciliation / idempotency / rotation / disable revocation"
    )
    print(
        "Measured mock call cost USD:",
        usage["confirmed_cost"],
        "; overspend USD:",
        usage["overspend"],
    )
finally:
    with DB() as db:
        hashes = list(db.scalars(select(GatewayKey.key_hash)))
    if hashes:
        gateway.call("POST", "/key/delete", {"keys": hashes})
    # Gateway key creation does not create a user row in this pinned version.
    # Delete only the explicitly provisioned keys above.
    gateway.call("POST", "/model/delete", {"id": model_id})
    case.doCleanups()
