"""Immutable prompt contracts and stable content identity."""

from dataclasses import dataclass
import hashlib
import json
from string import Formatter


@dataclass(frozen=True)
class PromptSpec:
    id: str
    version: str
    template: str
    variables: tuple[str, ...] = ()

    def __post_init__(self):
        fields = []
        for _, name, spec, conversion in Formatter().parse(self.template):
            if name is not None:
                if not name.isidentifier() or spec or conversion:
                    raise ValueError("Only simple named prompt variables are allowed")
                fields.append(name)
        if set(fields) != set(self.variables):
            raise ValueError("Prompt schema does not match template")

    @property
    def content_hash(self):
        return hashlib.sha256(
            json.dumps(
                [self.template, sorted(self.variables)], ensure_ascii=False
            ).encode()
        ).hexdigest()


@dataclass(frozen=True)
class RenderedPrompt:
    text: str
    id: str
    version: str
    content_hash: str
