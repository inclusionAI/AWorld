"""MCP client utilities for sandbox to get tools from MCP servers."""
import json
import traceback
from contextlib import AsyncExitStack
from datetime import timedelta
from typing import List, Dict, Any, Optional

from aworld.logs.util import logger
from aworld.sandbox.utils.server import MCPServerStdio, MCPServerStreamableHttp, MCPServerSse


async def get_tools_from_mcp_servers(
    mcp_config: Dict[str, Any],
    server_names: Optional[List[str]] = None
) -> Dict[str, List[Dict[str, str]]]:
    """
    Get tools from MCP servers based on configuration.
    
    Args:
        mcp_config: MCP configuration dict with structure {"mcpServers": {...}}
        server_names: Optional list of server names to filter. If None, processes all servers.
    
    Returns:
        Dict with server_name as key and list of tools as value:
        {
            "server_name": [
                {
                    "name": "server_name__tool_name",
                    "description": "..."
                },
                ...
            ],
            ...
        }
    """
    if not mcp_config:
        return {}
    
    mcp_servers_config = mcp_config.get("mcpServers", {})
    if not mcp_servers_config:
        return {}
    
    result = {}
    
    # Process each server
    for server_name, server_config in mcp_servers_config.items():
        # Skip disabled servers
        if server_config.get("disabled", False):
            continue
        
        # Filter by server_names if provided
        if server_names and server_name not in server_names:
            continue
        
        server_type = server_config.get("type", "stdio")
        
        try:
            # Get tools from this server
            server_tools = await _get_tools_from_server(server_name, server_type, server_config)
            if server_tools:
                result[server_name] = server_tools
        except Exception as e:
            logger.warning(
                f"Failed to get tools from MCP server '{server_name}': {e}\n"
                f"Traceback:\n{traceback.format_exc()}"
            )
            continue
    
    return result


async def _get_tools_from_server(
    server_name: str,
    server_type: str,
    server_config: Dict[str, Any]
) -> List[Dict[str, str]]:
    """
    Get tools from a single MCP server.
    
    Returns:
        List of tools with only name and description:
        [
            {
                "name": "server_name__tool_name",
                "description": "..."
            },
            ...
        ]
    """
    tools = []
    
    try:
        async with AsyncExitStack() as stack:
            # Create server instance based on type
            if server_type == "stdio":
                server = MCPServerStdio(
                    name=server_name,
                    params={
                        "command": server_config["command"],
                        "args": server_config.get("args", []),
                        "env": server_config.get("env", {}),
                        "cwd": server_config.get("cwd"),
                        "encoding": server_config.get("encoding", "utf-8"),
                        "encoding_error_handler": server_config.get("encoding_error_handler", "strict"),
                        "client_session_timeout_seconds": server_config.get("client_session_timeout_seconds")
                    }
                )
            elif server_type == "sse":
                params = {
                    "url": server_config["url"],
                    "headers": server_config.get("headers", {}),
                    "timeout": server_config.get("timeout", 6000),
                    "sse_read_timeout": server_config.get("sse_read_timeout", 6000),
                    "client_session_timeout_seconds": server_config.get("client_session_timeout_seconds", 6000)
                }
                # Convert timeout to timedelta if needed
                if "timeout" in params and not isinstance(params["timeout"], timedelta):
                    params["timeout"] = timedelta(seconds=float(params["timeout"]))
                if "sse_read_timeout" in params and not isinstance(params["sse_read_timeout"], timedelta):
                    params["sse_read_timeout"] = timedelta(seconds=float(params["sse_read_timeout"]))
                
                server = MCPServerSse(
                    name=server_name,
                    params=params
                )
            elif server_type == "streamable-http":
                params = {
                    "url": server_config["url"],
                    "headers": server_config.get("headers", {}),
                    "timeout": server_config.get("timeout", 6000),
                    "sse_read_timeout": server_config.get("sse_read_timeout", 6000),
                    "client_session_timeout_seconds": server_config.get("client_session_timeout_seconds", 6000)
                }
                # Convert timeout to timedelta if needed
                if "timeout" in params and not isinstance(params["timeout"], timedelta):
                    params["timeout"] = timedelta(seconds=float(params["timeout"]))
                if "sse_read_timeout" in params and not isinstance(params["sse_read_timeout"], timedelta):
                    params["sse_read_timeout"] = timedelta(seconds=float(params["sse_read_timeout"]))
                
                server = MCPServerStreamableHttp(
                    name=server_name,
                    params=params
                )
            else:
                logger.warning(f"Unsupported MCP server type: {server_type} for server '{server_name}'")
                return []
            
            # Enter async context and connect
            server = await stack.enter_async_context(server)
            await server.connect()
            
            if not server.session:
                logger.warning(f"Failed to connect to MCP server '{server_name}'")
                return []
            
            # Get tools from server
            mcp_tools = await server.list_tools()
            
            # Extract only name and description
            for tool in mcp_tools:
                tools.append({
                    "name": tool.name,
                    "description": tool.description or ""
                })
            
            logger.info(f"✅ Successfully retrieved {len(tools)} tools from server '{server_name}'")
            
    except Exception as e:
        logger.warning(
            f"Error getting tools from MCP server '{server_name}': {e}\n"
            f"Traceback:\n{traceback.format_exc()}"
        )
        return []
    
    return tools

