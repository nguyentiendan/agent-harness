"""agent / session."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional
import json

from app.agent.goals import GoalController
from app.agent.hooks import HookSystem
from app.agent.state import StateManager
from app.agent.types import SessionResult
from app.integrations.llm import LiteLLMClientWrapper
from app.security.permissions import ApprovalRequired, PermissionDenied, PermissionManager
from app.tools.registry import ToolRegistry


class AgentSession:
    """A real agent loop: LLM decides -> tools execute -> results feed back."""

    def __init__(self, llm: LiteLLMClientWrapper, tools: ToolRegistry,
                 permission: PermissionManager, state_mgr: StateManager,
                 goal: GoalController, model: str, system_prompt: str,
                 max_turns: Optional[int] = None,
                 should_stop: Optional[Callable[[], bool]] = None,
                 emit: Optional[Callable[[Dict[str, Any]], None]] = None,
                 approve: Optional[Callable[[str, Dict[str, Any], str], Any]] = None):
        self.llm = llm
        self.tools = tools
        self.permission = permission
        self.state_mgr = state_mgr
        self.goal = goal
        self.model = model
        self.system_prompt = system_prompt
        self.max_turns = max_turns
        self.should_stop = should_stop or (lambda: False)
        self.emit = emit or (lambda ev: None)
        self.approve = approve
        self.messages: List[Dict[str, Any]] = []
        self.hooks = HookSystem()
        self.turns = 0
        self._register_hooks()

    def _checkpoint(self, turn: int, status: str = "running"):
        """Persist the current step number so the UI can show progress."""
        try:
            state = self.state_mgr.load_state()
            state["turn"] = turn
            state["goal"] = self.goal.active_condition or state.get("goal", "")
            state["status"] = status
            self.state_mgr.save_state(state)
        except Exception:
            pass

    def _register_hooks(self):
        self.hooks.register("PreToolUse", self.permission.check)
        self.hooks.register("PreToolUse", self._log_tool_hook)
        self.hooks.register("PostToolUse", self._log_result_hook)

    def _log_tool_hook(self, name: str, args: Dict[str, Any]) -> None:
        preview = str(list(args.values())[:2])[:80]
        self.state_mgr.log_progress(f"> {name}({preview})")
        return None

    def _log_result_hook(self, name: str, args: Dict[str, Any], output: str) -> None:
        self.state_mgr.log_progress(f"  => {str(output)[:500]}")
        return None

    @staticmethod
    def _tool_call_to_dict(tc: Any) -> Dict[str, Any]:
        return {
            "id": tc.id,
            "type": "function",
            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
        }

    async def _execute_tool(self, name: str, args: Dict[str, Any]) -> str:
        try:
            return await self.tools.execute(name, args)
        except PermissionDenied as e:
            return f"Permission denied: {e}"
        except Exception as e:
            return f"{type(e).__name__}: {e}"

    async def run(self, goal: str, history: Optional[List[Dict[str, Any]]] = None) -> SessionResult:
        self.messages = [
            {"role": "system", "content": self.system_prompt},
            *(history or []),
            {"role": "user", "content": goal},
        ]
        self.goal.set_goal(goal)
        self.turns = 0
        recent_calls: List[str] = []

        while not self.should_stop():
            if self.max_turns is not None and self.turns >= self.max_turns:
                return SessionResult(
                    text="", status="max_turns",
                    reason=f"reached max_turns={self.max_turns} without satisfying the goal",
                )
            self.turns += 1
            self._checkpoint(self.turns)

            response = await self.llm.complete(
                self.messages, model=self.model, tools=self.tools.get_openai_tools()
            )
            message = response.choices[0].message
            assistant: Dict[str, Any] = {"role": "assistant", "content": message.content or ""}
            tool_calls = list(getattr(message, "tool_calls", None) or [])
            if tool_calls:
                assistant["tool_calls"] = [self._tool_call_to_dict(tc) for tc in tool_calls]
            self.messages.append(assistant)

            # The model chose not to call a tool -> goal gate decides whether to stop.
            if not tool_calls:
                decision = await self.goal.evaluate_after_turn(self.messages)
                if decision.action == "achieved":
                    text = message.content or ""
                    self.emit({"type": "assistant", "text": text})
                    self.hooks.trigger("Stop", self.messages)
                    return SessionResult(text=text, status="achieved", reason=decision.reason)
                if decision.action == "failed":
                    return SessionResult(text="", status="failed", reason=decision.reason)
                if decision.action == "limit":
                    return SessionResult(text="", status="max_turns", reason=decision.reason)
                if decision.action == "error":
                    return SessionResult(text="", status="error", reason=decision.reason)
                # block: send evaluator feedback back into the loop
                self.messages.append({
                    "role": "user",
                    "content": (
                        "[Goal not yet satisfied]\n"
                        f"Evaluator: {decision.reason}\n"
                        "Continue working and surface the missing evidence."
                    ),
                })
                continue

            # Execute each tool call and feed results back.
            for tc in tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except Exception:
                    args = {}

                # Repetition guard: stop if the agent repeats the same call.
                sig = f"{name}:{json.dumps(args, sort_keys=True)}"
                if recent_calls.count(sig) >= 2:
                    self.state_mgr.log_progress(f"Repetition guard: agent repeated {name}")
                    return SessionResult(text="", status="repetition",
                                         reason=f"agent repeated the same tool call ({name})")
                recent_calls.append(sig)

                self.emit({"type": "tool_call", "name": name, "args": args})
                blocked = self.hooks.trigger("PreToolUse", name, args)
                if isinstance(blocked, ApprovalRequired):
                    allowed = False
                    if self.approve is not None:
                        allowed = bool(await self.approve(name, args, blocked.reason))
                    self.emit({"type": "approval_result", "name": name, "allowed": allowed})
                    if allowed:
                        output = await self._execute_tool(name, args)
                        self.hooks.trigger("PostToolUse", name, args, output)
                    else:
                        output = "Permission denied by user"
                elif blocked is not None:
                    output = str(blocked)
                else:
                    output = await self._execute_tool(name, args)
                    self.hooks.trigger("PostToolUse", name, args, output)

                self.emit({"type": "tool_result", "name": name, "output": str(output)[:8000]})

                # Context compaction: cap tool output fed back to the model.
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": str(output)[:8000],
                })

        return SessionResult(text="", status="stopped", reason="stopped by user")
