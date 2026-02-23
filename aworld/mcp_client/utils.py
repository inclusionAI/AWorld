import asyncio
import json
import os
import threading
import traceback
from contextlib import AsyncExitStack
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple

import requests
from mcp.types import TextContent, ImageContent

from aworld.core.common import ActionResult
from aworld.core.context.base import Context
from aworld.logs.util import logger
from aworld.mcp_client.server import MCPServer, MCPServerSse, MCPServerStdio, MCPServerStreamableHttp
from aworld.tools import get_function_tools

MCP_SERVERS_CONFIG = {}


def get_function_tool(sever_name: str) -> List[Dict[str, Any]]:
    openai_tools = []
    try:
        if not sever_name:
            return []
        tool_server = get_function_tools(sever_name)
        if not tool_server:
            return []
        tools = tool_server.list_tools()
        if not tools:
            return []
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
                                            "properties": nested_items.get(
                                                "properties", {}
                                            ),
                                            "required": nested_items.get(
                                                "required", []
                                            ),
                                        },
                                    },
                                }
                            else:
                                properties[param_name] = {
                                    "description": param_desc,
                                    "type": param_type,
                                    "items": {
                                        "type": item_type,
                                        "items": {"type": nested_type},
                                    },
                                }
                        # Process object type cases
                        elif item_type == "object":
                            properties[param_name] = {
                                "description": param_desc,
                                "type": param_type,
                                "items": {
                                    "type": item_type,
                                    "properties": items_info.get("properties", {}),
                                    "required": items_info.get("required", []),
                                },
                            }
                        # Process basic type cases
                        else:
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

            openai_function_schema = {
                # "name": f"mcp__{sever_name}__{tool.name}",
                "name": f"{sever_name}__{tool.name}",
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
        logger.info(
            f"✅ function_tool_server #({sever_name}) connected success，tools: {len(tools)}"
        )

    except Exception as e:
        logger.warning(
            f"server_name-get_function_tool:{sever_name} translate failed: {e}"
        )
        return []

    return openai_tools


async def run(mcp_servers: list[MCPServer], black_tool_actions: Dict[str, List[str]] = None, tool_actions: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    openai_tools = []
    tool_actions_set = set(tool_actions) if tool_actions else None
    for i, server in enumerate(mcp_servers):
        try:
            tools = await server.list_tools()
            for tool in tools:
                balck_server = server.name
                if server.name.startswith("mcp__"):
                    balck_server = server.name[5:] if len(server.name) > 5 else server.name
                if (black_tool_actions and
                        balck_server in black_tool_actions and
                        black_tool_actions[balck_server] and
                        tool.name in black_tool_actions[balck_server]):
                    logger.info(
                        f"server #{i + 1} ({balck_server}) black_tool_actions: {tool.name}"
                    )
                    continue
                
                # Filter by tool_actions (whitelist)
                if tool_actions_set is not None:
                    if tool.name not in tool_actions_set:
                        continue
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
                                                "properties": nested_items.get(
                                                    "properties", {}
                                                ),
                                                "required": nested_items.get(
                                                    "required", []
                                                ),
                                            },
                                        },
                                    }
                                else:
                                    properties[param_name] = {
                                        "description": param_desc,
                                        "type": param_type,
                                        "items": {
                                            "type": item_type,
                                            "items": {"type": nested_type},
                                        },
                                    }
                            # Process object type cases
                            elif item_type == "object":
                                properties[param_name] = {
                                    "description": param_desc,
                                    "type": param_type,
                                    "items": {
                                        "type": item_type,
                                        "properties": items_info.get("properties", {}),
                                        "required": items_info.get("required", []),
                                    },
                                }
                            # Process basic type cases
                            else:
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

                openai_function_schema = {
                    "name": f"{server.name}__{tool.name}",
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
        except Exception as e:
            logger.warning(
                f"❌ server #{i + 1} ({server.name}) connect fail: {e}\n"
                f"Traceback:\n{traceback.format_exc()}"
            )
            continue

    return openai_tools


async def skill_translate_tools(
        skills: List[str] = None,
        skill_configs: Dict[str, Any] = None,
        tools: List[Dict[str, Any]] = None,
        tool_mapping: Dict[str, str] = {}
) -> List[Dict[str, Any]]:
    if not tools:
        return tools or []

    if not skill_configs:
        return tools

    # If skills is empty, exclude all tools in tool_mapping (only keep non-MCP tools)
    if not skills:
        filtered_tools = []
        for tool in tools:
            if not isinstance(tool, dict) or "function" not in tool:
                filtered_tools.append(tool)  # non-conforming, keep
                continue

            function_info = tool["function"]
            if not isinstance(function_info, dict) or "name" not in function_info:
                filtered_tools.append(tool)
                continue

            tool_name = function_info["name"]

            # Only keep tools that are NOT in tool_mapping
            if not tool_mapping or tool_name not in tool_mapping:
                filtered_tools.append(tool)

        logger.info(f"Skills is empty, excluded {len(tools) - len(filtered_tools)} MCP tools, kept {len(filtered_tools)} non-MCP tools")
        return filtered_tools

    # Collect all tool filters from skill configs
    tool_filter = {}  # {server_name: set(tool_names)} or {server_name: None} means all tools

    for skill_id in skills:
        if skill_id not in skill_configs:
            logger.warning(f"Skill '{skill_id}' not found in skill_configs")
            continue

        skill_config = skill_configs[skill_id]
        tool_list = skill_config.get("tool_list", {})

        for server_name, tool_names in tool_list.items():
            # Normalize tool_names to list (None or [] means all)
            if not tool_names:
                # If any skill requests ALL tools for this server, override to None
                tool_filter[server_name] = None
                continue

            # Merge specific tool names across skills
            if server_name not in tool_filter or tool_filter[server_name] is None:
                # Initialize with empty set if not already set to ALL (None)
                tool_filter[server_name] = set()

            if isinstance(tool_names, list):
                tool_filter[server_name].update(tool_names)
            else:
                # single string safety
                tool_filter[server_name].add(str(tool_names))

    # Selected servers from skills
    selected_servers = set(tool_filter.keys())

    # Build a set of all known MCP servers from mapping (values of mapping)
    known_mcp_servers = set(tool_mapping.values()) if tool_mapping else set()

    # Filter tools based on tool_filter and mapping rules
    filtered_tools = []
    tool_seen = set()  # Track unique tools to avoid duplicates

    for tool in tools:
        if not isinstance(tool, dict) or "function" not in tool:
            filtered_tools.append(tool)  # non-conforming, keep
            continue

        function_info = tool["function"]
        if not isinstance(function_info, dict) or "name" not in function_info:
            filtered_tools.append(tool)
            continue

        tool_name = function_info["name"]

        # Skip duplicates
        if tool_name in tool_seen:
            continue

        # Resolve server and specific tool name (prefer mapping)
        server_name = None
        specific_tool_name = tool_name

        if tool_mapping and specific_tool_name in tool_mapping:
            server_name = tool_mapping[specific_tool_name]

        # If this tool has no resolvable server (non-MCP or custom), keep it
        if not server_name:
            filtered_tools.append(tool)
            tool_seen.add(tool_name)
            continue

        # If tool belongs to a known MCP server but not in selected skills, drop it
        if server_name in known_mcp_servers and server_name not in selected_servers:
            continue

        # If the server is selected, apply per-server tool filtering
        if server_name in tool_filter:
            allowed = tool_filter[server_name]
            if allowed is None:
                # all tools from this server are allowed
                filtered_tools.append(tool)
                tool_seen.add(tool_name)
            else:
                if specific_tool_name in allowed:
                    filtered_tools.append(tool)
                    tool_seen.add(tool_name)
            # else drop
            continue

        # If server is not in selected (and also not in known_mcp_servers), keep as non-target tool
        filtered_tools.append(tool)
        tool_seen.add(tool_name)

    logger.info(f"Filtered {len(filtered_tools)} tools from {len(tools)} based on skills: {skills}")
    return filtered_tools


async def mcp_tool_desc_transform_v2(
        tools: List[str] = None, mcp_config: Dict[str, Any] = None, context: Context = None,
        server_instances: Dict[str, Any] = None,
        black_tool_actions: Dict[str, List[str]] = None,
        sandbox_id: Optional[str] = None,
        tool_actions: Optional[List[str]] = None,
        server_instances_session: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    # todo sandbox mcp_config get from registry

    if not mcp_config:
        return []
    config = mcp_config
    global MCP_SERVERS_CONFIG
    MCP_SERVERS_CONFIG = config
    mcp_servers_config = config.get("mcpServers", {})
    server_configs = []
    openai_tools = []
    mcp_openai_tools = []

    for server_name, server_config in mcp_servers_config.items():
        # Skip disabled servers
        if server_config.get("disabled", False):
            continue

        if tools and server_name in tools:
            # Handle SSE server
            if "function_tool" == server_config.get("type", ""):
                try:
                    tmp_function_tool = get_function_tool(server_name)
                    openai_tools.extend(tmp_function_tool)
                except Exception as e:
                    logger.warning(f"server_name:{server_name} translate failed: {e}")
            elif "api" == server_config.get("type", ""):
                api_result = requests.get(server_config["url"] + "/list_tools")
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
                                # "name": "mcp__" + server_name + "__" + item["name"],
                                "name": server_name + "__" + item["name"],
                                "description": item["description"],
                                "parameters": {
                                    **item["parameters"],
                                    "properties": {
                                        k: v
                                        for k, v in item["parameters"]
                                        .get("properties", {})
                                        .items()
                                        if "default" not in v
                                    },
                                },
                            },
                        }
                        openai_tools.append(tmp_function)
                except Exception as e:
                    logger.warning(f"server_name:{server_name} translate failed: {e}")
            elif "sse" == server_config.get("type", ""):
                server_configs.append(
                    {
                        # "name": "mcp__" + server_name,
                        "name": server_name,
                        "type": "sse",
                        "params": {
                            "url": server_config["url"],
                            "headers": server_config.get("headers"),
                            "timeout": server_config.get("timeout"),
                            "sse_read_timeout": server_config.get("sse_read_timeout"),
                            "client_session_timeout_seconds": server_config.get("client_session_timeout_seconds")
                        },
                    }
                )

            elif "streamable-http" == server_config.get("type", ""):
                server_configs.append(
                    {
                        # "name": "mcp__" + server_name,
                        "name": server_name,
                        "type": "streamable-http",
                        "params": {
                            "url": server_config["url"],
                            "headers": server_config.get("headers"),
                            "timeout": server_config.get("timeout"),
                            "sse_read_timeout": server_config.get("sse_read_timeout"),
                            "client_session_timeout_seconds": server_config.get("client_session_timeout_seconds")
                        },
                    }
                )
            # Handle stdio server
            else:
                # elif "stdio" == server_config.get("type", ""):
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
                            "client_session_timeout_seconds": server_config.get("client_session_timeout_seconds")
                        },
                    }
                )

    if not server_configs:
        return openai_tools
    servers = []
    for server_config in server_configs:
        try:
            _mcp_openai_tools = []
            async with AsyncExitStack() as stack:
                if server_config["type"] == "sse":
                    params = server_config["params"].copy()
                    headers = params.get("headers") or {}
                    env_name = headers.get("env_name")
                    _SESSION_ID = env_name or ""
                    if sandbox_id:
                        _SESSION_ID = _SESSION_ID + "_" + sandbox_id if _SESSION_ID else sandbox_id
                        from aworld.core.context.amni import AmniContext
                        if isinstance(context, AmniContext) and context.get_config().env_config.isolate:
                            if context.task_id:
                              _SESSION_ID = _SESSION_ID + "_" + str(context.task_id)
                        headers["SESSION_ID"] = _SESSION_ID

                    params["headers"] = headers
                    server = MCPServerSse(
                        name=server_config["name"], params=params
                    )
                elif server_config["type"] == "streamable-http":
                    params = server_config["params"].copy()
                    headers = params.get("headers") or {}
                    env_name = headers.get("env_name")
                    _SESSION_ID = env_name or ""
                    if sandbox_id:
                        _SESSION_ID = _SESSION_ID + "_" + sandbox_id if _SESSION_ID else sandbox_id
                        from aworld.core.context.amni import AmniContext
                        if isinstance(context, AmniContext) and context.get_config().env_config.isolate:
                            if context.task_id:
                                _SESSION_ID = _SESSION_ID + "_" + str(context.task_id)
                        headers["SESSION_ID"] = _SESSION_ID

                    params["headers"] = headers
                    if "timeout" in params and not isinstance(params["timeout"], timedelta):
                        params["timeout"] = timedelta(seconds=float(params["timeout"]))
                    if "sse_read_timeout" in params and not isinstance(params["sse_read_timeout"], timedelta):
                        params["sse_read_timeout"] = timedelta(seconds=float(params["sse_read_timeout"]))
                    server = MCPServerStreamableHttp(
                        name=server_config["name"], params=params
                    )
                elif server_config["type"] == "stdio":
                    server = MCPServerStdio(
                        name=server_config["name"], params=server_config["params"]
                    )
                else:
                    logger.warning(
                        f"Unsupported MCP server type: {server_config['type']}"
                    )
                    continue

                server = await stack.enter_async_context(server)
                # servers.append(server)
                _mcp_openai_tools = await run(
                    mcp_servers=[server],
                    black_tool_actions=black_tool_actions,
                    tool_actions=tool_actions
                )
            if _mcp_openai_tools:
                mcp_openai_tools.extend(_mcp_openai_tools)
        except BaseException as err:
            # single
            logger.warning(
                f"Failed to get tools for MCP server '{server_config['name']}'.\n"
                f"Error: {err}\n"
                f"Traceback:\n{traceback.format_exc()}"
            )
            continue

    # async with AsyncExitStack() as stack:

    # mcp_openai_tools = await run(servers)

    if mcp_openai_tools:
        openai_tools.extend(mcp_openai_tools)

    return openai_tools


async def mcp_tool_desc_transform_v2_reuse(
        tools: List[str] = None, mcp_config: Dict[str, Any] = None, context: Context = None,
        server_instances: Dict[str, Any] = None,
        black_tool_actions: Dict[str, List[str]] = None,
        sandbox_id: Optional[str] = None,
        tool_actions: Optional[List[str]] = None,
        server_instances_session: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    # todo sandbox mcp_config get from registry

    if not mcp_config:
        return []
    config = mcp_config
    global MCP_SERVERS_CONFIG
    MCP_SERVERS_CONFIG = config
    mcp_servers_config = config.get("mcpServers", {})
    server_configs = []
    openai_tools = []
    mcp_openai_tools = []

    for server_name, server_config in mcp_servers_config.items():
        # Skip disabled servers
        if server_config.get("disabled", False):
            continue

        if tools and server_name in tools:
            # Handle SSE server
            if "function_tool" == server_config.get("type", ""):
                try:
                    tmp_function_tool = get_function_tool(server_name)
                    openai_tools.extend(tmp_function_tool)
                except Exception as e:
                    logger.warning(f"server_name:{server_name} translate failed: {e}")
            elif "api" == server_config.get("type", ""):
                api_result = requests.get(server_config["url"] + "/list_tools")
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
                                # "name": "mcp__" + server_name + "__" + item["name"],
                                "name": server_name + "__" + item["name"],
                                "description": item["description"],
                                "parameters": {
                                    **item["parameters"],
                                    "properties": {
                                        k: v
                                        for k, v in item["parameters"]
                                        .get("properties", {})
                                        .items()
                                        if "default" not in v
                                    },
                                },
                            },
                        }
                        openai_tools.append(tmp_function)
                except Exception as e:
                    logger.warning(f"server_name:{server_name} translate failed: {e}")
            elif "sse" == server_config.get("type", ""):
                server_configs.append(
                    {
                        # "name": "mcp__" + server_name,
                        "name": server_name,
                        "type": "sse",
                        "params": {
                            "url": server_config["url"],
                            "headers": server_config.get("headers"),
                            "timeout": server_config.get("timeout"),
                            "sse_read_timeout": server_config.get("sse_read_timeout"),
                            "client_session_timeout_seconds": server_config.get("client_session_timeout_seconds")
                        },
                    }
                )

            elif "streamable-http" == server_config.get("type", ""):
                server_configs.append(
                    {
                        # "name": "mcp__" + server_name,
                        "name": server_name,
                        "type": "streamable-http",
                        "params": {
                            "url": server_config["url"],
                            "headers": server_config.get("headers"),
                            "timeout": server_config.get("timeout"),
                            "sse_read_timeout": server_config.get("sse_read_timeout"),
                            "client_session_timeout_seconds": server_config.get("client_session_timeout_seconds")
                        },
                    }
                )
            # Handle stdio server
            else:
                # elif "stdio" == server_config.get("type", ""):
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
                            "client_session_timeout_seconds": server_config.get("client_session_timeout_seconds")
                        },
                    }
                )

    if not server_configs:
        return openai_tools

    # Reuse mode: use cached server instances
    for server_config in server_configs:
        try:
            server = None
            env_session_id = None
            server_name = server_config["name"]
            # Check if server instance exists in cache
            if server_instances and server_name in server_instances:
                server = server_instances.get(server_name)
                env_session_id = server_instances_session.get(server_name)

            if not server:
                # Create new instance if not in cache
                server, env_session_id = await get_server_instance(
                    server_name=server_name,
                    mcp_config=mcp_config,
                    context=context,
                    sandbox_id=sandbox_id
                )
                if server and server_instances is not None:
                    server_instances[server_name] = server
                if env_session_id:
                    server_instances_session[server_name] = env_session_id

            if not server:
                logger.warning(f"Failed to get server instance for {server_name}")
                continue

            # Get tools from server
            _mcp_openai_tools = await run(
                mcp_servers=[server],
                black_tool_actions=black_tool_actions,
                tool_actions=tool_actions
            )
            if _mcp_openai_tools:
                mcp_openai_tools.extend(_mcp_openai_tools)
        except BaseException as err:
            logger.warning(
                f"❌ server ({server_name}) connect fail: {err}\n"
                f"Traceback:\n{traceback.format_exc()}"
            )
            continue

    if mcp_openai_tools:
        openai_tools.extend(mcp_openai_tools)

    return openai_tools


async def process_mcp_tools(
        mcp_tools: Optional[List[Dict[str, Any]]] = None
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    if mcp_tools is None:
        return [], {}

    tool_mapping: Dict[str, str] = {}
    processed_tools: List[Dict[str, Any]] = []
    seen_simple_names: set[str] = set()

    for tool in mcp_tools:
        processed_tool = tool.copy()
        processed_tool["function"] = tool["function"].copy()

        original_name = processed_tool["function"]["name"]
        if "__" in original_name:
            server_name, simple_name = original_name.split("__", 1)
            # only change: skip if we've already seen this simple name
            if simple_name in seen_simple_names:
                continue
            seen_simple_names.add(simple_name)

            processed_tool["function"]["name"] = simple_name
            # keep first mapping only
            if simple_name not in tool_mapping:
                tool_mapping[simple_name] = server_name

        processed_tools.append(processed_tool)

    return processed_tools, tool_mapping


async def mcp_tool_desc_transform(
        tools: List[str] = None, mcp_config: Dict[str, Any] = None
) -> List[Dict[str, Any]]:
    # todo sandbox mcp_config get from registry

    if not mcp_config:
        return []
    config = mcp_config
    global MCP_SERVERS_CONFIG
    MCP_SERVERS_CONFIG = config
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
            if "function_tool" == server_config.get("type", ""):
                try:
                    tmp_function_tool = get_function_tool(server_name)
                    openai_tools.extend(tmp_function_tool)
                except Exception as e:
                    logger.warning(f"server_name:{server_name} translate failed: {e}")
            elif "api" == server_config.get("type", ""):
                api_result = requests.get(server_config["url"] + "/list_tools")
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
                                # "name": "mcp__" + server_name + "__" + item["name"],
                                "name": server_name + "__" + item["name"],
                                "description": item["description"],
                                "parameters": {
                                    **item["parameters"],
                                    "properties": {
                                        k: v
                                        for k, v in item["parameters"]
                                        .get("properties", {})
                                        .items()
                                        if "default" not in v
                                    },
                                },
                            },
                        }
                        openai_tools.append(tmp_function)
                except Exception as e:
                    logger.warning(f"server_name:{server_name} translate failed: {e}")
            elif "sse" == server_config.get("type", ""):
                server_configs.append(
                    {
                        # "name": "mcp__" + server_name,
                        "name": server_name,
                        "type": "sse",
                        "params": {
                            "url": server_config["url"],
                            "headers": server_config.get("headers"),
                            "timeout": server_config.get("timeout"),
                            "sse_read_timeout": server_config.get("sse_read_timeout"),
                            "client_session_timeout_seconds": server_config.get("client_session_timeout_seconds")
                        },
                    }
                )

            elif "streamable-http" == server_config.get("type", ""):
                server_configs.append(
                    {
                        # "name": "mcp__" + server_name,
                        "name": server_name,
                        "type": "streamable-http",
                        "params": {
                            "url": server_config["url"],
                            "headers": server_config.get("headers"),
                            "timeout": server_config.get("timeout"),
                            "sse_read_timeout": server_config.get("sse_read_timeout"),
                            "client_session_timeout_seconds": server_config.get("client_session_timeout_seconds")
                        },
                    }
                )
            # Handle stdio server
            else:
                # elif "stdio" == server_config.get("type", ""):
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
                            "client_session_timeout_seconds": server_config.get("client_session_timeout_seconds")
                        },
                    }
                )

    if not server_configs:
        return openai_tools

    async with AsyncExitStack() as stack:
        servers = []
        for server_config in server_configs:
            try:
                if server_config["type"] == "sse":
                    server = MCPServerSse(
                        name=server_config["name"], params=server_config["params"]
                    )
                elif server_config["type"] == "streamable-http":
                    params = server_config["params"].copy()
                    if "timeout" in params and not isinstance(params["timeout"], timedelta):
                        params["timeout"] = timedelta(seconds=float(params["timeout"]))
                    if "sse_read_timeout" in params and not isinstance(params["sse_read_timeout"], timedelta):
                        params["sse_read_timeout"] = timedelta(seconds=float(params["sse_read_timeout"]))
                    server = MCPServerStreamableHttp(
                        name=server_config["name"], params=params
                    )
                elif server_config["type"] == "stdio":
                    server = MCPServerStdio(
                        name=server_config["name"], params=server_config["params"]
                    )
                else:
                    logger.warning(
                        f"Unsupported MCP server type: {server_config['type']}"
                    )
                    continue

                server = await stack.enter_async_context(server)
                servers.append(server)
            except BaseException as err:
                # single
                logger.error(
                    f"Failed to get tools for MCP server '{server_config['name']}'.\n"
                    f"Error: {err}\n"
                    f"Traceback:\n{traceback.format_exc()}"
                )

        mcp_openai_tools = await run(servers)

    if mcp_openai_tools:
        openai_tools.extend(mcp_openai_tools)

    return openai_tools


async def call_function_tool(
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
        tool_name=server_name, action_name=tool_name, content="", keep=True
    )
    try:
        tool_server = get_function_tools(server_name)
        if not tool_server:
            return action_result
        call_result_raw = tool_server.call_tool(tool_name, parameter)
        if call_result_raw and call_result_raw.content:
            if isinstance(call_result_raw.content[0], TextContent):
                action_result = ActionResult(
                    tool_name=server_name,
                    action_name=tool_name,
                    content=call_result_raw.content[0].text,
                    keep=True,
                    metadata=call_result_raw.content[0].model_extra.get("metadata", {}),
                )
            elif isinstance(call_result_raw.content[0], ImageContent):
                action_result = ActionResult(
                    tool_name=server_name,
                    action_name=tool_name,
                    content=f"data:image/jpeg;base64,{call_result_raw.content[0].data}",
                    keep=True,
                    metadata=call_result_raw.content[0].model_extra.get("metadata", {}),
                )

    except Exception as e:
        logger.warning(f"call_function_tool ({server_name})({tool_name}) failed: {e}")
        action_result = ActionResult(
            tool_name=server_name, action_name=tool_name, content="", keep=True
        )

    return action_result


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
        tool_name=server_name, action_name=tool_name, content="", keep=True
    )

    if not mcp_config or mcp_config.get("mcpServers") is None:
        return action_result

    mcp_servers = mcp_config.get("mcpServers")
    if not mcp_servers.get(server_name):
        return action_result

    server_config = mcp_servers.get(server_name)
    if "api" != server_config.get("type", ""):
        logger.warning(
            f"Server {server_name} is not API type, should use call_tool instead"
        )
        return action_result

    try:
        headers = {"Content-Type": "application/json"}
        response = requests.post(
            url=server_config["url"] + "/" + tool_name, headers=headers, json=parameter
        )
        action_result = ActionResult(
            tool_name=server_name,
            action_name=tool_name,
            content=response.text,
            keep=True,
        )
    except Exception as e:
        logger.warning(f"call_api ({server_name})({tool_name}) failed: {e}")
        action_result = ActionResult(
            tool_name=server_name,
            action_name=tool_name,
            content=f"Error calling API: {str(e)}",
            keep=True,
        )

    return action_result


async def get_server_instance(
        server_name: str, mcp_config: Dict[str, Any] = None,
        context: Context = None,
        sandbox_id: Optional[str] = None
) -> Tuple[Any, Optional[str]]:
    """Get server instance, create a new one if it doesn't exist

    Args:
        server_name: Server name
        mcp_config: MCP configuration
        context: Context object
        sandbox_id: Sandbox ID

    Returns:
        Tuple of (Server instance or None, _SESSION_ID or None)
    """
    if not mcp_config or mcp_config.get("mcpServers") is None:
        return None, None

    mcp_servers = mcp_config.get("mcpServers")
    if not mcp_servers.get(server_name):
        return None, None

    server_config = mcp_servers.get(server_name)
    try:
        # API type servers use special handling, no need for persistent connections
        # Note: We've already handled API type in McpServers.call_tool method
        # Here we don't return None, but let the caller handle it
        if "api" == server_config.get("type", ""):
            logger.info(f"API server {server_name} doesn't need persistent connection")
            return None, None
        elif "sse" == server_config.get("type", ""):
            headers = server_config.get("headers") or {}
            env_name = headers.get("env_name")
            _SESSION_ID = env_name or ""
            if sandbox_id:
                _SESSION_ID = _SESSION_ID + "_" + sandbox_id if _SESSION_ID else sandbox_id
                from aworld.core.context.amni import AmniContext
                if isinstance(context, AmniContext) and context.get_config().env_config.isolate:
                    if context.task_id:
                        _SESSION_ID = _SESSION_ID + "_" + str(context.task_id)
                headers["SESSION_ID"] = _SESSION_ID
            server = MCPServerSse(
                name=server_name,
                params={
                    "url": server_config["url"],
                    "headers": headers,
                    "timeout": server_config.get("timeout", 5.0),
                    "sse_read_timeout": server_config.get("sse_read_timeout", 300.0),
                    "client_session_timeout_seconds": server_config.get("client_session_timeout_seconds", 300.0),
                },
            )
            await server.connect()
            logger.info(f"Successfully connected to SSE server: {server_name}")
            return server, _SESSION_ID
        elif "streamable-http" == server_config.get("type", ""):
            headers = server_config.get("headers") or {}
            env_name = headers.get("env_name")
            _SESSION_ID = env_name or ""
            if sandbox_id:
                _SESSION_ID = _SESSION_ID + "_" + sandbox_id if _SESSION_ID else sandbox_id
                from aworld.core.context.amni import AmniContext
                if isinstance(context, AmniContext) and context.get_config().env_config.isolate:
                    if context.task_id:
                        _SESSION_ID = _SESSION_ID + "_" + str(context.task_id)
                headers["SESSION_ID"] = _SESSION_ID
            server = MCPServerStreamableHttp(
                name=server_name,
                params={
                    "url": server_config["url"],
                    "headers": headers,
                    "timeout": timedelta(seconds=server_config.get("timeout", 120.0)),
                    "sse_read_timeout": timedelta(seconds=server_config.get("sse_read_timeout", 300.0)),
                },
            )
            await server.connect()
            logger.info(
                f"[sandbox list_tools] server={server_name} pid={os.getpid()} tid={threading.get_ident()} at={datetime.utcnow().strftime('%H:%M:%S.%f')[:-3]}"
            )
            return server, _SESSION_ID
        else:  # stdio type
            params = {
                "command": server_config["command"],
                "args": server_config.get("args", []),
                "env": server_config.get("env", {}),
                "cwd": server_config.get("cwd"),
                "encoding": server_config.get("encoding", "utf-8"),
                "encoding_error_handler": server_config.get(
                    "encoding_error_handler", "strict"
                ),
                "client_session_timeout_seconds": server_config.get("client_session_timeout_seconds", 300.0),
            }
            server = MCPServerStdio(name=server_name, params=params)
            await server.connect()
            logger.info(f"Successfully connected to stdio server: {server_name}")
            return server, None
    except Exception as e:
        logger.warning(f"Failed to create server instance for {server_name}: {e}")
        return None, None


async def cleanup_server(server):
    """Clean up server connection

    Args:
        server: Server instance
    """
    name = getattr(server, "name", "unknown")
    try:
        if hasattr(server, "cleanup"):
            await server.cleanup()
        elif hasattr(server, "close"):
            await server.close()
    except RuntimeError as e:
        # RuntimeError about cancel scope usually means cleanup is being called
        # from a different task context. Log it but don't fail.
        if "cancel scope" in str(e).lower() or "different task" in str(e).lower():
            logger.warning(
                f"Cleanup called from different task context for server "
                f"{getattr(server, 'name', 'unknown')}: {e}. "
                f"This may indicate a resource leak."
            )
        else:
            logger.warning(f"Failed to cleanup server: {e}")
    except Exception as e:
        logger.warning(f"Failed to cleanup server: {e}")


async def call_mcp_tool_with_exit_stack(
    server_name: str,
    tool_name: str,
    parameter: Dict[str, Any],
    mcp_config: Dict[str, Any],
    context: Context = None,
    sandbox_id: Optional[str] = None,
    progress_callback=None,
    max_retry: int = 3,
    timeout: float = 120.0
) -> Any:
    """Call MCP tool using AsyncExitStack to manage connection lifecycle.

    This method creates a new server connection for each call and automatically
    cleans it up after use, similar to how list_tools works.

    Args:
        server_name: Name of the MCP server
        tool_name: Name of the tool to call
        parameter: Tool parameters
        mcp_config: MCP configuration
        context: Context object (optional)
        sandbox_id: Sandbox ID (optional)
        progress_callback: Optional progress callback function
        max_retry: Maximum number of retry attempts (default: 3)
        timeout: Timeout in seconds (default: 120.0)

    Returns:
        CallToolResult or None if all attempts fail
    """
    call_result_raw = None
    last_exception = None

    for attempt in range(max_retry):
        try:
            # Create a new server instance for each call using AsyncExitStack
            async with AsyncExitStack() as stack:
                server, env_session_id = await get_server_instance(
                    server_name=server_name,
                    mcp_config=mcp_config,
                    context=context,
                    sandbox_id=sandbox_id
                )

                if not server:
                    logger.warning(
                        f"Failed to create server instance: {server_name}, "
                        f"tool_name: {tool_name}, attempt: {attempt + 1}"
                    )
                    if attempt == max_retry - 1:
                        return None
                    continue

                # Register cleanup callback since server is already connected
                # get_server_instance already called connect(), so we just need cleanup
                async def cleanup_server_on_exit(exc_type, exc_val, exc_tb):
                    await server.cleanup()
                stack.push_async_exit(cleanup_server_on_exit)

                logger.info(
                    f"Created new server instance for {server_name} "
                    f"(attempt {attempt + 1}/{max_retry})"
                )

                # Call the tool with timeout
                call_result_raw = await asyncio.wait_for(
                    server.call_tool(
                        tool_name=tool_name,
                        arguments=parameter,
                        progress_callback=progress_callback
                    ),
                    timeout=timeout
                )

                # Success, break out of retry loop
                logger.info(
                    f"Successfully called tool {server_name}__{tool_name} "
                    f"(attempt {attempt + 1})"
                )
                break

        except asyncio.TimeoutError as e:
            last_exception = e
            logger.warning(
                f"Timeout calling tool {server_name}__{tool_name} "
                f"(attempt {attempt + 1}/{max_retry}): {e}"
            )
            if attempt == max_retry - 1:
                logger.error(
                    f"All {max_retry} attempts failed for {server_name}__{tool_name} "
                    f"due to timeout"
                )
        except BaseException as e:
            last_exception = e
            logger.warning(
                f"Error calling tool {server_name}__{tool_name} "
                f"(attempt {attempt + 1}/{max_retry}): {e}.\n"
                f"Traceback:\n{traceback.format_exc()}"
            )
            if attempt == max_retry - 1:
                logger.error(
                    f"All {max_retry} attempts failed for {server_name}__{tool_name}"
                )

    return call_result_raw


async def call_mcp_tool_with_reuse(
    server_name: str,
    tool_name: str,
    parameter: Dict[str, Any],
    server_instances: Dict[str, MCPServer],
    mcp_config: Dict[str, Any],
    context: Context = None,
    sandbox_id: Optional[str] = None,
    progress_callback=None,
    max_retry: int = 3,
    timeout: float = 120.0
) -> Any:
    """Call MCP tool using cached server instances (reuse mode).

    This method uses cached server instances and includes retry logic similar to
    call_mcp_tool_with_exit_stack, but without creating new connections each time.

    Args:
        server_name: Name of the MCP server
        tool_name: Name of the tool to call
        parameter: Tool parameters
        server_instances: Dictionary to cache server instances {server_name: server_instance}
        mcp_config: MCP configuration
        context: Context object (optional)
        sandbox_id: Sandbox ID (optional)
        progress_callback: Optional progress callback function
        max_retry: Maximum number of retry attempts (default: 3)
        timeout: Timeout in seconds (default: 120.0)

    Returns:
        CallToolResult or None if all attempts fail
    """
    # Get or create server instance before retry loop
    server = server_instances.get(server_name)
    if not server:
        server, env_session_id = await get_server_instance(
            server_name=server_name,
            mcp_config=mcp_config,
            context=context,
            sandbox_id=sandbox_id
        )
        if server:
            server_instances[server_name] = server

    if not server:
        logger.warning(f"Failed to get server instance: {server_name}, tool_name: {tool_name}")
        return None

    call_result_raw = None

    for attempt in range(max_retry):
        try:
            # Call the tool with timeout
            call_result_raw = await asyncio.wait_for(
                server.call_tool(
                    tool_name=tool_name,
                    arguments=parameter,
                    progress_callback=progress_callback
                ),
                timeout=timeout
            )
            # Success, break out of retry loop
            break

        except (asyncio.TimeoutError, BaseException) as e:
            logger.warning(
                f"Error calling tool {server_name}__{tool_name} "
                f"(attempt {attempt + 1}/{max_retry}): {e}"
            )

    return call_result_raw

# Helper: extract mcp_servers from mcp_config if current_servers is empty


def extract_mcp_servers_from_config(mcp_config: Dict[str, Any] = None,
                                     current_servers: List[str] = None) -> List[str]:
    """
    Extract MCP server names from mcp_config if current_servers is empty.
    
    If current_servers is not empty, return it as is.
    Otherwise, extract all keys from mcp_config["mcpServers"] as server names.
    
    Args:
        mcp_config: MCP configuration dictionary with structure:
            {
                "mcpServers": {
                    "server_name_1": {...},
                    "server_name_2": {...},
                    ...
                }
            }
        current_servers: Current list of MCP server names. If empty or None,
                        will extract from mcp_config.
    
    Returns:
        List of MCP server names. Returns current_servers if not empty,
        otherwise returns keys from mcp_config["mcpServers"].
    """
    if current_servers is None:
        current_servers = []
    
    # If current_servers is not empty, return it as is
    if current_servers:
        return current_servers
    
    # If mcp_config is empty or None, return empty list
    if not mcp_config:
        return []
    
    server_list = []
    try:
        mcp_servers = mcp_config.get("mcpServers", {})
        if isinstance(mcp_servers, dict):
            for server_name in mcp_servers.keys():
                if server_name:
                    server_list.append(str(server_name))
    except Exception as e:
        logger.warning(f"Failed to extract MCP servers from config: {e}")
        return []
    
    return server_list


# Helper: derive mcp_servers from skill_configs if provided


def replace_mcp_servers_variables(skill_configs: Dict[str, Any] = None,
                                  current_servers: List[str] = None,
                                  default_servers: List[str] = None) -> List[str]:
    """
    If skill_configs is empty/None, return current_servers (or default).
    If present, collect all keys of `tool_list` across skills as server names.
    Fallback to current_servers (or default) when no keys gathered.
    """
    if current_servers is None:
        current_servers = []
    if default_servers is None:
        default_servers = []

    if not skill_configs:
        return current_servers or default_servers

    server_set = set()
    try:
        for _skill_id, cfg in skill_configs.items():
            tool_list = (cfg or {}).get("tool_list", {})
            if isinstance(tool_list, dict):
                for server in tool_list.keys():
                    if server:
                        server_set.add(str(server))
    except Exception:
        # On any unexpected structure, keep original servers
        return current_servers or default_servers

    if not server_set:
        return current_servers or default_servers
    return list(server_set)
