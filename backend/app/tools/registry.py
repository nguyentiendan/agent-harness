"""tools / registry."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import asyncio
import os

from app.integrations.mcp import MCPManager
from app.security.permissions import PermissionDenied


def _function_schema(name: str, description: str, properties: Dict[str, Any], required: List[str]) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


class ToolRegistry:
    """Registers native Python tools and MCP tools, and dispatches calls."""

    def __init__(self, workspace_root: Path, mcp_manager: Optional[MCPManager] = None, bash_timeout: int = 120):
        self.workspace_root = workspace_root.resolve()
        self.mcp_manager = mcp_manager
        self.bash_timeout = bash_timeout
        self.tools: Dict[str, Dict[str, Any]] = {}  # name -> {"schema", "handler", "mcp"}
        self._register_default_tools()

    # -- path safety -------------------------------------------------
    def safe_path(self, path: str) -> Path:
        p = (self.workspace_root / path).resolve()
        try:
            p.relative_to(self.workspace_root)
        except ValueError:
            raise PermissionDenied(f"path escapes the workspace: {path}")
        return p

    # -- sanitized shell environment --------------------------------
    _BASH_ENV_KEEP = {"PATH", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "TZ", "SHELL"}

    def bash_env(self) -> Dict[str, str]:
        """Shell commands run without API keys/secrets in their environment."""
        env = {k: v for k, v in os.environ.items() if k in self._BASH_ENV_KEEP}
        env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
        env["HOME"] = str(self.workspace_root)
        env["PWD"] = str(self.workspace_root)
        return env

    # -- registration ------------------------------------------------
    def _register_default_tools(self):
        from app.tools import filesystem, shell, web
        for module in (filesystem, shell, web):
            module.register(self, _function_schema)

    def add_mcp_tools(self):
        """Merge discovered MCP tools into the pool (mcp__server__tool)."""
        if not self.mcp_manager:
            return
        for schema in self.mcp_manager.get_registered_tool_schemas():
            name = schema.get("function", {}).get("name")
            if name:
                self.tools[name] = {"schema": schema, "handler": None, "mcp": True}

    def register_tool(self, name: str, schema: Dict[str, Any], handler: Callable[..., Any]):
        """Register an external tool (native handler, OpenAI function schema)."""
        self.tools[name] = {"schema": schema, "handler": handler, "mcp": False}

    def get_openai_tools(self) -> List[Dict[str, Any]]:
        return [t["schema"] for t in self.tools.values()]

    async def execute(self, name: str, args: Dict[str, Any]) -> str:
        entry = self.tools.get(name)
        if entry is None:
            return f"Unknown tool: {name}"
        if entry["mcp"]:
            return await self.mcp_manager.call_mcp_tool(name, args)
        handler = entry["handler"]
        if asyncio.iscoroutinefunction(handler):
            return str(await handler(**args))
        return str(await asyncio.to_thread(handler, **args))
