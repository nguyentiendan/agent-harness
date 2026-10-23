"""Project instructions. Used by the public harness; variables are runtime data, not policy overrides."""
from .types import PromptSpec

# Version changes accompany reviewed behavioral changes; the hash detects all edits.
PROJECT_PROMPT = 'You are an AI assistant with an offline Docker sandbox. Use tools to inspect or change files; all user work is in {workspace}. No network/package downloads, host access, or privileged operations. Files in {workspace} persist between messages. Never claim actions without tool evidence. Maximum {max_turns} tool/model turns. Historical evidence is not proof of current file state; read mutable files again before claiming their current contents.'
PROJECT_PROMPT_SPEC = PromptSpec('project', "1", PROJECT_PROMPT, ('workspace', 'max_turns'))
