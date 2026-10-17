"""observability / metrics."""

from __future__ import annotations

from typing import Any, Dict, List
import time


def _compute_metrics(events: List[Dict[str, Any]], started_at: float, status: str,
                     model: str, turns: int) -> Dict[str, Any]:
    """Aggregate basic observability metrics from a run's events."""
    tool_calls = [e for e in events if e.get("type") == "tool_call"]
    tools: Dict[str, int] = {}
    for e in tool_calls:
        name = str(e.get("name", "?"))
        tools[name] = tools.get(name, 0) + 1
    return {
        "status": status,
        "duration_seconds": round(max(0.0, time.time() - started_at), 1),
        "turns": turns,
        "tool_calls": len(tool_calls),
        "tools": tools,
        "approvals": sum(1 for e in events if e.get("type") == "approval"),
        "model": model,
    }
