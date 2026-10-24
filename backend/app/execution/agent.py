"""Small scoped tool loop; legacy host tools/capabilities are never registered."""

import asyncio
import json
from . import client, lifecycle
from app.context.history import record_protocol
from app.context.outputs import ensure_capacity, store_output, read_output, preview
from app.observability import tracing
from app.config import load_config
from app.context.policy import load_context_policy
from app.prompts.project import PROJECT_PROMPT_SPEC
from app.prompts.system import SYSTEM_PROMPT_SPEC
from app.prompts.registry import render_prompt

SCHEMAS = []
for name, description, props, required in [
    (
        "bash",
        "Run a shell command in the offline sandbox. Files persist in /workspace.",
        {
            "command": {"type": "string"},
            "timeout": {"type": "integer", "minimum": 1, "maximum": 120},
        },
        ["command"],
    ),
    (
        "read_file",
        "Read UTF-8 text inside /workspace.",
        {"path": {"type": "string"}},
        ["path"],
    ),
    (
        "write_file",
        "Write UTF-8 text inside /workspace (max 60000 bytes).",
        {"path": {"type": "string"}, "content": {"type": "string"}},
        ["path", "content"],
    ),
    (
        "edit_file",
        "Replace one exact text occurrence in a file.",
        {
            "path": {"type": "string"},
            "old": {"type": "string"},
            "new": {"type": "string"},
        },
        ["path", "old", "new"],
    ),
    ("list_dir", "List regular files inside /workspace.", {}, []),
    (
        "read_tool_output",
        "Read a page of an archived tool result from this chat and workspace revision. Offsets count characters.",
        {
            "output_id": {"type": "string"},
            "offset": {"type": "integer", "minimum": 0},
            "limit": {"type": "integer", "minimum": 1},
        },
        ["output_id", "offset", "limit"],
    ),
]:
    SCHEMAS.append(
        {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                    "additionalProperties": False,
                },
            },
        }
    )


async def run(llm, rid, history, model, emit):
    policy = load_context_policy(load_config(strict=True))
    max_turns = policy.max_turns
    messages = [
        {
            "role": "system",
            "content": render_prompt(SYSTEM_PROMPT_SPEC, {}).text
            + "\n"
            + render_prompt(
                PROJECT_PROMPT_SPEC,
                {"workspace": policy.workspace, "max_turns": max_turns},
            ).text,
        },
        *history,
    ]
    known = {s["function"]["name"] for s in SCHEMAS}
    for _ in range(max_turns):
        response = await llm.complete(messages, model=model, tools=SCHEMAS)
        message = response.choices[0].message
        calls = message.tool_calls or []
        if len(calls) > policy.max_tool_calls:
            raise RuntimeError("Too many tool calls.")
        if message.content:
            emit(
                {
                    "type": "assistant",
                    "text": message.content,
                    "tool_assistant": bool(calls),
                }
            )
        if not calls:
            return
        messages.append(
            {
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {
                            "name": c.function.name,
                            "arguments": c.function.arguments,
                        },
                    }
                    for c in calls
                ],
            }
        )
        group_id = "turn:" + str(_)
        record_protocol(rid, group_id + ":assistant", messages[-1], group_id)
        for call in calls:
            name = call.function.name
            try:
                args = json.loads(call.function.arguments)
                if name not in known or not isinstance(args, dict) or "op" in args:
                    raise ValueError("Tool is not allowed.")
            except (ValueError, TypeError):
                raise RuntimeError("Invalid tool call.")
            emit({"type": "tool_call", "name": name, "args": args})
            with tracing.span("tool", {"tool_name": name, "tool_call_id": call.id}):
                if name == "read_tool_output":
                    try:
                        result = await asyncio.to_thread(
                            read_output, rid, policy=policy, **args
                        )
                    except ValueError:
                        result = {
                            "error": "Tool output unavailable or invalid pagination. Do not repeat a past side effect to recover it."
                        }
                else:
                    await asyncio.to_thread(ensure_capacity, rid, policy)
                    await asyncio.to_thread(lifecycle.prepare, rid)
                    result = await asyncio.to_thread(
                        client.RunnerClient().execute, rid, {"op": name, **args}
                    )
            raw = json.dumps(result, ensure_ascii=False)
            output_id = await asyncio.to_thread(
                store_output, rid, group_id + ":" + call.id, raw, policy
            )
            output = (
                preview(
                    raw,
                    output_id,
                    min(policy.tool_output_chars, policy.tool_preview_chars),
                )
                if len(raw) > min(policy.tool_output_chars, policy.tool_preview_chars)
                else raw
            )
            emit({"type": "tool_result", "name": name, "output": output})
            messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": output}
            )
            record_protocol(rid, group_id + ":" + call.id, messages[-1], group_id)
            if result.get("timed_out"):
                raise RuntimeError("Sandbox command timed out; container removed.")
    raise RuntimeError("Maximum agent turns reached.")
