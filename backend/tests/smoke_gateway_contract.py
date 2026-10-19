"""Pinned real gateway contract using mock_response (no provider request)."""

import os, uuid, hashlib, secrets
from decimal import Decimal
import httpx

base = os.environ["LITELLM_PROXY_URL"]
master = os.environ["LITELLM_MASTER_KEY"]
uid = "platform-contract-" + uuid.uuid4().hex
alias = "platform-mock-" + uuid.uuid4().hex
model_id = str(uuid.uuid4())
raw = "sk-" + secrets.token_urlsafe(32)
hashed = hashlib.sha256(raw.encode()).hexdigest()
with httpx.Client(
    base_url=base,
    headers={"Authorization": "Bearer " + master},
    timeout=30,
    trust_env=False,
) as admin:
    try:
        r = admin.post(
            "/model/new",
            json={
                "model_name": alias,
                "litellm_params": {
                    "model": "openai/gpt-4o-mini",
                    "mock_response": "mock contract success",
                    "api_key": "sk-invalid-test-only",
                    "api_base": "http://127.0.0.1:9",
                },
                "model_info": {"id": model_id},
            },
        )
        assert r.status_code == 200, ("mock_model_create", r.status_code)
        r = admin.post(
            "/key/generate",
            json={
                "key": raw,
                "user_id": uid,
                "models": [alias],
                "max_budget": 0.001,
                "duration": "600s",
                "metadata": {"purpose": "platform-contract-test"},
            },
        )
        assert r.status_code == 200, ("key_generate", r.status_code)
        assert r.json()["key"] == raw
        info = admin.get("/key/info", params={"key": hashed})
        assert info.status_code == 200
        assert info.json()["info"]["user_id"] == uid
        with httpx.Client(
            base_url=base,
            headers={"Authorization": "Bearer " + raw},
            timeout=30,
            trust_env=False,
        ) as user:
            r = user.post(
                "/chat/completions",
                json={
                    "model": alias,
                    "messages": [{"role": "user", "content": "contract test"}],
                    "max_tokens": 16,
                    "mock_response": "mock contract success",
                    "metadata": {"platform_usage_id": "contract-" + uid},
                },
            )
            assert r.status_code == 200, ("mock_completion", r.status_code)
            assert (
                r.json()["choices"][0]["message"]["content"] == "mock contract success"
            )
            print(
                "mock usage",
                r.json().get("usage"),
                "cost header",
                r.headers.get("x-litellm-response-cost"),
            )
            print(
                "request identifiers",
                {
                    "call_header": bool(r.headers.get("x-litellm-call-id")),
                    "response_id": bool(r.json().get("id")),
                },
            )
            response_id = r.json().get("id")
            call_id = r.headers.get("x-litellm-call-id")
            r = admin.post("/key/update", json={"key": raw, "max_budget": 0.000000001})
            assert r.status_code == 200, ("update", r.status_code)
            r = user.post(
                "/chat/completions",
                json={
                    "model": alias,
                    "messages": [{"role": "user", "content": "budget denial"}],
                    "max_tokens": 16,
                    "mock_response": "must be blocked",
                },
            )
            assert r.status_code in (400, 401, 403, 422, 429), (
                "budget_guard",
                r.status_code,
            )
            assert "budget" in str(r.json()).lower(), "Rejection was not a budget guard"
            print("tiny-budget rejection", r.status_code)
            r = admin.post("/key/block", json={"key": hashed})
            assert r.status_code == 200, ("block", r.status_code)
            r = user.get("/models")
            assert r.status_code in (401, 403), ("blocked_key", r.status_code)
            print("blocked-key rejection", r.status_code)
        # API fields only; don't print prompts, keys or full log records.
        import time

        for _ in range(20):
            time.sleep(1)
            logs = admin.get("/spend/logs", params={"request_id": response_id}).json()
            if isinstance(logs, list) and logs:
                row = logs[0]
                print(
                    "spend evidence",
                    {
                        "request_id_matches_response": row.get("request_id")
                        == response_id,
                        "request_id_matches_call": row.get("request_id") == call_id,
                        "user_matches": row.get("user") == uid,
                        "spend": row.get("spend"),
                        "status": row.get("status"),
                    },
                )
                break
        else:
            print("Spend log not visible within 20s; reconciliation remains pending.")
        print(
            "PASS: explicit key, hashed lookup/block, mock completion, budget update/denial"
        )
    finally:
        admin.post("/key/delete", json={"keys": [hashed]})
        admin.post("/model/delete", json={"id": model_id})
