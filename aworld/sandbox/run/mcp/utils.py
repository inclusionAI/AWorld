import json
import logging
from typing import List, Dict, Any
from contextlib import AsyncExitStack

import requests
from mcp.types import TextContent, ImageContent, CallToolResult
from mcp import Tool as MCPTool

from aworld.core.common import ActionResult
from aworld.sandbox.run.mcp.server import MCPServerSse, MCPServerStdio, MCPServer

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
                            item_type = param_info.get("items", {}).get("type", "string")
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
                        if param_info.get("required", False):
                            required.append(param_name)

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
            logging.debug(f"✅ server #{i + 1} ({server.name}) connected success，tools: {len(tools)}")

        except Exception as e:
            logging.error(f"❌ server #{i + 1} ({server.name}) connect fail: {e}")
            return []

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


async def transform_mcp_tools(server_name: str, tools: list[MCPTool]) -> List[str]:
    server_schema = {"server": server_name, "description": "", "tools": []}

    tool_list = []
    for tool in tools:
        properties = {}
        if tool.inputSchema and tool.inputSchema.get("properties"):
            required = tool.inputSchema.get("required", [])
            _properties = tool.inputSchema["properties"]
            for param_name, param_info in _properties.items():
                param_type = (
                    param_info.get("type")
                    if param_info.get("type") != "str"
                    and param_info.get("type") is not None
                    else "string"
                )
                param_desc = param_info.get("description", "")
                if param_type == "array":
                    # Handle array type parameters
                    item_type = param_info.get("items", {}).get("type", "string")
                    if item_type == "str":
                        item_type = "string"
                    properties[param_name] = {
                        "description": param_desc,
                        "type": param_type,
                        "items": {"type": item_type},
                        "required": param_name in required,
                    }
                else:
                    # Handle non-array type parameters
                    properties[param_name] = {
                        "description": param_desc,
                        "type": param_type,
                        "required": param_name in required,
                    }
        tool_item = {
            # "name": f'{server_name}__{tool.name}',
            "name": f"{tool.name}",
            "description": tool.description,
            "annotations": {},
            "InputSchema": {"type": "object", "properties": properties},
        }

        tool_list.append(tool_item)
    server_schema["tools"] = tool_list

    return server_schema


async def transform_openai(server_name: str, tools: list[MCPTool]) -> List[str]:
    openai_tools = []
    for tool in tools:
        required = []
        properties = {}
        if tool.inputSchema and tool.inputSchema.get("properties"):
            required = tool.inputSchema.get("required", [])
            _properties = tool.inputSchema["properties"]
            for param_name, param_info in _properties.items():
                param_type = (
                    param_info.get("type")
                    if param_info.get("type") != "str"
                    and param_info.get("type") is not None
                    else "string"
                )
                param_desc = param_info.get("description", "")
                if param_type == "array":
                    # Handle array type parameters
                    item_type = param_info.get("items", {}).get("type", "string")
                    if item_type == "str":
                        item_type = "string"
                    properties[param_name] = {
                        "description": param_desc,
                        "type": param_type,
                        "items": {"type": item_type},
                    }
                else:
                    # Handle non-array type parameters
                    properties[param_name] = {
                        "description": param_desc,
                        "type": param_type,
                    }
                if param_info.get("required", False):
                    required.append(param_name)

        openai_function_schema = {
            "name": f"{server_name}__{tool.name}",
            "description": tool.description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        }
        openai_tools.append(
            {
                "type": "function",
                "function": openai_function_schema,
            }
        )
    return openai_tools


async def getServerList(mcp_servers: list[MCPServer]) -> List[str]:
    openai_tools = []
    mcp_tools = []
    for i, server in enumerate(mcp_servers):
        try:
            tools = await server.list_tools()
            tmp_openai_tools = await transform_openai(server.name, tools)
            tmp_mcp_tools = await transform_mcp_tools(server.name, tools)
            openai_tools.append(tmp_openai_tools)
            mcp_tools.append(tmp_mcp_tools)
            print("----------mcp_tools-----------")
            print(mcp_tools)
            logging.debug(
                f"✅ server #{i + 1} ({server.name}) connected success, tools: {len(tools)}"
            )
        except Exception as e:
            logging.error(f"❌ server #{i + 1} ({server.name}) connect fail: {e}")
            # Continue with next server

    return openai_tools


async def call_tool(
        server_name: str,
        tool_name: str,
        parameter: Dict[str, Any] = None,
        mcp_config: Dict[str, Any] = None,
) -> ActionResult:
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
    try:
        async with AsyncExitStack() as stack:
            params = {}
            # todo sandbox
            if "api" == server_config.get("type", ""):
                headers = {
                    "Content-Type": "application/json"
                }
                response = requests.post(
                    url=server_config["url"]+"/" + tool_name,
                    headers=headers,
                    json=parameter
                )
                action_result = ActionResult(
                    content=response.text,
                    keep=True
                )
                return action_result
            elif "sse" == server_config.get("type", ""):
                server = MCPServerSse(
                    name=server_name,
                    params=params
                )
            #elif "stdio" == server_config.get("type", ""):
            else:
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
            server = await stack.enter_async_context(server)

            call_result = await run_to_call(server, tool_name, parameter)
            if call_result and call_result.content:
                if isinstance(call_result.content[0], TextContent):
                    action_result = ActionResult(
                        content=call_result.content[0].text,
                        keep=True
                    )
                elif isinstance(call_result.content[0], ImageContent):
                    action_result = ActionResult(
                        content=f"data:image/jpeg;base64,{call_result.content[0].data}",
                        keep=True
                    )
    except Exception as e:
        logging.warning(f"call_tool ({server_name})({tool_name})({params}) connect fail: {e}")

    return action_result

async def run_to_call(mcp_server: MCPServer,tool_name: str,
        params: Dict[str, Any] = None) -> CallToolResult:
    result = await mcp_server.call_tool(tool_name,params)
    return result

async def get_tool_list(mcp_config: Dict[str, Any] = None) -> Dict[str, Any]:
    """Default implement transform framework standard protocol to openai protocol of tool description."""
    try:
        if not mcp_config:
            return {}
        config = mcp_config
        if not isinstance(config, dict):
            return {}
        mcp_servers_config = config.get("mcpServers", {})
        if not mcp_servers_config or not isinstance(mcp_servers_config, dict):
            return {}
        server_configs = []
        for server_name, server_config in mcp_servers_config.items():
            try:
                if "sse" == server_config.get("type", ""):
                    server_configs.append(
                        {
                            # "name": "mcp__" + server_name,
                            "name": server_name,
                            "type": "sse",
                            "params": {"url": server_config["url"]},
                        }
                    )
                # Handle stdio server
                elif "stdio" == server_config.get("type", ""):
                    server_configs.append(
                        {
                            # "name": "mcp__" + server_name,
                            "name": server_name,
                            "type": "stdio",
                            "params": {
                                "command": server_config["command"],
                                "args": server_config.get("args", []),
                                "env": server_config.get("env", {}),
                                "cwd": server_config.get("cwd"),
                                "encoding": server_config.get("encoding", "utf-8"),
                                "encoding_error_handler": server_config.get(
                                    "encoding_error_handler", "strict"
                                ),
                            },
                        }
                    )
            except KeyError as ke:
                logging.error(f"Missing required key for server {server_name}: {ke}")
                continue
        if not server_configs:
            return []

        async with AsyncExitStack() as stack:
            servers = []
            openai_tools = []

            for server_config in server_configs:
                try:
                    if server_config["type"] == "sse":
                        server = MCPServerSse(
                            name=server_config["name"], params=server_config["params"]
                        )
                    elif server_config["type"] == "stdio":
                        server = MCPServerStdio(
                            name=server_config["name"], params=server_config["params"]
                        )
                    else:
                        logging.warning(
                            f"Unsupported MCP server type: {server_config['type']}"
                        )
                        continue

                    server = await stack.enter_async_context(server)
                    servers.append(server)
                except Exception as e:
                    logging.error(
                        f"server #stack.enter_async_context ({server_config}) connect fail: {str(e)}"
                    )
                    continue

            # Use getServerList to get tool name list
            if servers:
                # Get tool names from all servers
                openai_tools = await getServerList(servers)

            return openai_tools

    except Exception as e:
        logging.warning(f"get_available_mcp_tools error: {e}")
        return {}
