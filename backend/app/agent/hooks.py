"""agent / hooks."""

from __future__ import annotations

from typing import Any, Callable, Dict, List


class HookSystem:
    """Extension points around the agent loop; a non-None hook result blocks."""

    def __init__(self):
        self._hooks: Dict[str, List[Callable[..., Any]]] = {
            "UserPromptSubmit": [], "PreToolUse": [], "PostToolUse": [], "Stop": [],
        }

    def register(self, event: str, callback: Callable[..., Any]):
        self._hooks.setdefault(event, []).append(callback)

    def trigger(self, event: str, *args: Any) -> Any:
        for callback in self._hooks.get(event, []):
            result = callback(*args)
            if result is not None:
                return result
        return None
