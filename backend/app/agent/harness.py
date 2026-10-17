"""agent / harness."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

from app.agent.goals import GoalController, PromptGoalEvaluator
from app.agent.session import AgentSession
from app.agent.state import StateManager
from app.agent.types import GoalFailedError, SessionResult
from app.agent.workspace import WorkspaceManager
from app.integrations.llm import LiteLLMClientWrapper
from app.integrations.mcp import MCPManager
from app.security.paths import DEFAULT_ALLOWED_PATH_PREFIXES
from app.security.permissions import PermissionManager
from app.tools.registry import ToolRegistry
import app.capabilities.background as background_mod
import app.capabilities.memory as memory_mod
import app.capabilities.scheduler as scheduler_mod
import app.capabilities.skills as skills_mod
import app.capabilities.tasks as tasks_mod
import app.capabilities.teams as teams_mod
import app.capabilities.workflow as workflow_mod


class Harness:
    """Builds the tool pool, permission, hooks, evaluator and runs the agent loop."""

    def __init__(self, config_file: str = "config.yaml", workspace_path: Optional[str] = None,
                 default_model: Optional[str] = None,
                 events: Optional[List[Dict[str, Any]]] = None,
                 approve: Optional[Callable[[str, Dict[str, Any], str], Any]] = None):
        self.config_path = Path(config_file)
        self.config = self._load_config()

        ws_path = Path(workspace_path or self.config.get("workspace", {}).get("root_dir", "./workspace_demo"))
        if not ws_path.is_absolute():
            ws_path = (self.config_path.resolve().parent / ws_path).resolve()

        self.workspace = WorkspaceManager(ws_path, self.config)
        self.workspace.init_workspace()
        self.state_mgr = StateManager(ws_path)
        self.mcp_manager = MCPManager(self.config)

        self.events = events if events is not None else []
        self.approve = approve
        self._stop_requested = False

    def _load_config(self) -> Dict[str, Any]:
        if self.config_path.exists():
            with open(self.config_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        return {}

    def get_config(self) -> Dict[str, Any]:
        return self.config

    def request_stop(self):
        self._stop_requested = True

    def is_stop_requested(self) -> bool:
        return self._stop_requested

    def _build_system_prompt(self, skills_catalog: str = "", memory_index: str = "") -> str:
        rules = self.workspace.get_system_prompt()
        sections = [
            "You are an autonomous coding agent working inside a harness.\n"
            f"Workspace root: {self.workspace.root_dir}\n"
            "Use the provided tools to complete the user's request. Prefer dedicated file "
            "tools over bash for file operations. Report concrete results (exit codes, file "
            "paths, command output) so an independent evaluator can verify completion.",
        ]
        if skills_catalog:
            sections.append(
                f"Skills available:\n{skills_catalog}\n\n"
                "Use load_skill to read the full instructions when a skill applies."
            )
        if memory_index:
            sections.append(f"Memory catalog:\n{memory_index}")
        sections.append(rules)
        return "\n\n".join(sections)

    async def run(self, goal: str, max_turns: Optional[int] = None, reviewer_model: Optional[str] = None,
                  history: Optional[List[Dict[str, Any]]] = None) -> SessionResult:
        self._stop_requested = False
        print(f"=== Starting Agent Harness for Goal: '{goal}' ===")

        try:
            await self.mcp_manager.initialize_all_servers()

            llm_cfg = self.config.get("llm", {})
            exec_cfg = self.config.get("execution", {})
            sec_cfg = self.config.get("security", {})

            primary_model = self.config.get("llm", {}).get("primary_model", "gpt-4o-mini")
            evaluator_model = reviewer_model or llm_cfg.get("reviewer_model", "gpt-4o-mini")
            turns_limit = max_turns or exec_cfg.get("max_turns", 15)

            tools = ToolRegistry(self.workspace.root_dir, self.mcp_manager,
                                 bash_timeout=int(exec_cfg.get("bash_timeout", 120)))
            tools.add_mcp_tools()

            llm = LiteLLMClientWrapper(
                default_model=primary_model,
                temperature=float(llm_cfg.get("temperature", 0.2)),
                max_tokens=int(llm_cfg.get("max_tokens", 4096)),
            )
            bash_sec = sec_cfg.get("bash") if isinstance(sec_cfg.get("bash"), dict) else {}
            permission = PermissionManager(
                self.workspace.root_dir,
                approval_mode=str(exec_cfg.get("approval_mode", "auto_deny")),
                deny_patterns=list(sec_cfg.get("deny_file_patterns", [])),
                restrict_paths=bool(bash_sec.get("restrict_paths", True)),
                allowed_path_prefixes=list(
                    bash_sec.get("allowed_path_prefixes", DEFAULT_ALLOWED_PATH_PREFIXES)
                ),
            )
            evaluator = PromptGoalEvaluator(llm, evaluator_model)
            goal_controller = GoalController(evaluator, block_cap=int(exec_cfg.get("goal_block_cap", 8)))

            # ---- Register additional harness mechanisms (learn-claude-code s07-s16) ----
            ws = self.workspace.root_dir
            skills_loader = skills_mod.register(tools, ws / "skills")
            memory_store = memory_mod.register(tools, ws / ".memory")
            task_store = tasks_mod.register(tools, ws / ".tasks")
            background_mod.register(tools, ws, int(exec_cfg.get("bash_timeout", 600)))
            cron_store = scheduler_mod.register(tools, ws / ".scheduled_tasks.json")
            workflow_mod.register(tools, llm, primary_model, ws / ".runtime")

            async def run_subagent(prompt: str, workdir: Path) -> str:
                sub = AgentSession(
                    llm=llm, tools=tools, permission=permission, state_mgr=self.state_mgr,
                    goal=GoalController(evaluator, block_cap=3),
                    model=primary_model, system_prompt=prompt,
                    max_turns=6, should_stop=self.is_stop_requested,
                    emit=self.events.append,
                )
                r = await sub.run(prompt)
                return r.text or f"(subagent finished: {r.status})"

            teams_mod.register(tools, task_store, ws / "worktrees", run_subagent)

            # Inject due cron prompts (a single run is short-lived; jobs persist to disk).
            for p in cron_store.poll_due():
                self.state_mgr.log_progress(f"[cron] due: {p}")

            system_prompt = self._build_system_prompt(skills_loader.catalog(), memory_store.index_text())

            session = AgentSession(
                llm=llm, tools=tools, permission=permission, state_mgr=self.state_mgr,
                goal=goal_controller, model=primary_model,
                system_prompt=system_prompt,
                max_turns=turns_limit, should_stop=self.is_stop_requested,
                emit=self.events.append, approve=self.approve,
            )

            self.state_mgr.log_progress(f"Starting agent for goal: {goal}")
            result = await session.run(goal, history=history)
            self.state_mgr.log_progress(f"Finished: {result.status} - {result.reason}")

            state = self.state_mgr.load_state()
            state["goal"] = goal
            state["status"] = result.status
            state["reason"] = result.reason
            state["turn"] = session.turns
            self.state_mgr.save_state(state)

            if result.status in ("failed", "max_turns", "error", "repetition"):
                raise GoalFailedError(result.reason or result.status)

            if self._stop_requested:
                print("\n=== Harness Stopped by User ===")
            else:
                print(f"\n=== Harness Complete: {result.status} ===")
            return result
        finally:
            await self.mcp_manager.shutdown()
