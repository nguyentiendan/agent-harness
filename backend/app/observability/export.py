"""Disabled-by-default exporter contract; retries never replay model/tool calls."""

from typing import Protocol
from sqlalchemy import select
from app.platform.db import DB, TraceExportJob, TraceRecord, Chat, Project
from app.config import load_config
from app.context.policy import load_context_policy
from .routes import trace_data


class TraceExporter(Protocol):
    def export(self, batch: list[dict], idempotency_key: str) -> None: ...


def export_pending(exporter: TraceExporter, now: float) -> int:
    policy = load_context_policy(load_config(strict=True))
    done = 0
    # Single-worker platform. Locks serialize deletion with an export transaction.
    with DB.begin() as db:
        jobs = list(
            db.scalars(
                select(TraceExportJob)
                .where(
                    TraceExportJob.status == "pending",
                    TraceExportJob.next_attempt_at <= now,
                )
                .limit(100)
                .with_for_update()
            )
        )
        for job in jobs:
            trace = db.get(TraceRecord, job.trace_id)
            chat = (
                db.scalar(
                    select(Chat).where(Chat.id == trace.scope_id).with_for_update()
                )
                if trace
                else None
            )
            project = (
                db.get(Project, chat.project_id) if chat and chat.project_id else None
            )
            if not chat or chat.deleted_at or (project and project.deleted_at):
                db.delete(job)
                continue
            if job.attempts >= policy.export_max_attempts:
                job.status = "failed"
                continue
            job.attempts += 1
            try:
                # Export all pages; no silent 100-span truncation.
                data = trace_data(db, trace, 0, 500)
                offset = 500
                while len(data["spans"]) >= offset:
                    page = trace_data(db, trace, offset, 500)["spans"]
                    if not page:
                        break
                    data["spans"].extend(page)
                    offset += 500
                exporter.export([data], job.id)
            except Exception:
                job.next_attempt_at = now + min(300, 2**job.attempts)
                if job.attempts >= policy.export_max_attempts:
                    job.status = "failed"
            else:
                job.status = "completed"
                done += 1
    return done
