"""agent / context."""

from __future__ import annotations

from typing import Any, Dict, List


def transcript_text(messages: List[Dict[str, Any]], max_chars: int = 24000) -> str:
    """Render a conversation for the evaluator, keeping recent messages."""
    rendered: List[str] = []
    for m in messages:
        role = str(m.get("role", "?"))
        content = m.get("content")
        if content is None:
            content = ""
        if isinstance(content, list):
            content = "\n".join(
                str(b.get("text", "")) for b in content if isinstance(b, dict)
            )
        tool_calls = m.get("tool_calls") or []
        if tool_calls:
            names = ", ".join(
                str(tc.get("function", {}).get("name", ""))
                for tc in tool_calls if isinstance(tc, dict)
            )
            content = content + (f"\n[tool_use: {names}]" if names else "")
        rendered.append(f"{role.upper()}:\n{content}")

    selected: List[str] = []
    size = 0
    for item in reversed(rendered):
        n = len(item) + 2
        if not selected and n > max_chars:
            marker = "\n...[middle omitted]...\n"
            avail = max(0, max_chars - len(marker))
            head = avail * 3 // 4
            tail = avail - head
            selected.append(item[:head] + marker + item[-tail:] if avail else marker[:max_chars])
            break
        if selected and size + n > max_chars:
            break
        selected.append(item)
        size += n
    return "\n\n".join(reversed(selected))
