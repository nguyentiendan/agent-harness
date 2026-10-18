"""Small scoped tool loop; legacy host tools/capabilities are never registered."""

import asyncio
import json
from . import client, lifecycle

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
    messages = [
        {
            "role": "system",
            "content": "You are an AI assistant with an offline Docker sandbox. Use tools to inspect or change files; all user work is in /workspace. No network/package downloads, host access, or privileged operations. Files in /workspace persist between messages. Never claim actions without tool evidence. Maximum 15 tool/model turns.",
        },
        *history,
    ]
    known = {s["function"]["name"] for s in SCHEMAS}
    for _ in range(15):
        response = await llm.complete(messages, model=model, tools=SCHEMAS)
        message = response.choices[0].message
        calls = message.tool_calls or []
        if len(calls) > 16:
            raise RuntimeError("Too many tool calls.")
        if message.content:
            emit({"type": "assistant", "text": message.content})
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
        for call in calls:
            name = call.function.name
            try:
                args = json.loads(call.function.arguments)
                if name not in known or not isinstance(args, dict) or "op" in args:
                    raise ValueError("Tool is not allowed.")
            except (ValueError, TypeError):
                raise RuntimeError("Invalid tool call.")
            emit({"type": "tool_call", "name": name, "args": args})
            await asyncio.to_thread(lifecycle.prepare, rid)
            result = await asyncio.to_thread(
                client.RunnerClient().execute, rid, {"op": name, **args}
            )
            output = json.dumps(result, ensure_ascii=False)
            # Both persistence and next model input are bounded.
            output = output[:70000]
            emit({"type": "tool_result", "name": name, "output": output})
            messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": output}
            )
            if result.get("timed_out"):
                raise RuntimeError("Sandbox command timed out; container removed.")
    raise RuntimeError("Maximum agent turns reached.")
