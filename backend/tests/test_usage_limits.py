import support
import os, unittest
from unittest.mock import patch
import test_platform_auth as auth_tests


class LimitsTests(unittest.TestCase):
    setUp = auth_tests.PlatformTests.setUp
    login = auth_tests.PlatformTests.login
    new_chat = auth_tests.PlatformTests.new_chat

    def test_missing_budget_blocks_run(self):
        self.login()
        sid = self.new_chat()
        with patch.dict(os.environ, {"PLATFORM_LIMITS_ENABLED": "true"}):
            r = self.client.post(
                "/api/harness/start",
                json={"session_id": sid, "request_id": "quota", "goal": "hi"},
            )
        self.assertEqual(r.status_code, 403, r.text)

    def test_usage_private_and_admin_limits_routes(self):
        self.login()
        self.assertEqual(self.client.get("/api/usage").status_code, 200)
        self.assertEqual(
            self.client.get("/api/admin/users/" + self.b + "/limits").status_code, 403
        )
        self.login("admin", "Admin password 123!")
        r = self.client.get("/api/admin/users/" + self.a + "/limits")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNone(r.json()["monthly_budget"])

    def configured_run(self, budget="1"):
        import uuid
        from app.platform.db import DB, UserLimit, Chat, Run

        self.login()
        sid = self.new_chat()
        rid = str(uuid.uuid4())
        with DB.begin() as db:
            db.add(
                UserLimit(user_id=self.a, monthly_budget=budget, models=["gpt-4o-mini"])
            )
            db.get(Chat, sid).status = "running"
            db.add(
                Run(
                    id=rid,
                    session_id=sid,
                    request_id=rid,
                    body_hash="test",
                    model="gpt-4o-mini",
                    event_offset=0,
                )
            )
        return rid

    def test_reservation_idempotency_pending_and_month_rollover(self):
        from app.billing import policy
        from app.platform.db import DB
        from fastapi import HTTPException

        rid = self.configured_run(".1")
        with patch("app.billing.policy.month", return_value="2026-10"):
            r = policy.reserve(self.a, rid, "gpt-4o-mini", ".1")
            with self.assertRaises(HTTPException):
                policy.reserve(self.a, rid, "gpt-4o-mini", ".01")
            with DB() as db:
                s = policy.summary(db, self.a)
                self.assertEqual(s["pending_calls"], 1)
                self.assertEqual(s["confirmed_cost"], 0)
            policy.settle(r["id"], ".02")
            policy.settle(r["id"], ".03")
            with DB() as db:
                self.assertEqual(policy.summary(db, self.a)["confirmed_cost"], 0.02)
        with patch("app.billing.policy.month", return_value="2026-11"):
            with DB() as db:
                self.assertEqual(policy.summary(db, self.a)["confirmed_cost"], 0)
            policy.reserve(self.a, rid, "gpt-4o-mini", ".1")
        self.assertEqual(policy.month(1767225599), "2025-12")
        self.assertEqual(policy.month(1767225600), "2026-01")

    def test_per_user_key_and_unknown_cost_no_retry(self):
        import asyncio, httpx
        from app.billing import completion, policy, gateway
        from app.platform.db import DB

        rid = self.configured_run()
        real_client = httpx.AsyncClient
        seen = []

        def transport(request):
            seen.append(request.headers["Authorization"])
            raise httpx.ReadTimeout("ambiguous timeout")

        def client(**kw):
            return real_client(transport=httpx.MockTransport(transport), **kw)

        with (
            patch.dict(
                os.environ,
                {
                    "LITELLM_PROXY_URL": "http://gateway",
                    "LITELLM_MASTER_KEY": "never-use-this",
                },
            ),
            patch.object(
                gateway,
                "priced_models",
                return_value={"gpt-4o-mini": {"ceiling": ".1"}},
            ),
            patch.object(gateway, "user_key", return_value="sk-user-only"),
            patch("app.billing.completion.httpx.AsyncClient", side_effect=client),
        ):
            with self.assertRaises(gateway.GatewayError):
                asyncio.run(
                    completion.complete(
                        self.a,
                        rid,
                        [{"role": "user", "content": "hi"}],
                        "gpt-4o-mini",
                        None,
                        0.2,
                        100,
                    )
                )
        self.assertEqual(seen, ["Bearer sk-user-only"])
        with DB() as db:
            s = policy.summary(db, self.a)
            self.assertEqual(s["pending_calls"], 1)
            self.assertEqual(s["pending_reserved"], 0.1)

    def test_confirmed_overspend_blocks_next_call_and_no_identity_denied(self):
        import asyncio, httpx
        from app.billing import completion, policy, gateway
        from app.platform.db import DB
        from fastapi import HTTPException

        rid = self.configured_run(".1")
        r = policy.reserve(self.a, rid, "gpt-4o-mini", ".1")
        policy.settle(r["id"], ".11")
        with DB() as db:
            self.assertEqual(policy.summary(db, self.a)["overspend"], 0.01)
            with self.assertRaises(HTTPException):
                policy.access(db, self.a, "gpt-4o-mini")
        with self.assertRaises(gateway.GatewayError):
            asyncio.run(
                completion.complete(None, None, [], "gpt-4o-mini", None, 0.2, 10)
            )

    def test_model_denial_reviewer_and_concurrent_reservations(self):
        from app.billing import policy
        from app.platform.db import DB, Run, Chat
        from concurrent.futures import ThreadPoolExecutor
        from fastapi import HTTPException

        rid = self.configured_run(".1")

        def attempt(_):
            try:
                policy.reserve(self.a, rid, "gpt-4o-mini", ".1")
                return True
            except HTTPException:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(attempt, [1, 2])), [False, True])
        with DB() as db:
            with self.assertRaises(HTTPException):
                policy.access(db, self.a, "gpt-4o")

    def test_encrypted_key_revocation_and_limits_validation(self):
        from app.billing import gateway, policy
        from app.platform.db import DB, GatewayKey
        from sqlalchemy import select
        from cryptography.fernet import Fernet

        rid = self.configured_run()
        with (
            patch.dict(
                os.environ,
                {"PLATFORM_KEY_ENCRYPTION_KEY": Fernet.generate_key().decode()},
            ),
            patch.object(gateway, "call", return_value={}),
        ):
            raw = gateway.user_key(self.a, policy.month(), 1)
            with DB.begin() as db:
                key = db.scalar(select(GatewayKey))
                self.assertNotIn(raw, key.encrypted_key)
                self.assertEqual(key.state, "active")
                gateway.mark_revoke(db, self.a)
            gateway.revoke_pending(self.a)
            with DB() as db:
                self.assertEqual(db.scalar(select(GatewayKey)).state, "revoked")
        self.login("admin", "Admin password 123!")
        with patch.object(
            gateway, "priced_models", return_value={"gpt-4o-mini": {"ceiling": ".1"}}
        ):
            r = self.client.put(
                "/api/admin/users/" + self.a + "/limits",
                json={
                    "monthly_budget": 1,
                    "models": ["gpt-4o-mini"],
                    "concurrent_runs": 5,
                },
            )
            self.assertEqual(r.status_code, 422)

    def test_reconcile_uses_response_id_and_is_idempotent(self):
        from app.billing import policy, gateway, reconcile
        from app.platform.db import DB

        rid = self.configured_run()
        r = policy.reserve(self.a, rid, "gpt-4o-mini", ".1")
        policy.settle(r["id"], request_id="chatcmpl-evidence")
        with (
            patch.object(gateway, "revoke_pending"),
            patch.object(
                gateway,
                "call",
                return_value=[
                    {
                        "request_id": "chatcmpl-evidence",
                        "user": "platform-" + self.b,
                        "spend": ".01",
                    }
                ],
            ),
        ):
            reconcile.reconcile()
        with DB() as db:
            self.assertEqual(policy.summary(db, self.a)["pending_calls"], 1)
        with (
            patch.object(gateway, "revoke_pending"),
            patch.object(
                gateway,
                "call",
                return_value=[
                    {
                        "request_id": "chatcmpl-evidence",
                        "user": "platform-" + self.a,
                        "spend": ".01",
                    }
                ],
            ),
        ):
            reconcile.reconcile()
            reconcile.reconcile()
        with DB() as db:
            self.assertEqual(policy.summary(db, self.a)["confirmed_cost"], 0.01)
            self.assertEqual(policy.summary(db, self.a)["pending_calls"], 0)

    def test_limits_update_and_manual_resolution_authorization(self):
        from app.billing import policy, gateway
        from app.platform.db import DB, Run

        rid = self.configured_run()
        r = policy.reserve(self.a, rid, "gpt-4o-mini", ".1")
        self.login("bob", "Bob password 123!")
        self.assertEqual(
            self.client.get("/api/admin/users/" + self.a + "/usage").status_code, 403
        )
        self.assertEqual(self.client.get("/api/usage").json()["calls"], [])
        self.login("admin", "Admin password 123!")
        path = "/api/admin/usage/" + r["id"] + "/resolve"
        body = {"cost": ".015", "reason": "Confirmed provider invoice"}
        self.assertEqual(self.client.post(path, json=body).status_code, 409)
        with DB.begin() as db:
            db.get(Run, rid).status = "completed"
        self.assertEqual(self.client.post(path, json=body).status_code, 200)
        self.assertEqual(self.client.post(path, json=body).status_code, 409)
        with patch.object(
            gateway, "priced_models", return_value={"gpt-4o-mini": {"ceiling": ".1"}}
        ):
            response = self.client.put(
                "/api/admin/users/" + self.a + "/limits",
                json={
                    "monthly_budget": 2,
                    "models": ["gpt-4o-mini"],
                    "concurrent_runs": 2,
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["monthly_budget"], 2)

    def test_completion_cost_and_response_id(self):
        import asyncio, httpx
        from app.billing import completion, policy, gateway
        from app.platform.db import DB, UsageRecord
        from sqlalchemy import select

        rid = self.configured_run()
        real = httpx.AsyncClient

        def transport(request):
            return httpx.Response(
                200,
                headers={
                    "x-litellm-response-cost": "0.00001",
                    "x-litellm-call-id": "different",
                },
                json={
                    "id": "chatcmpl-real",
                    "choices": [{"message": {"content": "done"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 2},
                },
            )

        with (
            patch.dict(os.environ, {"LITELLM_PROXY_URL": "http://gateway"}),
            patch.object(
                gateway,
                "priced_models",
                return_value={"gpt-4o-mini": {"ceiling": ".1"}},
            ),
            patch.object(gateway, "user_key", return_value="sk-user"),
            patch.object(
                httpx,
                "AsyncClient",
                side_effect=lambda **kw: real(
                    transport=httpx.MockTransport(transport), **kw
                ),
            ),
        ):
            asyncio.run(
                completion.complete(self.a, rid, [], "gpt-4o-mini", None, 0.2, 100)
            )
        with DB() as db:
            row = db.scalar(select(UsageRecord))
            self.assertEqual(row.gateway_request_id, "chatcmpl-real")
            self.assertEqual(float(row.cost), 0.00001)
            self.assertEqual(row.output_tokens, 2)

    def test_pending_history_and_fair_reconciliation(self):
        from app.billing import policy, gateway, reconcile
        from app.platform.db import DB, UsageRecord

        rid = self.configured_run("100")
        with DB.begin() as db:
            for n in range(102):
                db.add(
                    UsageRecord(
                        id=f"{n:036d}",
                        user_id=self.a,
                        run_id=rid,
                        period="2020-01",
                        model="gpt-4o-mini",
                        reserved=".01",
                        gateway_request_id=f"evidence-{n}",
                        created_at=n,
                    )
                )
        self.login("admin", "Admin password 123!")
        path = "/api/admin/users/" + self.a + "/pending"
        first = self.client.get(path).json()
        self.assertTrue(first["has_more"])
        self.assertEqual(len(first["calls"]), 100)
        self.assertEqual(first["calls"][0]["period"], "2020-01")
        last = self.client.get(path + "?offset=100").json()
        self.assertEqual(len(last["calls"]), 2)
        self.assertFalse(last["has_more"])

        def evidence(method, path, params):
            return (
                [
                    {
                        "request_id": "evidence-101",
                        "user": "platform-" + self.a,
                        "spend": ".005",
                    }
                ]
                if params["request_id"] == "evidence-101"
                else []
            )

        with (
            patch.object(reconcile, "_cursor", ""),
            patch.object(gateway, "revoke_pending"),
            patch.object(gateway, "call", side_effect=evidence),
        ):
            reconcile.reconcile()
            reconcile.reconcile()
        with DB() as db:
            self.assertEqual(float(db.get(UsageRecord, f"{101:036d}").cost), 0.005)

    def test_cancellation_holds_reserve_and_provision_failure_releases(self):
        import asyncio, httpx
        from app.billing import completion, policy, gateway
        from app.platform.db import DB

        rid = self.configured_run()
        real = httpx.AsyncClient

        async def cancelled(request):
            raise asyncio.CancelledError()

        with (
            patch.dict(os.environ, {"LITELLM_PROXY_URL": "http://gateway"}),
            patch.object(
                gateway,
                "priced_models",
                return_value={"gpt-4o-mini": {"ceiling": ".1"}},
            ),
        ):
            with patch.object(
                gateway, "user_key", side_effect=gateway.GatewayError("offline")
            ):
                with self.assertRaises(gateway.GatewayError):
                    asyncio.run(
                        completion.complete(
                            self.a, rid, [], "gpt-4o-mini", None, 0.2, 10
                        )
                    )
            with DB() as db:
                self.assertEqual(policy.summary(db, self.a)["pending_reserved"], 0)
            with (
                patch.object(gateway, "user_key", return_value="sk-user"),
                patch.object(
                    httpx,
                    "AsyncClient",
                    side_effect=lambda **kw: real(
                        transport=httpx.MockTransport(cancelled), **kw
                    ),
                ),
            ):
                with self.assertRaises(asyncio.CancelledError):
                    asyncio.run(
                        completion.complete(
                            self.a, rid, [], "gpt-4o-mini", None, 0.2, 10
                        )
                    )
            with DB() as db:
                self.assertEqual(policy.summary(db, self.a)["pending_reserved"], 0.1)
