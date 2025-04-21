import asyncio
import json
import logging
import os
import traceback
import asyncio

from typing import Any, Dict, List, Tuple

from mcp.types import TextContent

from aworld.core.common import ActionModel, ActionResult, Observation
from aworld.core.envs.tool import ToolActionExecutor, Tool
from aworld.logs.util import logger
from aworld.mcp.server import MCPServer, MCPServerSse, MCPServerStdio
from aworld.utils.common import sync_exec, find_file, asyncio_loop


class MCPToolExecutor(ToolActionExecutor):
    """A tool executor that uses MCP server to execute actions."""

    def __init__(self, tool: Tool[Observation, List[ActionModel]] = None):
        """Initialize the MCP tool executor."""
        super().__init__(tool)
        self.initialized = False
        self.mcp_servers: Dict[str, MCPServer] = {}
        self._load_mcp_config()
        # {"server name": {tool config}}, used to more refined configuration
        self.servers_conf = self.tool.conf

    def _load_mcp_config(self) -> None:
        """Load MCP server configurations from config file."""
        try:
            # Priority given to the running path.
            config_path = find_file(filename='mcp.json')
            if not os.path.exists(config_path):
                # Use relative path for config file
                current_dir = os.path.dirname(os.path.abspath(__file__))
                config_path = os.path.normpath(os.path.join(current_dir, "../../config/mcp.json"))
            logger.info(f"mcp conf path: {config_path}")

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
                        "timeout": server_config.get("timeout", 5.0),
                        "sse_read_timeout": server_config.get(
                            "sse_read_timeout", 300.0
                        ),
                        "headers": server_config.get("headers"),
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
                        "encoding_error_handler": server_config.get(
                            "encoding_error_handler", "strict"
                        ),
                        "instance": None,
                    }

            self.initialized = True
        except Exception as e:
            logger.error(f"Failed to load MCP config: {traceback.format_exc()}")

    async def _get_or_create_server(self, server_name: str) -> MCPServer:
        """Get an existing MCP server instance or create a new one."""
        if server_name not in self.mcp_servers:
            raise ValueError(f"MCP server '{server_name}' not found in configuration")

        server_info = self.mcp_servers[server_name]

        # If an instance already exists, check if it's available
        if server_info.get("instance"):
            try:
                # Try listing tools to check if the server connection is healthy
                await server_info["instance"].list_tools()
                return server_info["instance"]
            except (asyncio.InvalidStateError, RuntimeError) as e:
                # Connection might be closed or there's an event loop issue
                logging.warning(f"Server '{server_name}' instance exists but has issues: {e}. Creating new instance.")
                try:
                    # Try to clean up the existing instance
                    await server_info["instance"].cleanup()
                except Exception as cleanup_error:
                    logging.warning(f"Error cleaning up old server instance: {cleanup_error}")

                # Remove existing instance reference, prepare to create a new one
                server_info["instance"] = None
            except Exception as e:
                logging.warning(f"Error checking server '{server_name}' availability: {e}")
                # Continue using the existing instance, assuming it's still valid
                return server_info["instance"]

        server_type = server_info.get("type", "sse")

        try:
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
                    "encoding_error_handler": server_info["encoding_error_handler"],
                }

                server = MCPServerStdio(
                    server_params, cache_tools_list=True, name=server_name
                )
            else:
                raise ValueError(f"Unsupported MCP server type: {server_type}")

            # Try connecting and handle various exceptions
            max_retries = 2  # Maximum retry count for connection failures
            for retry in range(max_retries + 1):
                try:
                    await server.connect()
                    break  # Connection successful, exit retry loop
                except asyncio.CancelledError:
                    # When the task is cancelled, ensure resources are cleaned up
                    logging.warning(f"Connection to server '{server_name}' was cancelled")
                    await server.cleanup()
                    raise
                except asyncio.InvalidStateError as e:
                    if retry < max_retries:
                        logging.warning(f"Event loop issue during connection to '{server_name}'. Retrying... ({retry+1}/{max_retries})")
                        # Try resetting the event loop
                        try:
                            # Create a new event loop
                            loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(loop)
                            logging.info(f"Created new event loop for retry {retry+1}")
                        except Exception as loop_error:
                            logging.error(f"Error recreating event loop: {loop_error}")

                        # Clean up resources from the previous attempt
                        try:
                            await server.cleanup()
                        except Exception as cleanup_error:
                            logging.warning(f"Error cleaning up during retry: {cleanup_error}")

                        # For stdio servers, recreate the instance
                        if server_type == "stdio":
                            server = MCPServerStdio(
                                server_params, cache_tools_list=True, name=server_name
                            )
                        # For SSE servers, recreate the instance
                        else:
                            server = MCPServerSse(
                                server_params, cache_tools_list=True, name=server_name
                            )
                    else:
                        # Maximum retries reached
                        logging.error(f"Failed to connect to MCP server '{server_name}' after {max_retries} retries: {e}")
                        raise
                except Exception as e:
                    if retry < max_retries:
                        logging.warning(f"Error connecting to '{server_name}'. Retrying... ({retry+1}/{max_retries}): {e}")
                        # Clean up resources from the previous attempt
                        try:
                            await server.cleanup()
                        except Exception as cleanup_error:
                            logging.warning(f"Error cleaning up during retry: {cleanup_error}")

                        # Brief delay before retrying
                        await asyncio.sleep(0.5)
                    else:
                        # Maximum retries reached
                        logging.error(f"Failed to connect to MCP server '{server_name}' after {max_retries} retries: {e}")
                        raise

            server_info["instance"] = server
            return server

        except asyncio.CancelledError:
            # Pass the cancellation exception for upper-level handling
            raise
        except Exception as e:
            logger.error(f"Failed to connect to MCP server '{server_name}': {e}")
            raise

    async def async_execute_action(
        self, actions: List[ActionModel], **kwargs
    ) -> Tuple[List[ActionResult], Any]:
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

        # Check if the event loop is closed, recreate if necessary
        loop = asyncio_loop()
        if loop is None:
            # Create a new event loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            logging.info("Created a new event loop for MCP execution")

        results = []
        for action in actions:
            # Get server and operation information
            server_name = action.tool_name
            if not server_name:
                raise ValueError("Missing tool_name in action model")

            action_name = action.action_name
            if not action_name:
                raise ValueError("Missing action_name in action model")

            params = action.params or {}

            try:
                # Get or create MCP server
                server = await self._get_or_create_server(server_name)

                # Call the tool and process results
                try:
                    result = await server.call_tool(action_name, params)

                    if result and result.content:
                        if isinstance(result.content[0], TextContent):
                            action_result = ActionResult(
                                content=result.content[0].text,
                                keep=True
                            )
                            results.append(action_result)
                except asyncio.CancelledError:
                    # Log cancellation exception, reset server connection to avoid async context confusion
                    logger.warning(f"Tool call to {action_name} on {server_name} was cancelled")
                    if server_name in self.mcp_servers and self.mcp_servers[server_name].get("instance"):
                        try:
                            await self.mcp_servers[server_name]["instance"].cleanup()
                            self.mcp_servers[server_name]["instance"] = None
                        except Exception as cleanup_error:
                            logger.error(f"Error cleaning up server after cancellation: {cleanup_error}")
                    # Re-raise exception to notify upper level caller
                    raise
                except asyncio.InvalidStateError as e:
                    # Handle invalid event loop state error
                    logging.error(f"Invalid event loop state: {e}")
                    # Try to reset and reconnect
                    if server_name in self.mcp_servers:
                        try:
                            if self.mcp_servers[server_name].get("instance"):
                                await self.mcp_servers[server_name]["instance"].cleanup()
                            self.mcp_servers[server_name]["instance"] = None
                            # Recreate server connection
                            server = await self._get_or_create_server(server_name)
                            # Retry the call
                            result = await server.call_tool(action_name, params)
                            if result and result.content:
                                if isinstance(result.content[0], TextContent):
                                    action_result = ActionResult(
                                        content=result.content[0].text, keep=True
                                    )
                                    results.append(action_result)
                        except Exception as retry_error:
                            error_msg = f"Error retrying MCP action after loop reset: {retry_error}"
                            logging.error(error_msg)
                            action_result = ActionResult(
                                content=f"Error executing tool: {error_msg}", keep=True
                            )
                            results.append(action_result)
            except asyncio.CancelledError:
                # Pass cancellation exception
                logger.warning("Async execution was cancelled")
                raise
            except Exception as e:
                # Handle general errors
                error_msg = str(e)
                logger.error(f"Error executing MCP action: {error_msg}")
                action_result = ActionResult(
                    content=f"Error executing tool: {error_msg}", keep=True
                )
                results.append(action_result)

        return results, None

    async def cleanup(self) -> None:
        """Clean up all MCP server connections."""
        cleanup_errors = []

        # Ensure there is a running event loop
        loop = asyncio_loop()
        if loop is None:
            logging.warning("Event loop is closed during cleanup, creating new one")
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        for server_name, server_info in self.mcp_servers.items():
            if server_info.get("instance"):
                try:
                    await server_info["instance"].cleanup()
                    server_info["instance"] = None
                except asyncio.CancelledError:
                    logging.warning(f"Cleanup for {server_name} was cancelled")
                    cleanup_errors.append(f"Cleanup for {server_name} was cancelled")
                except Exception as e:
                    error_msg = f"Error cleaning up MCP server {server_name}: {e}"
                    logging.error(error_msg)
                    cleanup_errors.append(error_msg)

        if cleanup_errors:
            logging.error(f"Cleanup completed with {len(cleanup_errors)} errors")
        else:
            logging.info("All MCP servers cleaned up successfully")

    def execute_action(
        self, actions: List[ActionModel], **kwargs
    ) -> Tuple[List[ActionResult], Any]:
        return sync_exec(self.async_execute_action, actions, **kwargs)
