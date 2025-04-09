import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

from mcp.types import TextContent

from aworld.core.common import ActionModel, ActionResult, Observation
from aworld.mcp.server import MCPServer, MCPServerSse


class MCPToolExecutor:
    """A tool executor that uses MCP server to execute actions."""

    def __init__(self):
        """Initialize the MCP tool executor."""
        self.initialized = False
        self.mcp_servers: Dict[str, MCPServer] = {}
        self._load_mcp_config()

    def _load_mcp_config(self) -> None:
        """Load MCP server configurations from config file."""
        try:
            # Use relative path for config file
            current_dir = os.path.dirname(os.path.abspath(__file__))
            config_path = os.path.normpath(os.path.join(current_dir, "../config/mcp.json"))
            
            with open(config_path, "r") as f:
                config_data = json.load(f)
                
            # Load all server configurations
            for server_name, server_config in config_data.get("mcpServers", {}).items():
                if "url" in server_config:
                    self.mcp_servers[server_name] = {
                        "url": server_config["url"],
                        "instance": None
                    }
            
            self.initialized = True
        except Exception as e:
            logging.error(f"Failed to load MCP config: {e}")

    async def _get_or_create_server(self, server_name: str) -> MCPServer:
        """Get an existing MCP server instance or create a new one."""
        if server_name not in self.mcp_servers:
            raise ValueError(f"MCP server '{server_name}' not found in configuration")
            
        server_info = self.mcp_servers[server_name]
        if server_info["instance"] is None:
            # Create new SSE server instance
            server_params = {
                "url": server_info["url"],
                "timeout": 5.0,
                "sse_read_timeout": 300.0  # 5 minutes
            }
            
            server = MCPServerSse(server_params, cache_tools_list=True, name=server_name)
            await server.connect()
            server_info["instance"] = server
            
        return server_info["instance"]

    async def execute_action(self, actions: List[ActionModel], **kwargs) -> List[ActionResult]:
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
            return []
            
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
                    if isinstance(result.content[0],TextContent):
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
        
        return results

    async def cleanup(self) -> None:
        """Clean up all MCP server connections."""
        for server_name, server_info in self.mcp_servers.items():
            if server_info.get("instance"):
                try:
                    await server_info["instance"].cleanup()
                except Exception as e:
                    logging.error(f"Error cleaning up MCP server {server_name}: {e}")

    def step(self, actions: List[ActionModel], **kwargs) -> Tuple[Observation, float, bool, bool, Dict[str, Any]]:
        """Execute actions and return observation, reward, done flags and info.
        
        Args:
            actions: A list of action models to execute
            **kwargs: Additional arguments
            
        Returns:
            A tuple containing:
            - observation: The observation after executing the action
            - reward: The reward for the action
            - terminated: Whether the episode is terminated
            - truncated: Whether the episode is truncated
            - info: Additional information
        """
        if not self.initialized:
            raise RuntimeError("Call init first before calling step.")

        # Create the event loop or get the existing one
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        reward = 0
        fail_error = ""
        action_results = None

        try:
            # Execute the action asynchronously
            action_results = loop.run_until_complete(self.execute_action(actions, **kwargs))
            reward = 1
        except (ValueError, IOError, RuntimeError) as e:
            fail_error = str(e)
        finally:
            loop.close()


        terminated = kwargs.get("terminated", False)
        if action_results:
            for res in action_results:
                if res.is_done:
                    terminated = res.is_done
                if not res.success and res.error:
                    fail_error = res.error

        info = {"exception": fail_error}

        observation = Observation(dom_tree="", image="", action_result=[], info={})
        if action_results:
            observation.action_result = action_results
            if action_results and len(action_results) > 0:
                observation.content = action_results[-1].content
        
        return (observation, reward, terminated, kwargs.get("truncated", False), info)
