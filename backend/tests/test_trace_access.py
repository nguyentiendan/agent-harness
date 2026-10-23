import support
import unittest
from app.platform.db import DB, TraceRecord, TraceSpan, TraceExportJob, Chat
import test_platform_auth as auth
from app.observability.export import export_pending


class TraceAccessTests(unittest.TestCase):
    setUp = auth.PlatformTests.setUp
    login = auth.PlatformTests.login
    new_chat = auth.PlatformTests.new_chat

    def test_owner_and_other_user(self):
        self.login()
        sid = self.new_chat()
        with DB.begin() as db:
            db.add(TraceRecord(id="trace", scope_id=sid, status="completed"))
            db.add(
                TraceSpan(
                    id="span", trace_id="trace", kind="context", data={"omitted": []}
                )
            )
        self.assertEqual(
            self.client.get(f"/api/sessions/{sid}/runs/trace/trace").status_code, 200
        )
        self.assertEqual(
            self.client.get(f"/api/sessions/{sid}/runs/wrong/trace").status_code, 404
        )
        with DB.begin() as db:
            db.get(Chat, sid).owner_id = self.b
        self.assertEqual(
            self.client.get(f"/api/sessions/{sid}/runs/trace/trace").status_code, 404
        )

    def test_deleted_scope_not_exported(self):
        self.login()
        sid = self.new_chat()
        with DB.begin() as db:
            db.get(Chat, sid).deleted_at = 1
            db.add(TraceRecord(id="trace", scope_id=sid, status="completed"))
            db.add(TraceExportJob(id="job", trace_id="trace"))
        exported = []

        class Exporter:
            def export(self, batch, idempotency_key):
                exported.append(batch)

        export_pending(Exporter(), 100)
        self.assertEqual(exported, [])

    def test_export_failure_is_bounded(self):
        self.login()
        sid = self.new_chat()
        with DB.begin() as db:
            db.add(TraceRecord(id="trace", scope_id=sid, status="completed"))
            db.add(TraceExportJob(id="job", trace_id="trace"))
        calls = []

        class Exporter:
            def export(self, batch, idempotency_key):
                calls.append(idempotency_key)
                raise RuntimeError("offline")

        for now in range(100, 10000, 100):
            export_pending(Exporter(), now)
        self.assertEqual(calls, ["job"] * 5)
        with DB() as db:
            self.assertEqual(db.get(TraceExportJob, "job").status, "failed")

    def test_retention_and_purge(self):
        from app.observability.storage import retain, purge_scopes
        from app.context.policy import ContextPolicy
        from app.platform.db import TracePayload, ContextMessage

        self.login()
        sid = self.new_chat()
        with DB.begin() as db:
            db.add(
                TraceRecord(id="old", scope_id=sid, status="completed", created_at=0)
            )
            db.add(TraceSpan(id="span", trace_id="old", kind="model"))
            db.add(
                TracePayload(
                    id="payload",
                    trace_id="old",
                    payload={"secret": "test"},
                    created_at=0,
                )
            )
        with DB.begin() as db:
            retain(db, 31 * 86400, ContextPolicy())
        with DB() as db:
            self.assertIsNone(db.get(TraceRecord, "old"))
            self.assertIsNone(db.get(TraceSpan, "span"))
            self.assertIsNone(db.get(TracePayload, "payload"))
