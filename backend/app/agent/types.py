"""agent / types."""

from __future__ import annotations

from dataclasses import dataclass


class GoalFailedError(Exception):
    """Raised when a run ends without satisfying its goal."""


@dataclass
class GoalEvaluation:
    ok: bool
    reason: str
    impossible: bool = False


@dataclass
class StopDecision:
    action: str
    reason: str = ""


@dataclass
class SessionResult:
    text: str
    status: str
    reason: str = ""
