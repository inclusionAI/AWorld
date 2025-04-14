import json
import logging
import os
import traceback

from typing import Any, Dict, List, Tuple

from mcp.types import TextContent

from aworld.core.common import ActionModel, ActionResult, Observation
from aworld.core.envs.tool import ToolActionExecutor, Tool
from aworld.mcp.server import MCPServer, MCPServerSse
from aworld.utils.common import sync_exec


class MCPToolExecutor(ToolActionExecutor):
    """A tool executor that uses MCP server to execute actions."""

    def __init__(self, tool: Tool[Observation, List[ActionModel]] = None):
        """Initialize the MCP tool executor."""
        super().__init__(tool)
        self.initialized = False
        self.mcp_servers: Dict[str, MCPServer] = {}
        self._load_mcp_config()

    def _load_mcp_config(self) -> None:
        """Load MCP server configurations from config file."""
        try:
            # Priority given to the running path.
            if os.path.exists(os.path.join(os.getcwd(), "mcp.json")):
                config_path = os.path.join(os.getcwd(), "mcp.json")
            else:
                # Use relative path for config file
                current_dir = os.path.dirname(os.path.abspath(__file__))
                config_path = os.path.normpath(os.path.join(current_dir, "../../config/mcp.json"))

            with open(config_path, "r") as f:
                config_data = json.load(f)

            # Load all server configurations
            for server_name, server_config in config_data.get("mcpServers", {}).items():
                # Skip disabled servers
                if server_config.get("disabled", False):
                    continue
                    
                # Handle SSE server
                if "url" in server_config:
                    self.mcp_servers[server_name] = {
                        "type": "sse",
                        "url": server_config["url"],
                        "instance": None,
                        "timeout": server_config.get('timeout', 5.),
                        "sse_read_timeout": server_config.get('sse_read_timeout', 300.0),
                        "headers": server_config.get('headers')
                    }
                # Handle stdio server
                elif "command" in server_config:
                    self.mcp_servers[server_name] = {
                        "type": "stdio",
                        "command": server_config["command"],
                        "args": server_config.get("args", []),
                        "env": server_config.get("env", {}),
                        "cwd": server_config.get("cwd"),
                        "encoding": server_config.get("encoding", "utf-8"),
                        "encoding_error_handler": server_config.get("encoding_error_handler", "strict"),
                        "instance": None
                    }
            
            self.initialized = True
        except Exception as e:
            logging.error(f"Failed to load MCP config: {traceback.format_exc()}")

    async def _get_or_create_server(self, server_name: str) -> MCPServer:
        """Get an existing MCP server instance or create a new one."""
        if server_name not in self.mcp_servers:
            raise ValueError(f"MCP server '{server_name}' not found in configuration")

        server_info = self.mcp_servers[server_name]
        server_type = server_info.get("type", "sse")

        if server_type == "sse":
            # Create new SSE server instance
            server_params = {
                "url": server_info["url"],
                "timeout": server_info['timeout'],
                "sse_read_timeout": server_info['sse_read_timeout'],
                "headers": server_info['headers']
            }

            server = MCPServerSse(server_params, cache_tools_list=True, name=server_name)
        elif server_type == "stdio":
            # Create new stdio server instance
            server_params = {
                "command": server_info["command"],
                "args": server_info["args"],
                "env": server_info["env"],
                "cwd": server_info.get("cwd"),
                "encoding": server_info["encoding"],
                "encoding_error_handler": server_info["encoding_error_handler"]
            }

            from aworld.mcp.server import MCPServerStdio
            server = MCPServerStdio(server_params, cache_tools_list=True, name=server_name)
        else:
            raise ValueError(f"Unsupported MCP server type: {server_type}")

        await server.connect()
        server_info["instance"] = server

        return server

    async def async_execute_action(self, actions: List[ActionModel], **kwargs) -> Tuple[
        List[ActionResult], Any]:
        """Execute actions using the MCP server.
        
        Args:
            actions: A list of action models to execute
            **kwargs: Additional arguments
            
        Returns:
            A list of action results
        """
        if not self.initialized:
            raise RuntimeError("MCP Tool Executor not initialized")

        if not actions:
            return [], None

        results = []
        for action in actions:
            # Check if this is an MCP action
            # if not action.is_mcp:
            #     raise ValueError(f"Action {action.action_name} is not an MCP action")

            # Get the server name from tool_name
            server_name = action.tool_name
            if not server_name:
                raise ValueError("Missing tool_name in action model")

            # Get the action name
            action_name = action.action_name
            if not action_name:
                raise ValueError("Missing action_name in action model")

            # Get parameters
            params = action.params or {}
            try:
                # Get or create the MCP server
                server = await self._get_or_create_server(server_name)

                # Call the tool on the server
                result = await server.call_tool(action_name, params)
                if result and result.content:
                    if isinstance(result.content[0], TextContent):
                        action_result = ActionResult(
                            content=result.content[0].text,
                            keep=True
                        )
                        results.append(action_result)

            except Exception as e:
                # Create an error action result
                error_msg = str(e)
                logging.error(f"Error executing MCP action: {error_msg}")
                break

        return results, None

    async def cleanup(self) -> None:
        """Clean up all MCP server connections."""
        for server_name, server_info in self.mcp_servers.items():
            if server_info.get("instance"):
                try:
                    await server_info["instance"].cleanup()
                except Exception as e:
                    logging.error(f"Error cleaning up MCP server {server_name}: {e}")

    def execute_action(self, actions: List[ActionModel], **kwargs) -> Tuple[
        List[ActionResult], Any]:
        return sync_exec(self.async_execute_action, actions, **kwargs)
