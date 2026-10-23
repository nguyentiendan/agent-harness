"""Strict single-pass rendering; inserted user data is never evaluated."""
from .types import PromptSpec, RenderedPrompt

def render_prompt(spec: PromptSpec, values: dict[str, str | int]) -> RenderedPrompt:
    if set(values) != set(spec.variables):
        raise ValueError("Prompt variables do not match schema")
    if any(type(v) not in (str, int) for v in values.values()):
        raise ValueError("Prompt variables must be strings or integers")
    return RenderedPrompt(spec.template.format_map(values), spec.id, spec.version, spec.content_hash)
