import logging
from typing import List, Dict, Any
import json
import os
from contextlib import AsyncExitStack
import traceback

import requests
from mcp.types import CallToolResult

from aworld.core.common import ActionResult

from aworld.logs.util import logger
from aworld.mcp_client.server import MCPServer, MCPServerSse, MCPServerStdio
from aworld.utils.common import find_file

MCP_SERVERS_CONFIG = {}

async def run(mcp_servers: list[MCPServer]) -> List[Dict[str, Any]]:
    openai_tools = []
    for i, server in enumerate(mcp_servers):
        try:
            tools = await server.list_tools()
            for tool in tools:
                required = []
                properties = {}
                if tool.inputSchema and tool.inputSchema.get("properties"):
                    required = tool.inputSchema.get("required", [])
                    _properties = tool.inputSchema["properties"]
                    for param_name, param_info in _properties.items():
                        param_type = param_info.get("type") if param_info.get("type") != "str" and param_info.get("type") is not None else "string"
                        param_desc = param_info.get("description", "")
                        if param_type == "array":
                            # Handle array type parameters
                            items_info = param_info.get("items", {})
                            item_type = items_info.get("type", "string")

                            # Process nested array type parameters
                            if item_type == "array":
                                nested_items = items_info.get("items", {})
                                nested_type = nested_items.get("type", "string")

                                # If the nested type is an object
                                if nested_type == "object":
                                    properties[param_name] = {
                                        "description": param_desc,
                                        "type": param_type,
                                        "items": {
                                            "type": item_type,
                                            "items": {
                                                "type": nested_type,
                                                "properties": nested_items.get("properties", {}),
                                                "required": nested_items.get("required", [])
                                            }
                                        }
                                    }
                                else:
                                    properties[param_name] = {
                                        "description": param_desc,
                                        "type": param_type,
                                        "items": {
                                            "type": item_type,
                                            "items": {
                                                "type": nested_type
                                            }
                                        }
                                    }
                            # Process object type cases
                            elif item_type == "object":
                                properties[param_name] = {
                                    "description": param_desc,
                                    "type": param_type,
                                    "items": {
                                        "type": item_type,
                                        "properties": items_info.get("properties", {}),
                                        "required": items_info.get("required", [])
                                    }
                                }
                            # Process basic type cases
                            else:
                                if item_type == "str":
                                    item_type = "string"
                                properties[param_name] = {
                                    "description": param_desc,
                                    "type": param_type,
                                    "items": {
                                        "type": item_type
                                    }
                                }
                        else:
                            # Handle non-array type parameters
                            properties[param_name] = {
                                "description": param_desc,
                                "type": param_type
                            }

                openai_function_schema = {
                    "name": f'{server.name}__{tool.name}',
                    "description": tool.description,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required
                    }
                }
                openai_tools.append({
                    "type": "function",
                    "function": openai_function_schema,
                })
            logging.info(f"✅ server #{i + 1} ({server.name}) connected success，tools: {len(tools)}")

        except Exception as e:
            logging.error(f"❌ server #{i + 1} ({server.name}) connect fail: {e}")
            return []

    return openai_tools


async def mcp_tool_desc_transform(tools: List[str] = None,mcp_config: Dict[str, Any] = None) -> List[Dict[str, Any]]:
    """Default implement transform framework standard protocol to openai protocol of tool description."""
    config = {}
    global MCP_SERVERS_CONFIG
    def _replace_env_variables(config):
        if isinstance(config, dict):
            for key, value in config.items():
                if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
                    env_var_name = value[2:-1]
                    config[key] = os.getenv(env_var_name, value)
                    logging.info(f"Replaced {value} with {config[key]}")
                elif isinstance(value, dict) or isinstance(value, list):
                    _replace_env_variables(value)
        elif isinstance(config, list):
            for index, item in enumerate(config):
                if isinstance(item, str) and item.startswith("${") and item.endswith("}"):
                    env_var_name = item[2:-1]
                    config[index] = os.getenv(env_var_name, item)
                    logging.info(f"Replaced {item} with {config[index]}")
                elif isinstance(item, dict) or isinstance(item, list):
                    _replace_env_variables(item)

    if mcp_config:
        try:
            config = mcp_config
            MCP_SERVERS_CONFIG = config
        except Exception as e:
            logging.error(f"mcp_config error: {e}")
            return []
    else:
        # Priority given to the running path.
        config_path = find_file(filename='mcp.json')
        if not os.path.exists(config_path):
            current_dir = os.path.dirname(os.path.abspath(__file__))
            config_path = os.path.normpath(os.path.join(current_dir, "../config/mcp.json"))
        logger.info(f"mcp conf path: {config_path}")

        if not os.path.exists(config_path):
            logging.info(f"mcp config is not exist: {config_path}")
            return []

        try:
            with open(config_path, "r") as f:
                config = json.load(f)
        except Exception as e:
            logging.info(f"load config fail: {e}")
            return []
        _replace_env_variables(config)

        MCP_SERVERS_CONFIG = config

    mcp_servers_config = config.get("mcpServers", {})

    server_configs = []
    for server_name, server_config in mcp_servers_config.items():
        # Skip disabled servers
        if server_config.get("disabled", False):
            continue

        if tools is None or server_name in tools:
            # Handle SSE server
            if "url" in server_config:
                server_configs.append({
                    "name": "mcp__" + server_name,
                    "type": "sse",
                    "params": {"url": server_config["url"]}
                })
            # Handle stdio server
            elif "command" in server_config:
                server_configs.append({
                    "name": "mcp__" + server_name,
                    "type": "stdio",
                    "params": {
                        "command": server_config["command"],
                        "args": server_config.get("args", []),
                        "env": server_config.get("env", {}),
                        "cwd": server_config.get("cwd"),
                        "encoding": server_config.get("encoding", "utf-8"),
                        "encoding_error_handler": server_config.get("encoding_error_handler", "strict")
                    }
                })

    if not server_configs:
        return []

    async with AsyncExitStack() as stack:
        servers = []
        for server_config in server_configs:
            try:
                if server_config["type"] == "sse":
                    server = MCPServerSse(
                        name=server_config["name"],
                        params=server_config["params"]
                    )
                elif server_config["type"] == "stdio":
                    from aworld.mcp_client.server import MCPServerStdio
                    server = MCPServerStdio(
                        name=server_config["name"],
                        params=server_config["params"]
                    )
                else:
                    logging.warning(f"Unsupported MCP server type: {server_config['type']}")
                    continue

                server = await stack.enter_async_context(server)
                servers.append(server)
            except BaseException as err:
                # single
                logging.error(
                    f"Failed to get tools for MCP server '{server_config['name']}'.\n"
                    f"Error: {err}\n"
                    f"Traceback:\n{traceback.format_exc()}"
                )

        openai_tools = await run(servers)

    return openai_tools



async def sandbox_mcp_tool_desc_transform(tools: List[str] = None,mcp_config: Dict[str, Any] = None) -> List[Dict[str, Any]]:
    # todo sandbox mcp_config get from registry

    if not mcp_config:
        return None
    config = mcp_config
    mcp_servers_config = config.get("mcpServers", {})
    server_configs = []
    openai_tools = []
    mcp_openai_tools = []

    for server_name, server_config in mcp_servers_config.items():
        # Skip disabled servers
        if server_config.get("disabled", False):
            continue

        if tools is None or server_name in tools:
            # Handle SSE server
            if "api" == server_config.get("type", ""):
                api_result = requests.get(server_config["url"]+"/list_tools")
                try:
                    if not api_result or not api_result.text:
                        continue
                        # return None
                    data = json.loads(api_result.text)
                    if not data or not data.get("tools"):
                        continue
                    for item in data.get("tools"):
                        tmp_function = {
                            "type": "function",
                            "function": {
                                "name": "mcp__" + server_name + "__" + item["name"],
                                "description": item["description"],
                                "parameters": {
                                    **item["parameters"],
                                    "properties": {k: v for k, v in item["parameters"].get("properties", {}).items()
                                                   if
                                                   'default' not in v}
                                }
                            }
                        }
                        openai_tools.append(tmp_function)
                except Exception as e:
                    logging.warning(f"server_name:{server_name} translate failed: {e}")
            elif "sse" == server_config.get("type", ""):
                server_configs.append({
                    "name": "mcp__" + server_name,
                    "type": "sse",
                    "params": {
                        "url": server_config["url"],
                        "headers": server_config.get("headers")
                    }
                })
            # Handle stdio server
            else:
            #elif "stdio" == server_config.get("type", ""):
                server_configs.append({
                    "name": "mcp__" + server_name,
                    "type": "stdio",
                    "params": {
                        "command": server_config["command"],
                        "args": server_config.get("args", []),
                        "env": server_config.get("env", {}),
                        "cwd": server_config.get("cwd"),
                        "encoding": server_config.get("encoding", "utf-8"),
                        "encoding_error_handler": server_config.get("encoding_error_handler", "strict")
                    }
                })

    if not server_configs:
        return openai_tools

    async with AsyncExitStack() as stack:
        servers = []
        for server_config in server_configs:
            try:
                if server_config["type"] == "sse":
                    server = MCPServerSse(
                        name=server_config["name"],
                        params=server_config["params"]
                    )
                elif server_config["type"] == "stdio":
                    server = MCPServerStdio(
                        name=server_config["name"],
                        params=server_config["params"]
                    )
                else:
                    logging.warning(f"Unsupported MCP server type: {server_config['type']}")
                    continue

                server = await stack.enter_async_context(server)
                servers.append(server)
            except BaseException as err:
                # single
                logging.error(
                    f"Failed to get tools for MCP server '{server_config['name']}'.\n"
                    f"Error: {err}\n"
                )

        mcp_openai_tools = await run(servers)

    if mcp_openai_tools:
        openai_tools.extend(mcp_openai_tools)

    return openai_tools


async def call_api(
        server_name: str,
        tool_name: str,
        parameter: Dict[str, Any] = None,
        mcp_config: Dict[str, Any] = None,
) -> ActionResult:
    """Specifically handle API type server calls

    Args:
        server_name: Server name
        tool_name: Tool name
        parameter: Parameters
        mcp_config: MCP configuration

    Returns:
        ActionResult: Call result
    """
    action_result = ActionResult(
        content="",
        keep=True
    )

    if not mcp_config or mcp_config.get("mcpServers") is None:
        return action_result

    mcp_servers = mcp_config.get("mcpServers")
    if not mcp_servers.get(server_name):
        return action_result

    server_config = mcp_servers.get(server_name)
    if "api" != server_config.get("type", ""):
        logging.warning(f"Server {server_name} is not API type, should use call_tool instead")
        return action_result

    try:
        headers = {
            "Content-Type": "application/json"
        }
        response = requests.post(
            url=server_config["url"] + "/" + tool_name,
            headers=headers,
            json=parameter
        )
        action_result = ActionResult(
            content=response.text,
            keep=True
        )
    except Exception as e:
        logging.warning(f"call_api ({server_name})({tool_name}) failed: {e}")
        action_result = ActionResult(
            content=f"Error calling API: {str(e)}",
            keep=True
        )

    return action_result



async def get_server_instance(server_name: str, mcp_config: Dict[str, Any] = None) -> Any:
    """Get server instance, create a new one if it doesn't exist

    Args:
        server_name: Server name
        mcp_config: MCP configuration

    Returns:
        Server instance or None (if creation fails)
    """
    if not mcp_config or mcp_config.get("mcpServers") is None:
        return None

    mcp_servers = mcp_config.get("mcpServers")
    if not mcp_servers.get(server_name):
        return None

    server_config = mcp_servers.get(server_name)
    try:
        # API type servers use special handling, no need for persistent connections
        # Note: We've already handled API type in McpServers.call_tool method
        # Here we don't return None, but let the caller handle it
        if "api" == server_config.get("type", ""):
            logging.info(f"API server {server_name} doesn't need persistent connection")
            return None
        elif "sse" == server_config.get("type", ""):
            server = MCPServerSse(
                name=server_name,
                params={
                    "url": server_config["url"],
                    "headers": server_config.get("headers"),
                    "timeout": server_config.get("timeout", 5.0),
                    "sse_read_timeout": server_config.get("sse_read_timeout", 300.0)
                }
            )
            await server.connect()
            logging.info(f"Successfully connected to SSE server: {server_name}")
            return server
        else:  # stdio type
            params = {
                "command": server_config["command"],
                "args": server_config.get("args", []),
                "env": server_config.get("env", {}),
                "cwd": server_config.get("cwd"),
                "encoding": server_config.get("encoding", "utf-8"),
                "encoding_error_handler": server_config.get("encoding_error_handler", "strict")
            }
            server = MCPServerStdio(
                name=server_name,
                params=params
            )
            await server.connect()
            logging.info(f"Successfully connected to stdio server: {server_name}")
            return server
    except Exception as e:
        logging.warning(f"Failed to create server instance for {server_name}: {e}")
        return None


async def cleanup_server(server):
    """Clean up server connection

    Args:
        server: Server instance
    """
    try:
        if hasattr(server, 'cleanup'):
            await server.cleanup()
        elif hasattr(server, 'close'):
            await server.close()
        logging.info(f"Successfully cleaned up server: {getattr(server, 'name', 'unknown')}")
    except Exception as e:
        logging.warning(f"Failed to cleanup server: {e}")
