import support
import unittest
from unittest.mock import patch
import test_platform_auth as auth
from app.billing import policy
from app.platform.db import DB, UserLimit, Run, Chat
from fastapi import HTTPException

FREE = {
    "id": "openrouter/vendor/model:free",
    "price_class": "free",
    "ceiling": "0",
    "revision": "test",
    "max_output_tokens": 4096,
}
PAID = {**FREE, "id": "openrouter/vendor/paid", "price_class": "paid", "ceiling": ".1"}


class FreePolicyTests(unittest.TestCase):
    setUp = auth.PlatformTests.setUp
    login = auth.PlatformTests.login
    new_chat = auth.PlatformTests.new_chat

    def test_free_without_limits_and_paid_denied(self):
        with DB() as db:
            self.assertEqual(
                policy.access(db, self.a, FREE["id"], metadata=FREE).concurrent_runs, 4
            )
            with self.assertRaises(HTTPException):
                policy.access(db, self.a, PAID["id"], metadata=PAID)
        with DB.begin() as db:
            db.add(UserLimit(user_id=self.a, monthly_budget=0, models=[]))
        with DB() as db:
            self.assertEqual(
                policy.access(db, self.a, FREE["id"], metadata=FREE).concurrent_runs, 4
            )

    def test_free_zero_reservation_stays_pending(self):
        self.login()
        sid = self.new_chat()
        with DB.begin() as db:
            db.add(
                Run(
                    id="free-run",
                    session_id=sid,
                    request_id="free",
                    body_hash="f",
                    model=FREE["id"],
                    event_offset=0,
                )
            )
        r = policy.reserve(self.a, "free-run", FREE["id"], "0", metadata=FREE)
        with DB() as db:
            s = policy.summary(db, self.a)
            self.assertEqual(s["pending_calls"], 1)
            self.assertEqual(s["pending_reserved"], 0)
        policy.settle(r["id"], 0)
        with DB() as db:
            self.assertEqual(policy.summary(db, self.a)["pending_calls"], 0)

    def test_mixed_cap(self):
        self.assertEqual(policy.concurrency_cap(4, [1]), 1)
        self.assertEqual(policy.concurrency_cap(1, [4]), 1)
        self.assertEqual(policy.concurrency_cap(4, [4, 4]), 4)

    def test_reconciled_free_cost_blocks_route(self):
        from app.platform.db import CatalogModel

        self.login()
        sid = self.new_chat()
        with DB.begin() as db:
            db.add(CatalogModel(id=FREE["id"], payload=FREE, present=True))
            db.add(
                Run(
                    id="anomaly",
                    session_id=sid,
                    request_id="anomaly",
                    body_hash="f",
                    model=FREE["id"],
                    event_offset=0,
                )
            )
        r = policy.reserve(self.a, "anomaly", FREE["id"], "0", metadata=FREE)
        policy.settle(r["id"], ".01")
        with DB() as db:
            self.assertTrue(db.get(CatalogModel, FREE["id"]).blocked)
            self.assertEqual(policy.summary(db, self.a)["confirmed_cost"], 0.01)

    def test_catalog_changed_during_key_creation_never_dispatches(self):
        import asyncio
        from app.billing import completion, gateway

        self.login()
        sid = self.new_chat()
        with DB.begin() as db:
            db.add(
                Run(
                    id="race",
                    session_id=sid,
                    request_id="race",
                    body_hash="f",
                    model=FREE["id"],
                    event_offset=0,
                )
            )
        with (
            patch(
                "app.billing.catalog.lookup",
                side_effect=[FREE, {**FREE, "revision": "changed"}],
            ),
            patch("app.billing.catalog.ensure_route", return_value="route"),
            patch("app.billing.gateway.user_key", return_value="test-key"),
            patch("app.billing.completion.httpx.AsyncClient") as client,
        ):
            with self.assertRaises(gateway.GatewayError):
                asyncio.run(
                    completion.complete(self.a, "race", [], FREE["id"], [], 0, 10)
                )
            client.assert_not_called()
        with DB() as db:
            self.assertEqual(policy.summary(db, self.a)["pending_calls"], 0)

    def test_catalog_locked_paid_and_route_failure(self):
        from app.billing import gateway

        self.login()
        free = {**FREE, "name": "Free", "blocked": False}
        paid = {**PAID, "name": "Paid", "blocked": False}
        with (
            patch("app.billing.catalog.entries", return_value=[free, paid]),
            patch("app.billing.gateway.priced_models", return_value={}),
            patch("app.billing.catalog.ensure_route", return_value="route"),
        ):
            r = self.client.get("/api/models?limit=1")
            self.assertEqual(r.status_code, 200, r.text)
            self.assertTrue(r.json()["has_more"])
            self.assertTrue(r.json()["models"][0]["selectable"])
            r = self.client.get("/api/models?kind=paid")
            self.assertFalse(r.json()["models"][0]["selectable"])
            self.assertEqual(
                self.client.post("/api/admin/models/refresh").status_code, 403
            )
        with (
            patch("app.billing.catalog.entries", return_value=[free]),
            patch("app.billing.gateway.priced_models", return_value={}),
            patch(
                "app.billing.catalog.ensure_route",
                side_effect=gateway.GatewayError("offline"),
            ),
        ):
            self.assertFalse(
                self.client.get("/api/models").json()["models"][0]["selectable"]
            )

    def test_admin_refresh_audited(self):
        self.login("admin", "Admin password 123!")
        with patch("app.billing.catalog.refresh"):
            r = self.client.post("/api/admin/models/refresh")
        self.assertEqual(r.status_code, 200, r.text)

    def test_free_http_admission_four_and_mixed_cap(self):
        import asyncio
        from app.platform import chat

        async def hold(rid):
            await asyncio.Event().wait()

        self.login()
        sessions = [self.new_chat() for _ in range(5)]
        with (
            patch.dict("os.environ", PLATFORM_LIMITS_ENABLED="true"),
            patch("app.billing.catalog.lookup", return_value=FREE),
            patch("app.billing.catalog.ensure_route", return_value="route"),
            patch.object(chat, "execute", side_effect=hold),
        ):
            for i, sid in enumerate(sessions):
                r = self.client.post(
                    "/api/harness/start",
                    json={
                        "session_id": sid,
                        "request_id": str(i),
                        "goal": "hello",
                        "primary_model": FREE["id"],
                    },
                )
                self.assertEqual(r.status_code, 200 if i < 4 else 409, r.text)
            for sid in sessions[:4]:
                self.client.post("/api/harness/stop/" + sid)
            with DB.begin() as db:
                db.add(
                    UserLimit(
                        user_id=self.a,
                        monthly_budget=5,
                        models=["gpt-4o-mini"],
                        concurrent_runs=1,
                    )
                )
            with patch("app.billing.catalog.lookup", return_value=None):
                r = self.client.post(
                    "/api/harness/start",
                    json={
                        "session_id": sessions[0],
                        "request_id": "paid",
                        "goal": "hello",
                        "primary_model": "gpt-4o-mini",
                    },
                )
                self.assertEqual(r.status_code, 200, r.text)
            r = self.client.post(
                "/api/harness/start",
                json={
                    "session_id": sessions[1],
                    "request_id": "mixed",
                    "goal": "hello",
                    "primary_model": FREE["id"],
                },
            )
            self.assertEqual(r.status_code, 409, r.text)
            self.client.post("/api/harness/stop/" + sessions[0])

    def test_free_disabled_user_and_password_required_denied(self):
        from app.platform.db import User

        for field in ("active", "must_change_password"):
            with DB.begin() as db:
                user = db.get(User, self.a)
                user.active = field != "active"
                user.must_change_password = field == "must_change_password"
            with DB() as db:
                with self.assertRaises(HTTPException):
                    policy.access(db, self.a, FREE["id"], metadata=FREE)

    def test_free_key_is_model_scoped_without_paid_budget(self):
        from app.billing import gateway
        from cryptography.fernet import Fernet

        with (
            patch.dict(
                "os.environ", PLATFORM_KEY_ENCRYPTION_KEY=Fernet.generate_key().decode()
            ),
            patch.object(gateway, "call", return_value={}) as remote,
        ):
            gateway.user_key(self.a, policy.month(), None, "immutable-route", FREE)
            body = next(
                call.args[2]
                for call in remote.call_args_list
                if call.args[1] == "/key/generate"
            )
            self.assertEqual(body["models"], ["immutable-route"])
            self.assertIsNone(body["max_budget"])
            self.assertEqual(body["max_parallel_requests"], 4)

    def test_provider_cost_overrides_zero_or_invalid_free_estimate(self):
        import asyncio, httpx
        from sqlalchemy import select
        from app.billing import completion, gateway
        from app.platform.db import CatalogModel, UsageRecord

        self.login()
        sid = self.new_chat()
        with DB.begin() as db:
            db.add(CatalogModel(id=FREE["id"], payload=FREE, present=True))
            db.add(
                Run(
                    id="cost-conflict",
                    session_id=sid,
                    request_id="cost-conflict",
                    body_hash="f",
                    model=FREE["id"],
                    event_offset=0,
                )
            )
        real = httpx.AsyncClient
        for header in ("0", "invalid"):

            def transport(request):
                return httpx.Response(
                    200,
                    headers={"x-litellm-response-cost": header},
                    json={
                        "id": "evidence",
                        "choices": [
                            {"message": {"role": "assistant", "content": "done"}}
                        ],
                        "usage": {
                            "cost": 0.01,
                            "prompt_tokens": 2,
                            "completion_tokens": 1,
                        },
                    },
                )

            with (
                patch.dict("os.environ", LITELLM_PROXY_URL="http://gateway"),
                patch("app.billing.catalog.lookup", return_value=FREE),
                patch("app.billing.catalog.ensure_route", return_value="route"),
                patch("app.billing.gateway.user_key", return_value="test-key"),
                patch(
                    "app.billing.completion.httpx.AsyncClient",
                    side_effect=lambda **kw: real(
                        transport=httpx.MockTransport(transport), **kw
                    ),
                ),
            ):
                with self.assertRaises(gateway.GatewayError):
                    asyncio.run(
                        completion.complete(
                            self.a, "cost-conflict", [], FREE["id"], [], 0, 10
                        )
                    )
            with DB() as db:
                self.assertTrue(db.get(CatalogModel, FREE["id"]).blocked)
                self.assertTrue(
                    all(float(r.cost) == 0.01 for r in db.scalars(select(UsageRecord)))
                )
