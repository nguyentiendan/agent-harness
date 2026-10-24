"""Bounded summary calls with explicit provenance and no recursive dispatch."""

from dataclasses import dataclass
import json
import hashlib
from .tokens import count_payload
from app.prompts.summarization import SUMMARY_PROMPT_SPEC
from app.prompts.registry import render_prompt


@dataclass
class SummaryOutcome:
    summary: dict | None = None
    usage_id: str | None = None
    reason: str = ""
    attempted: bool = False


def validate_summary(payload: dict, allowed_source_ids: set[str]) -> dict:
    keys = {"goal", "constraints", "decisions", "unresolved_work", "evidence"}
    if not isinstance(payload, dict) or set(payload) != keys:
        raise ValueError("Invalid summary schema")
    for items in payload.values():
        if not isinstance(items, list):
            raise ValueError("Invalid summary items")
        for item in items:
            if not isinstance(item, dict) or set(item) != {"text", "source_ids"}:
                raise ValueError("Invalid summary item")
            if not isinstance(item["text"], str) or not item["text"].strip():
                raise ValueError("Invalid summary text")
            ids = item["source_ids"]
            if (
                not isinstance(ids, list)
                or not ids
                or any(
                    not isinstance(i, str) or i not in allowed_source_ids for i in ids
                )
            ):
                raise ValueError("Unknown summary source")
    return payload


def source_digest(groups):
    return hashlib.sha256(
        json.dumps(
            [(g.source_ids, g.messages) for g in groups],
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()


async def maybe_summarize(request, policy, llm, summaries_used: int) -> SummaryOutcome:
    if not policy.summary_enabled:
        return SummaryOutcome(reason="disabled")
    if summaries_used >= policy.summary_max_calls:
        return SummaryOutcome(reason="allowance_exhausted")
    if policy.summary_model and policy.summary_model != request.model:
        return SummaryOutcome(reason="summary_override_requires_matching_model")
    output = min(
        policy.summary_output_tokens,
        request.effective_output_cap,
        request.limits.max_output_tokens,
    )
    budget = (
        int(request.limits.context_window * policy.summary_input_fraction)
        - output
        - policy.safety_tokens
    )
    if request.limits.max_input_tokens is not None:
        budget = min(budget, request.limits.max_input_tokens)
    prompt = render_prompt(SUMMARY_PROMPT_SPEC, {})
    selected = []

    def messages(groups):
        data = [{"source_ids": g.source_ids, "messages": g.messages} for g in groups]
        if request.summary and request.summary_source_ids:
            data.insert(
                0,
                {
                    "source_ids": request.summary_source_ids,
                    "messages": [
                        {
                            "role": "user",
                            "content": json.dumps(request.summary, ensure_ascii=False),
                        }
                    ],
                },
            )
        return [
            {"role": "system", "content": prompt.text},
            {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
        ]

    for group in sorted(request.groups, key=lambda g: g.sequence):
        if (
            group.workspace_revision is not None
            and group.workspace_revision != request.workspace_revision
        ):
            continue
        if (
            count_payload(messages(selected + [group]), [], request.model).tokens
            > budget
        ):
            break
        selected.append(group)
    if not selected:
        return SummaryOutcome(reason="no_prefix_fits")
    try:
        response = await llm(
            messages(selected),
            model=request.model,
            max_tokens=output,
            purpose="summary",
        )
        text = response.choices[0].message.content
        if not isinstance(text, str) or len(text.encode()) > output * 16:
            raise ValueError("Summary response exceeds limit")
        payload = validate_summary(
            json.loads(text),
            set(request.summary_source_ids)
            | {i for g in selected for i in g.source_ids},
        )
        return SummaryOutcome(
            summary={
                "content": payload,
                "source_ids": list(
                    dict.fromkeys(
                        request.summary_source_ids
                        + [i for g in selected for i in g.source_ids]
                    )
                ),
                "source_digest": source_digest(selected),
                "first_sequence": selected[0].sequence,
                "last_sequence": selected[-1].sequence,
                "prompt_hash": prompt.content_hash,
                "prompt_version": prompt.version,
                "model": request.model,
                "policy_hash": policy.content_hash,
                "workspace_revision": request.workspace_revision,
            },
            usage_id=getattr(response, "platform_usage_id", None),
            reason="created",
            attempted=True,
        )
    except Exception:
        return SummaryOutcome(reason="summary_failed", attempted=True)
