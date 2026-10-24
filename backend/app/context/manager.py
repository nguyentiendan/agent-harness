"""Deterministic priority selection with a complete omission manifest."""

import math
from app.prompts.registry import render_prompt
from app.prompts.summarization import HISTORICAL_SUMMARY_PROMPT_SPEC
from .types import ContextRequest, PreparedContext, ContextBudgetExceeded
from .tokens import count_payload
from .policy import ContextPolicy


def prepare_context(request: ContextRequest, policy: ContextPolicy) -> PreparedContext:
    reserve = max(
        policy.safety_tokens,
        math.ceil(request.limits.context_window * policy.safety_fraction),
    )
    budget = request.limits.context_window - request.effective_output_cap - reserve
    if request.limits.max_input_tokens is not None:
        budget = min(budget, request.limits.max_input_tokens)
    # System instructions lead; all current-run messages stay after old context.
    head = [m for m in request.mandatory if m["role"] == "system"]
    tail = [m for m in request.mandatory if m["role"] != "system"]

    def assemble(selected, summary):
        history = [
            m
            for g in sorted(selected, key=lambda g: g.sequence)
            if not g.current_run
            for m in g.messages
        ]
        active = [
            m
            for g in sorted(selected, key=lambda g: g.sequence)
            if g.current_run
            for m in g.messages
        ]
        split = next(
            (i for i, m in enumerate(tail) if m["role"] == "assistant"), len(tail)
        )
        return head + summary + history + tail[:split] + active + tail[split:]

    def count(selected, summary):
        return count_payload(
            assemble(selected, summary), request.tools, request.model
        ).tokens

    if count([], []) > budget:
        raise ContextBudgetExceeded(
            "Required instructions/current request exceed this model context. Reduce the input or select a larger model."
        )
    selected, omitted = [], []
    eligible = []
    for group in request.groups:
        if (
            group.workspace_revision is not None
            and group.workspace_revision != request.workspace_revision
        ):
            omitted.append({"id": group.id, "reason": "workspace_revision"})
        else:
            eligible.append(group)
    covered = [
        g
        for g in eligible
        if request.summary_source_ids
        and set(g.source_ids).issubset(request.summary_source_ids)
    ]
    covered_ids = {g.id for g in covered}
    ranked = sorted(
        [g for g in eligible if g.id not in covered_ids],
        key=lambda g: g.sequence,
        reverse=True,
    )
    recent, older = ranked[: policy.recent_groups], ranked[policy.recent_groups :]
    for group in recent:
        if count(selected + [group], []) <= budget:
            selected.append(group)
        else:
            omitted.append({"id": group.id, "reason": "budget"})
    summary = []
    if request.summary:
        import json

        candidate = [
            {
                "role": "user",
                "content": render_prompt(
                    HISTORICAL_SUMMARY_PROMPT_SPEC,
                    {"summary": json.dumps(request.summary, ensure_ascii=False)},
                ).text,
            }
        ]
        if count(selected, candidate) <= budget:
            summary = candidate
    if summary:
        omitted.extend({"id": g.id, "reason": "summarized"} for g in covered)
    else:
        older = sorted(older + covered, key=lambda g: g.sequence, reverse=True)
    for group in older:
        if count(selected + [group], summary) <= budget:
            selected.append(group)
        else:
            omitted.append({"id": group.id, "reason": "budget"})
    messages = assemble(selected, summary)
    estimate = count_payload(messages, request.tools, request.model)
    manifest = {
        "selected_group_ids": [
            g.id for g in sorted(selected, key=lambda g: g.sequence)
        ],
        "omitted": omitted,
        "estimated_tokens": estimate.tokens,
        "estimator_id": estimate.estimator_id,
        "approximate": estimate.approximate,
        "input_budget": budget,
        "output_cap": request.effective_output_cap,
        "safety_reserve": reserve,
        "model_revision": request.limits.revision,
        "policy_version": policy.version,
        "policy_hash": policy.content_hash,
        "summary_used": bool(summary),
        "summary_source_ids": request.summary_source_ids if summary else [],
        "summary_group_ids": [g.id for g in covered] if summary else [],
        "prompt_versions": [
            {
                "id": HISTORICAL_SUMMARY_PROMPT_SPEC.id,
                "version": HISTORICAL_SUMMARY_PROMPT_SPEC.version,
                "hash": HISTORICAL_SUMMARY_PROMPT_SPEC.content_hash,
            }
        ]
        if summary
        else [],
    }
    return PreparedContext(messages, manifest, request.effective_output_cap)
