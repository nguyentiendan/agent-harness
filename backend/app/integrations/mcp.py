"""
Model Context Protocol (MCP) Client Manager for Python Agent Harness
======================================================================
Tích hợp Model Context Protocol (MCP) cho Harness:
1. MCPClient: Quản lý kết nối JSON-RPC 2.0 qua Stdio (Subprocess) và SSE (HTTP).
2. MCPManager: Tự động kết nối nhiều MCP Servers từ config.yaml, phát hiện công cụ (tools/list)
   và chuyển đổi sang định dạng Tool Engine / LiteLLM.
3. Executer: Thực thi tool call (tools/call) và trả kết quả cho Agent.
"""


from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
import asyncio
import json
import os

import sys


class MCPTransportError(Exception):
    """Lỗi khi truyền nhận dữ liệu với MCP Server."""
    pass


class StdioMCPClient:
    """MCP Client giao tiếp qua Subprocess Stdio (Standard I/O)."""

    def __init__(self, command: str, args: List[str], env: Optional[Dict[str, str]] = None):
        self.command = command
        self.args = args
        self.env = {**os.environ, **(env or {})}
        self.process: Optional[asyncio.subprocess.Process] = None
        self._msg_id = 0

    async def connect(self):
        """Khởi chạy MCP Server Subprocess và thiết lập đường truyền stdio."""
        cmd_str = f"{self.command} {' '.join(self.args)}"
        print(f"[MCP StdioClient] Launching server: {cmd_str}")
        try:
            self.process = await asyncio.create_subprocess_exec(
                self.command,
                *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self.env
            )
            # Send initialize RPC
            init_response = await self.send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "PythonAgentHarness", "version": "1.0.0"}
            })
            
            # Send initialized notification
            await self.send_notification("notifications/initialized", {})
            print(f"[MCP StdioClient] Connected successfully to {self.command}")
            return init_response
        except Exception as e:
            raise MCPTransportError(f"Failed to start MCP server process '{cmd_str}': {e}")

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    async def send_request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not self.process or not self.process.stdin or not self.process.stdout:
            raise MCPTransportError("MCP Server process is not connected.")

        req_id = self._next_id()
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params or {}
        }
        
        raw_msg = json.dumps(payload) + "\n"
        self.process.stdin.write(raw_msg.encode("utf-8"))
        await self.process.stdin.drain()

        # Read line response
        while True:
            line = await self.process.stdout.readline()
            if not line:
                raise MCPTransportError("MCP Server connection closed unexpectedly.")
            
            line_str = line.decode("utf-8").strip()
            if not line_str:
                continue
                
            try:
                data = json.loads(line_str)
                if data.get("id") == req_id:
                    if "error" in data:
                        raise MCPTransportError(f"MCP RPC Error [{method}]: {data['error']}")
                    return data.get("result", {})
            except json.JSONDecodeError:
                # Ignore non-json debug lines output by server
                continue

    async def send_notification(self, method: str, params: Optional[Dict[str, Any]] = None):
        if not self.process or not self.process.stdin:
            return
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {}
        }
        raw_msg = json.dumps(payload) + "\n"
        self.process.stdin.write(raw_msg.encode("utf-8"))
        await self.process.stdin.drain()

    async def close(self):
        if self.process:
            try:
                self.process.terminate()
                await self.process.wait()
            except Exception:
                pass


class MCPManager:
    """
    Bộ quản lý đa kết nối MCP Servers.
    Tự động kết nối, tự động phát hiện (Auto-Discovery) danh sách công cụ 
    và chuyển đổi thành định dạng schema phù hợp cho Agent ToolEngine.
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.mcp_servers_config = config.get("mcp_servers", {})
        self.clients: Dict[str, StdioMCPClient] = {}
        self.discovered_tools: Dict[str, Dict[str, Any]] = {}  # qualified_name -> tool_info

    async def initialize_all_servers(self):
        """Khởi tạo tất cả kết nối MCP Server từ cấu hình config.yaml."""
        if not self.mcp_servers_config:
            print("[MCPManager] No MCP servers configured in config.yaml.")
            return

        for server_name, srv_cfg in self.mcp_servers_config.items():
            if not srv_cfg.get("enabled", True):
                print(f"[MCPManager] Skipping disabled server: {server_name}")
                continue

            transport = srv_cfg.get("transport", "stdio")
            if transport == "stdio":
                command = srv_cfg.get("command")
                args = srv_cfg.get("args", [])
                env = srv_cfg.get("env", {})
                
                if not command:
                    print(f"[MCPManager] Warning: Missing command for MCP server '{server_name}'")
                    continue

                try:
                    client = StdioMCPClient(command, args, env)
                    await client.connect()
                    self.clients[server_name] = client
                    
                    # Discover tools
                    await self._discover_server_tools(server_name, client)
                except Exception as e:
                    print(f"[MCPManager] Error connecting to MCP server '{server_name}': {e}")
            else:
                print(f"[MCPManager] Transport '{transport}' for '{server_name}' is not supported yet (stdio only).")

    async def _discover_server_tools(self, server_name: str, client: StdioMCPClient):
        """Gọi tools/list để tự động lấy danh sách công cụ từ MCP Server."""
        try:
            res = await client.send_request("tools/list")
            tools = res.get("tools", [])
            print(f"[MCPManager] Discovered {len(tools)} tools from MCP server '{server_name}'")
            
            for tool in tools:
                tool_name = tool.get("name")
                qualified_name = f"mcp__{server_name}__{tool_name}"
                
                self.discovered_tools[qualified_name] = {
                    "server_name": server_name,
                    "original_name": tool_name,
                    "description": tool.get("description", ""),
                    "input_schema": tool.get("inputSchema", {"type": "object", "properties": {}}),
                    "client": client
                }
                print(f"  -> Discovered tool: {qualified_name} - {tool.get('description')[:60]}...")
        except Exception as e:
            print(f"[MCPManager] Failed to list tools for '{server_name}': {e}")

    def get_registered_tool_schemas(self) -> List[Dict[str, Any]]:
        """Xuất danh sách công cụ MCP dưới dạng JSON Schema chuẩn LiteLLM / OpenAI Function Calling."""
        schemas = []
        for qualified_name, info in self.discovered_tools.items():
            schemas.append({
                "type": "function",
                "function": {
                    "name": qualified_name,
                    "description": f"[{info['server_name']}] {info['description']}",
                    "parameters": info["input_schema"]
                }
            })
        return schemas

    async def call_mcp_tool(self, qualified_name: str, arguments: Dict[str, Any]) -> str:
        """Thực thi một MCP Tool Call qua giao thức tools/call."""
        if qualified_name not in self.discovered_tools:
            return f"[MCP ERROR] Tool '{qualified_name}' is not registered in MCPManager."

        tool_info = self.discovered_tools[qualified_name]
        client: StdioMCPClient = tool_info["client"]
        orig_name = tool_info["original_name"]

        try:
            print(f"[MCPManager] Executing tool '{orig_name}' on server '{tool_info['server_name']}'...")
            res = await client.send_request("tools/call", {
                "name": orig_name,
                "arguments": arguments
            })
            
            content_blocks = res.get("content", [])
            output_texts = []
            for block in content_blocks:
                if block.get("type") == "text":
                    output_texts.append(block.get("text", ""))
                elif block.get("type") == "image":
                    output_texts.append("[Image Content Received]")
                else:
                    output_texts.append(str(block))

            return "\n".join(output_texts) if output_texts else json.dumps(res, ensure_ascii=False)
        except Exception as e:
            return f"[MCP TOOL ERROR] {str(e)}"

    async def shutdown(self):
        """Đóng tất cả các kết nối MCP Server khi tắt ứng dụng."""
        for server_name, client in self.clients.items():
            print(f"[MCPManager] Closing connection to '{server_name}'...")
            await client.close()


# =====================================================================
# DEMO TESTING
# =====================================================================
if __name__ == "__main__":
    async def demo():
        demo_config = {
            "mcp_servers": {
                "memory": {
                    "enabled": True,
                    "transport": "stdio",
                    "command": "python3",
                    "args": ["-c", "import sys, json; print(json.dumps({'jsonrpc':'2.0','id':1,'result':{'protocolVersion':'2024-11-05'}})); sys.stdout.flush()"]
                }
            }
        }
        manager = MCPManager(demo_config)
        print("MCP Manager module initialized successfully.")

    asyncio.run(demo())
