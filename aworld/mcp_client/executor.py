import logging
import traceback
import asyncio

from typing import Any, Dict

from aworld.mcp_client.server import MCPServer, MCPServerSse, MCPServerStdio


class MCPToolExecutor:
    """A tool executor that uses MCP server to execute actions."""

    def __init__(self, mcp_config: Dict[str, Any] = None):
        """Initialize the MCP tool executor."""
        self.initialized = False
        self.mcp_servers: Dict[str, MCPServer] = {}
        self._load_mcp_config(mcp_config)

    def _load_mcp_config(self, mcp_config: Dict[str, Any] = None) -> None:
        """Load MCP server configurations from config file."""
        try:
            if not mcp_config:
                return

            config_data = mcp_config

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
            logging.error(f"Failed to load MCP config: {traceback.format_exc()}")

    async def _get_or_create_server(self, server_name: str) -> MCPServer:
        """Get an existing MCP server instance or create a new one."""
        if server_name not in self.mcp_servers:
            raise ValueError(f"MCP server '{server_name}' not found in configuration")

        server_info = self.mcp_servers[server_name]

        # If an instance already exists, check if it's available and reuse it
        if server_info.get("instance"):
            return server_info["instance"]

        server_type = server_info.get("type", "sse")

        try:
            if server_type == "sse":
                # Create new SSE server instance
                server_params = {
                    "url": server_info["url"],
                    "timeout": server_info["timeout"],
                    "sse_read_timeout": server_info["sse_read_timeout"],
                    "headers": server_info["headers"],
                }

                server = MCPServerSse(
                    server_params, cache_tools_list=True, name=server_name
                )
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

            # Try to connect, with special handling for cancellation exceptions
            try:
                await server.connect()
            except asyncio.CancelledError:
                # When the task is cancelled, ensure resources are cleaned up
                logging.warning(f"Connection to server '{server_name}' was cancelled")
                await server.cleanup()
                raise

            server_info["instance"] = server
            return server

        except asyncio.CancelledError:
            # Pass cancellation exceptions up to be handled by the caller
            raise
        except Exception as e:
            logging.error(f"Failed to connect to MCP server '{server_name}': {e}")
            raise

    async def cleanup(self) -> None:
        """Clean up all MCP server connections."""
        for server_name, server_info in self.mcp_servers.items():
            if server_info.get("instance"):
                try:
                    await server_info["instance"].cleanup()
                except Exception as e:
                    logging.error(f"Error cleaning up MCP server {server_name}: {e}")
