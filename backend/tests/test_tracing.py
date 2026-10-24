import support
import unittest
from unittest.mock import patch
from app.platform.db import Base, engine, DB, TraceRecord, TraceSpan
from app.observability.tracing import start_trace, start_span, finish_span, link_usage


class TraceTests(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)

    def test_hierarchy_and_unknown_usage(self):
        trace = start_trace("r", {"scope_id": "s", "model": "m"})
        span = start_span(trace, None, "model", {"purpose": "generation"})
        link_usage(span, "u")
        finish_span(span, "completed", {})
        with DB() as db:
            row = db.get(TraceSpan, span)
            self.assertEqual(row.trace_id, "r")
            self.assertEqual(row.usage_id, "u")
            self.assertNotIn("cost", row.data)
            self.assertEqual(row.status, "completed")
            self.assertIsNotNone(row.finished_at)

    def test_telemetry_failure_does_not_raise(self):
        with patch(
            "app.observability.tracing.DB.begin",
            side_effect=RuntimeError("database down"),
        ):
            self.assertIsNone(start_trace("r", {"scope_id": "s"}))

    def test_payload_not_in_metadata(self):
        trace = start_trace(
            "r", {"scope_id": "s", "messages": [{"content": "secret"}], "model": "m"}
        )
        with DB() as db:
            self.assertNotIn("messages", db.get(TraceRecord, trace).data)

    def test_debug_payload_opt_in_and_redaction(self):
        from app.observability.tracing import capture_payload
        from app.context.policy import ContextPolicy
        from app.platform.db import TracePayload
        from dataclasses import replace
        from sqlalchemy import select

        start_trace("r", {"scope_id": "s"})
        capture_payload(
            "r", "span", {"text": "Bearer secret-token-value"}, ContextPolicy()
        )
        with DB() as db:
            self.assertEqual(list(db.scalars(select(TracePayload))), [])
        capture_payload(
            "r",
            "span",
            {"text": "Bearer secret-token-value"},
            replace(ContextPolicy(), trace_payloads=True),
        )
        with DB() as db:
            rows = list(db.scalars(select(TracePayload)))
            self.assertEqual(len(rows), 1)
            self.assertNotIn("secret-token-value", str(rows[0].payload))

    def test_failed_trace_creation_cannot_leave_orphans(self):
        from app.observability.tracing import capture_payload
        from app.context.policy import ContextPolicy
        from app.platform.db import TracePayload
        from dataclasses import replace
        from sqlalchemy import select

        with patch(
            "app.observability.tracing.DB.begin",
            side_effect=RuntimeError("temporary outage"),
        ):
            start_trace("r", {"scope_id": "s"})
        sid = start_span("r", None, "model", {})
        capture_payload(
            "r", sid, {"text": "private"}, replace(ContextPolicy(), trace_payloads=True)
        )
        with DB() as db:
            self.assertEqual(list(db.scalars(select(TraceSpan))), [])
            self.assertEqual(list(db.scalars(select(TracePayload))), [])

    def test_purge_cleans_preexisting_orphan_by_run(self):
        from app.platform.db import User, Chat, Run, TracePayload
        from app.observability.storage import purge_scopes

        with DB.begin() as db:
            db.add(User(id="u", username="orphan-user", password_hash="x", role="user"))
            db.flush()
            db.add(Chat(id="s", owner_id="u", title="test"))
            db.flush()
            db.add(
                Run(
                    id="r",
                    session_id="s",
                    request_id="r",
                    body_hash="x",
                    model="fake",
                    event_offset=0,
                )
            )
            db.add(TraceSpan(id="orphan", trace_id="r", kind="model"))
            db.add(
                TracePayload(id="payload", trace_id="r", payload={"text": "private"})
            )
        with DB.begin() as db:
            purge_scopes(db, ["s"])
        with DB() as db:
            self.assertIsNone(db.get(TraceSpan, "orphan"))
            self.assertIsNone(db.get(TracePayload, "payload"))
