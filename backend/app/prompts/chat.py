"""Chat instructions. Used by the public harness; variables are runtime data, not policy overrides."""
from .types import PromptSpec

# Version changes accompany reviewed behavioral changes; the hash detects all edits.
CHAT_PROMPT = 'You are a text-only assistant. You may answer questions, explain code and provide code examples. You cannot execute commands or create, read, edit or delete files. For those actions, tell the user to create or open a Project. Never claim to have performed file actions.'
CHAT_PROMPT_SPEC = PromptSpec('chat', "1", CHAT_PROMPT, ())
