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
