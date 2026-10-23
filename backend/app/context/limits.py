"""Normalize provider limits without conflating input and combined windows."""
from dataclasses import dataclass
from .policy import ContextPolicy

@dataclass(frozen=True)
class ModelLimits:
    context_window: int
    max_input_tokens: int | None
    max_output_tokens: int
    revision: str

def resolve_model_limits(metadata: dict, policy: ContextPolicy) -> ModelLimits:
    window = metadata.get("context_window")
    input_cap = metadata.get("max_input_tokens")
    if window is None:
        # Existing catalog/gateway records used this field for the window.
        window, input_cap = input_cap, None
    output = metadata.get("max_output_tokens")
    if any(type(v) is not int or v <= 0 for v in (window, output)):
        raise ValueError("Verified model context/output limits are required")
    if input_cap is not None and (type(input_cap) is not int or input_cap <= 0):
        raise ValueError("Invalid model input cap")
    return ModelLimits(window, input_cap, output, str(metadata.get("revision", "legacy")))

def effective_output_cap(requested: int, user_cap: int, limits: ModelLimits) -> int:
    if min(requested, user_cap) <= 0:
        raise ValueError("Output token cap must be positive")
    return min(requested, user_cap, limits.max_output_tokens)
