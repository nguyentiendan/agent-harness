"""Optional rubric judging; failures remain visible and never count as passes."""

import json
import math
from app.prompts.evaluation import JUDGE_PROMPT_SPEC
from app.prompts.registry import render_prompt


def validate_score(payload: dict, evidence_ids: set[str]) -> dict:
    if not isinstance(payload, dict) or set(payload) != {
        "score",
        "reason",
        "evidence_ids",
    }:
        raise ValueError("Invalid judge schema")
    score = payload["score"]
    if (
        type(score) not in (int, float)
        or not math.isfinite(score)
        or not 0 <= score <= 1
    ):
        raise ValueError("Invalid judge score")
    if not isinstance(payload["reason"], str) or not payload["reason"].strip():
        raise ValueError("Judge reason missing")
    ids = payload["evidence_ids"]
    if not isinstance(ids, list) or any(
        not isinstance(i, str) or i not in evidence_ids for i in ids
    ):
        raise ValueError("Invalid judge evidence")
    return payload


async def judge(
    llm, rubric: str, result: dict, evidence_ids: set[str], max_tokens: int
) -> dict:
    prompt = render_prompt(JUDGE_PROMPT_SPEC, {"rubric": rubric})
    try:
        response = await llm.complete(
            [
                {"role": "system", "content": prompt.text},
                {"role": "user", "content": json.dumps(result)},
            ],
            max_tokens=max_tokens,
            purpose="judge",
        )
        score = validate_score(
            json.loads(response.choices[0].message.content), evidence_ids
        )
        return {"status": "scored", **score, "prompt_hash": prompt.content_hash}
    except Exception as error:
        return {
            "status": "failed",
            "error_code": type(error).__name__,
            "score": None,
            "prompt_hash": prompt.content_hash,
        }
