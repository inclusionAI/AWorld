# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
import json
import os
import traceback
from typing import List, Dict, Any, Tuple

from transformers import AutoTokenizer


def turns_num(messages: List[Dict[str, Any]]) -> int:
    # Normalize messages to satisfy chat templates expectations
    def _normalize_message(msg: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(msg)
        # content may be None when assistant only returns tool_calls; make it empty string
        if normalized.get("content") is None:
            normalized["content"] = ""
        # Ensure tool_calls.function.arguments is a string (many templates expect str)
        if isinstance(normalized.get("tool_calls"), list):
            fixed_calls = []
            for call in normalized["tool_calls"]:
                call_copy = dict(call)
                func = call_copy.get("function")
                if isinstance(func, dict):
                    func_copy = dict(func)
                    args_val = func_copy.get("arguments")
                    if not isinstance(args_val, (str, bytes)):
                        try:
                            func_copy["arguments"] = json.dumps(args_val, ensure_ascii=False)
                        except Exception:
                            func_copy["arguments"] = str(args_val)
                    call_copy["function"] = func_copy
                fixed_calls.append(call_copy)
            normalized["tool_calls"] = fixed_calls
        return normalized

    messages = [_normalize_message(m) for m in messages]
    num_turns = 0
    for i in range(len(messages)):
        if messages[i].get("role") == "system":
            continue
        # parallel tool calls are in single turn
        if i == 0 or messages[i].get("role") != messages[i - 1].get("role"):
            num_turns += 1
    return num_turns


async def encode_messages(tokenizer: AutoTokenizer,
                          messages: List[Dict[str, Any]],
                          response_length: int = 128000,
                          tools: Dict[str, Any] = None) -> Tuple[List[int], List[int], List[int]]:
    """Encode messages to IDs.

    Args:
        tokenizer (AutoTokenizer): Tokenizer for tokenize messages.
        messages (List[Dict[str, Any]]): List of messages in OpenAI request format.
        response_length (int): Max length of response.
        tools: Tool list used by the agent.

    Returns:
        prompt_ids, response_ids, response_mask.
    """
    # Ensure tools is iterable for chat templates that iterate over tools
    if tools is None:
        tools = []

    if not messages:
        return [], [], []

    prompt_ids = []
    response_ids = []
    response_mask = []
    chat_list = []
    loop = asyncio.get_running_loop()
    # system_prompt_prefix_ids = self.tokenizer.apply_chat_template([{}], add_generation_prompt=False, tokenize=True)
    i = 0
    try:
        while i < len(messages):
            if messages[i].get("role") == "system":
                chat_list.append(messages[i])
                i += 1
                continue
            # initial chat completion
            if messages[i].get("role") == "user":
                if i == 0 or messages[i - 1].get("role") == "system":
                    chat_list.append(messages[i])
                    prompt_ids = await loop.run_in_executor(
                        None,
                        lambda: tokenizer.apply_chat_template(
                            chat_list,
                            tools=tools,
                            add_generation_prompt=True,
                            tokenize=True,
                        ),
                    )
                else:
                    chat_list.append(messages[i])
                    cur_response_ids = await loop.run_in_executor(
                        None,
                        lambda: tokenizer.apply_chat_template(
                            chat_list,
                            add_generation_prompt=False,
                            tokenize=True,
                        ),
                    )
                    response_ids += cur_response_ids
                    response_mask += [0] * len(cur_response_ids)
                chat_list = []
                i += 1
                continue
            # assistant message
            if messages[i].get("role") == "assistant":
                chat_list.append(messages[i])
                cur_response_ids = await loop.run_in_executor(
                    None,
                    lambda: tokenizer.apply_chat_template(
                        chat_list,
                        add_generation_prompt=False,
                        tokenize=True,
                    ),
                )
                chat_list = []
                response_ids += cur_response_ids
                response_mask += [1] * len(cur_response_ids)
                i += 1
                continue
            # follow up chat completion with tool response:
            if messages[i].get("role") == "tool":
                last_assistant_message = messages[i - 1]
                chat_list.append(last_assistant_message)
                token_assistant = await loop.run_in_executor(
                    None,
                    lambda: tokenizer.apply_chat_template(
                        chat_list,
                        add_generation_prompt=False,
                        tokenize=True,
                    ),
                )
                while i < len(messages) and messages[i].get("role") == "tool":
                    chat_list.append(messages[i])
                    i += 1
                token_assistant_tool = await loop.run_in_executor(
                    None,
                    lambda: tokenizer.apply_chat_template(
                        chat_list,
                        add_generation_prompt=False,
                        tokenize=True,
                    ),
                )
                tool_response_ids = token_assistant_tool[len(token_assistant):]
                chat_list = []
                response_ids += tool_response_ids
                response_mask += [0] * len(tool_response_ids)
    except Exception as e:
        raise Exception(f"Failed to convert messages to agentloop_output: {messages}. {traceback.format_exc()}")

    max_response_length = min(response_length, len(response_ids))
    return prompt_ids, response_ids[:max_response_length], response_mask[:max_response_length]


def get_agent_tool_env_and_servers(
        url: str = None,
        mcp_server_list: str = "",
        tool_config: Dict[str, Any] = None
) -> tuple[Dict[str, Any], List[str]]:
    """Build MCP client config and server list from inputs and environment.

    Args:
        url (str, optional):
            Base URL of the MCP server. If not provided, it tries
            to read from environment variable `MCP_SERVER_URL` or
            from `tool_config["url"]`. This parameter is required
            effectively; if it cannot be resolved, a ValueError is raised.
        mcp_server_list (str, optional):
            Comma-separated server identifiers to pass through header
            `MCP_SERVERS`. Defaults to an empty string. Can also be
            provided via `tool_config["mcp_servers"]`.
        tool_config (Dict[str, Any], optional):
            Extra configuration overrides:
            - `url` (str): same as `url` argument.
            - `mcp_servers` (str): same as `mcp_server_list` argument.
            - `server_name` (str): logical name of the MCP server. Default: "aworld-mcp".
            - `type` (str): server type, e.g., "streamable-http". Default: "streamable-http".
            - `timeout` (int): request timeout seconds. Default: 600.
            - `sse_read_timeout` (int): SSE read timeout seconds. Default: 600.
            - `client_session_timeout_seconds` (int): client session timeout seconds. Default: 600.

    Returns:
        tuple[Dict[str, Any], List[str]]: A tuple `(mcp_config, servers)` where
            - `mcp_config` is a dict compatible with MCP client configuration schema.
            - `servers` is a list of server names (keys of `mcp_config["mcpServers"]`).

    Example:
        >>> mcp_config, servers = get_agent_tool_env_and_servers(
        ...     url="http://localhost:8080/mcp",
        ...     mcp_server_list="serverA,serverB",
        ...     tool_config={
        ...         "server_name": "aworld-mcp",
        ...         "type": "streamable-http",
        ...         "timeout": 600,
        ...     },
        ... )
        >>> servers
        ['aworld-mcp']
        >>> mcp_config["mcpServers"]["aworld-mcp"]["url"]
        'http://localhost:8080/mcp'
    """
    if tool_config is None:
        tool_config = {}
    server_url = url or tool_config.get("url", os.getenv("MCP_SERVER_URL"))
    mcp_servers_str = mcp_server_list or tool_config.get("mcp_servers", mcp_server_list)
    if not server_url:
        raise ValueError("url is required. Please set MCP_SERVER_URL environment variable \
            or provide 'url' in tool_config parameter.")
    server_name = tool_config.get('server_name', 'aworld-mcp')
    server_type = tool_config.get('type', 'streamable-http')
    timeout = tool_config.get('timeout', 600)
    sse_read_timeout = tool_config.get('sse_read_timeout', 600)
    client_session_timeout_seconds = tool_config.get('client_session_timeout_seconds', 600)
    mcp_config = {
        "mcpServers": {
            server_name: {
                "type": server_type,
                "url": server_url,
                "headers": {
                    "MCP_SERVERS": mcp_servers_str,
                },
                "timeout": timeout,
                "sse_read_timeout": sse_read_timeout,
                "client_session_timeout_seconds": client_session_timeout_seconds,
            }
        }
    }
    servers = list(server_name for server_name in mcp_config.get("mcpServers", {}).keys())
    return mcp_config, servers