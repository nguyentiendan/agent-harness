"""Data contracts shared by history, selection and summarization."""

from dataclasses import dataclass, field
from .limits import ModelLimits


@dataclass(frozen=True)
class ConversationRecord:
    id: str
    role: str
    message: dict
    group_id: str
    workspace_revision: int | None = None


@dataclass(frozen=True)
class ConversationGroup:
    id: str
    messages: list[dict]
    source_ids: list[str]
    workspace_revision: int | None = None
    sequence: int = 0
    current_run: bool = False


@dataclass(frozen=True)
class TokenEstimate:
    tokens: int
    estimator_id: str
    approximate: bool = True


@dataclass
class ContextRequest:
    scope_id: str
    run_id: str
    model: str
    limits: ModelLimits
    effective_output_cap: int
    mandatory: list[dict]
    groups: list[ConversationGroup] = field(default_factory=list)
    tools: list[dict] = field(default_factory=list)
    workspace_revision: int | None = None
    summary: dict | None = None
    summary_source_ids: list[str] = field(default_factory=list)


@dataclass
class PreparedContext:
    messages: list[dict]
    manifest: dict
    effective_max_output_tokens: int


class ContextBudgetExceeded(ValueError):
    code = "context_budget_exceeded"
