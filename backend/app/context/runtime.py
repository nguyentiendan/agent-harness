"""Per-run context orchestration shared by Chat and Project completions."""

import asyncio
import hashlib
import json
from dataclasses import replace
from sqlalchemy import select
from app.config import load_config
from app.platform.db import (
    DB,
    Run,
    Chat,
    UserLimit,
    Event,
    ContextSummary,
    ContextMessage,
)
from app.billing import catalog, gateway
from app.observability import tracing
from app.security.redaction import redact_text
from .policy import load_context_policy
from .limits import resolve_model_limits, effective_output_cap
from .types import ContextRequest, ConversationGroup, ContextBudgetExceeded
from .manager import prepare_context
from .history import load_groups
from .summarization import maybe_summarize, source_digest
from .tokens import count_payload
from app.prompts.summarization import SUMMARY_PROMPT_SPEC


class ContextRuntime:
    def __init__(self, user_id, run_id):
        self.user_id = user_id
        self.run_id = run_id
        self.policy = load_context_policy(load_config(strict=True))
        self.summaries_used = 0
        self.last_manifest = None
        self.dispatch_revision = None

    async def prepare(self, messages, model, tools, max_tokens, summary_complete):
        metadata = await asyncio.to_thread(catalog.lookup, model)
        if not metadata:
            metadata = (await asyncio.to_thread(gateway.priced_models)).get(model, {})
        limits = resolve_model_limits(metadata, self.policy)
        self.dispatch_revision = limits.revision
        with DB() as db:
            run = db.get(Run, self.run_id)
            chat = db.get(Chat, run.session_id) if run else None
            if (
                not chat
                or chat.owner_id != self.user_id
                or chat.deleted_at
                or run.status != "running"
            ):
                raise ValueError("Run is not available for context preparation")
            cap = db.get(UserLimit, self.user_id)
            output = effective_output_cap(
                max_tokens, cap.max_output_tokens if cap else max_tokens, limits
            )
            revision = (run.workspace_snapshot or {}).get("revision")
            historical = load_groups(db, chat.id)
            # Current run is in the live messages; only replay earlier runs.
            historical = [
                g for g in historical if not g.id.startswith(self.run_id + ":")
            ]
            events = list(
                db.scalars(
                    select(Event)
                    .where(Event.session_id == chat.id, Event.run_id != self.run_id)
                    .order_by(Event.sequence)
                )
            )
            represented = set(
                db.scalars(
                    select(ContextMessage.run_id).where(
                        ContextMessage.scope_id == chat.id
                    )
                )
            )
            runs = {
                r.id: (r.event_offset, r.created_at, r.id)
                for r in db.scalars(select(Run).where(Run.session_id == chat.id))
            }
            group_runs = {g.id: g.id.split(":", 1)[0] for g in historical}
            for e in events:
                if e.run_id not in represented and e.payload.get("type") in (
                    "user",
                    "assistant",
                ):
                    group_runs["legacy:" + str(e.id)] = e.run_id
                    historical.append(
                        ConversationGroup(
                            "legacy:" + str(e.id),
                            [{"role": e.payload["type"], "content": e.payload["text"]}],
                            ["event:" + str(e.id)],
                            None,
                            e.sequence,
                        )
                    )
            # Local durable ordinals and legacy event sequences are incomparable.
            # Order runs first, then normalize all groups onto one timeline.
            historical.sort(
                key=lambda g: (
                    runs.get(group_runs[g.id], (0, 0, group_runs[g.id])),
                    g.sequence,
                )
            )
            historical = [replace(g, sequence=i) for i, g in enumerate(historical)]
            saved = list(
                db.scalars(
                    select(ContextSummary)
                    .where(ContextSummary.scope_id == chat.id)
                    .order_by(ContextSummary.created_at.desc())
                )
            )
            scope_id = chat.id
        # Archive large results before shrinking. Never rerun an action to recover output.
        from .outputs import store_output, preview, read_output

        messages = [dict(m) for m in messages]
        for i, message in enumerate(messages):
            text = message.get("content")
            if message.get("role") != "tool" or not isinstance(text, str):
                continue
            try:
                data = json.loads(text)
                existing = data.get("output_id") if isinstance(data, dict) else None
                if existing:
                    read_output(self.run_id, existing, 0, 1, self.policy)
            except (ValueError, TypeError):
                existing = None
            if len(text) > self.policy.tool_preview_chars:
                ident = existing or store_output(
                    self.run_id, message.get("tool_call_id", str(i)), text, self.policy
                )
                message["content"] = preview(
                    text, ident, self.policy.tool_preview_chars
                )
        # Public chat now passes only current-run text and policy preferences.
        # Generic direct callers retain their entire supplied payload as mandatory.
        mandatory = list(messages)
        starts = [
            i
            for i, m in enumerate(messages)
            if m.get("role") == "assistant" and m.get("tool_calls")
        ]
        active_groups = []
        if len(starts) > 1:
            # Current request/policies and newest tool group are mandatory; older
            # complete groups may be selected without breaking protocol order.
            mandatory = messages[: starts[0]] + messages[starts[-1] :]
            for index, (a, b) in enumerate(zip(starts, starts[1:])):
                block = messages[a:b]
                calls = [c["id"] for m in block for c in m.get("tool_calls", [])]
                results = [m.get("tool_call_id") for m in block if m["role"] == "tool"]
                if sorted(calls) != sorted(results):
                    raise ValueError("Incomplete active tool group")
                key = hashlib.sha256(
                    json.dumps(block, sort_keys=True).encode()
                ).hexdigest()
                active_groups.append(
                    ConversationGroup(
                        "active:" + key,
                        block,
                        ["active:" + key],
                        revision,
                        len(historical) + index,
                        True,
                    )
                )
        request = ContextRequest(
            scope_id,
            self.run_id,
            model,
            limits,
            output,
            mandatory,
            historical + active_groups,
            list(tools or []),
            revision,
        )
        all_groups = historical + active_groups
        for row in saved:
            saved_summary = row.payload
            ids = set(saved_summary.get("source_ids", []))
            sources = [g for g in all_groups if set(g.source_ids).issubset(ids)]
            if (
                saved_summary.get("workspace_revision") == revision
                and saved_summary.get("policy_hash") == self.policy.content_hash
                and saved_summary.get("prompt_hash") == SUMMARY_PROMPT_SPEC.content_hash
                and saved_summary.get("prompt_version") == SUMMARY_PROMPT_SPEC.version
                and saved_summary.get("model") == model
                and set(i for g in sources for i in g.source_ids) == ids
                and source_digest(sources) == saved_summary.get("source_digest")
            ):
                request.summary = saved_summary["content"]
                request.summary_source_ids = saved_summary["source_ids"]
                break
        with tracing.span("context", {"policy_hash": self.policy.content_hash}) as sid:
            prepared = self._fit_mandatory(request)
            full = count_payload(
                messages + [m for g in historical for m in g.messages],
                tools or [],
                model,
            ).tokens
            reason = "not_needed"
            if (
                summary_complete
                and full
                > prepared.manifest["input_budget"] * self.policy.summary_trigger
            ):
                older = sorted(all_groups, key=lambda g: g.sequence)
                older = (
                    older[: -self.policy.recent_groups]
                    if len(older) > self.policy.recent_groups
                    else []
                )
                older = [
                    g
                    for g in older
                    if not set(g.source_ids).issubset(request.summary_source_ids)
                ]
                with tracing.span("summary", {"purpose": "summary"}):
                    outcome = await maybe_summarize(
                        replace(request, groups=older),
                        self.policy,
                        summary_complete,
                        self.summaries_used,
                    )
                self.summaries_used += int(outcome.attempted)
                reason = outcome.reason
                if outcome.summary:
                    # Recheck cancellation/liveness before publishing summary.
                    with DB.begin() as db:
                        run = db.get(Run, self.run_id)
                        from .outputs import scope_for

                        scope_for(db, self.run_id)
                        if not run or run.status != "running":
                            raise ValueError("Run stopped during summary")
                        safe = json.loads(redact_text(json.dumps(outcome.summary)))
                        covered_ids = set(safe["source_ids"])
                        covered = [
                            g
                            for g in all_groups
                            if set(g.source_ids).issubset(covered_ids)
                        ]
                        safe["source_digest"] = source_digest(covered)
                        key = hashlib.sha256(
                            (scope_id + json.dumps(safe, sort_keys=True)).encode()
                        ).hexdigest()
                        if not db.get(ContextSummary, key):
                            db.add(
                                ContextSummary(id=key, scope_id=scope_id, payload=safe)
                            )
                    request.summary = safe["content"]
                    request.summary_source_ids = safe["source_ids"]
                    prepared = prepare_context(request, self.policy)
            prepared.manifest["summary_reason"] = reason
            tracing.finish_span(sid, "completed", prepared.manifest)
        self.last_manifest = prepared.manifest
        if prepared.manifest["omitted"]:
            from app.platform.chat import append_event

            with DB.begin() as db:
                append_event(
                    db,
                    scope_id,
                    self.run_id,
                    {"type": "context", "manifest": prepared.manifest},
                )
        return prepared

    def _fit_mandatory(self, request):
        """Shrink only recoverable tool payloads; never silently truncate user intent."""
        from .outputs import store_output, preview

        limit = self.policy.tool_preview_chars
        while True:
            try:
                return prepare_context(request, self.policy)
            except ContextBudgetExceeded:
                limit //= 2
                if limit < self.policy.tool_min_preview_chars:
                    raise
                changed = False
                for m in request.mandatory:
                    if m.get("role") != "tool" or len(m.get("content", "")) <= limit:
                        continue
                    text = m["content"]
                    try:
                        data = json.loads(text)
                        ident = (
                            data.get("output_id") if isinstance(data, dict) else None
                        )
                    except ValueError:
                        ident = None
                    ident = ident or store_output(
                        self.run_id, m.get("tool_call_id", ""), text, self.policy
                    )
                    m["content"] = preview(text, ident, limit)
                    changed = True
                if not changed:
                    raise
