"""Script to generate mcp_tool_schema.json"""

import asyncio
from contextlib import AsyncExitStack
from datetime import timedelta
import json
import logging
from pathlib import Path
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def _list_tools(server_name: str, config: dict):
    logger.info(f"Starting tool server {server_name} with config {config}")
    async with AsyncExitStack() as exit_stack:
        try:
            if config.get("type") == "sse":
                read_stream, write_stream = await exit_stack.enter_async_context(
                    sse_client(
                        url=config.get("url", ""),
                        headers=config.get("headers", {}),
                        timeout=config.get("timeout", 5),
                        sse_read_timeout=config.get("sse_read_timeout", 60 * 5),
                        auth=config.get("auth", None),
                    )
                )

            elif config.get("type") == "streamable_http":
                read_stream, write_stream, _ = await exit_stack.enter_async_context(
                    streamablehttp_client(
                        url=config.get("url", ""),
                        headers=config.get("headers", {}),
                        timeout=config.get("timeout", 60),
                        sse_read_timeout=config.get("sse_read_timeout", 60 * 5),
                        auth=config.get("auth", None),
                    )
                )

            else:  # stdio
                base_folder = Path(__file__).resolve().parent
                server_params = StdioServerParameters(
                    command=config.get("command", ""),
                    args=config.get("args", []),
                    env=config.get("env", {}),
                    cwd=str(base_folder / config.get("cwd", "")),
                )
                read_stream, write_stream = await exit_stack.enter_async_context(
                    stdio_client(server=server_params)
                )

            # Create session and tool manager
            session = await exit_stack.enter_async_context(
                ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timedelta(
                        seconds=config.get("read_timeout", 60)
                    ),
                )
            )
            await session.initialize()
            result = await session.list_tools()
            return server_name, result.tools
        except Exception as e:
            logger.error(f"Error starting tool server {server_name}: {config}, {e}")
            raise e


def gen_unique_name(name: str, names: set[str]) -> str:
    for i in range(1, 10):
        full_name = f"v{i}-{name}"
        if full_name not in names:
            return full_name
    raise Exception(
        f"Tool name {name} could not get unique name, please check mcp_config.py"
    )


async def list_mcp_server_tools():
    from mcp_config import mcp_config as config, black_list_tools

    tool_names = set()
    json_tools = {}
    for server_name, server_config in config.get("mcpServers", {}).items():
        result = await _list_tools(server_name, server_config)
        server_name, tools = result
        logger.info(f"Result for {server_name}: {tools}")

        tools_dict = [
            {
                "name": tool.name,
                "title": tool.title,
                "description": tool.description,
                "inputSchema": tool.inputSchema,
                "annotations": (
                    {
                        "title": tool.annotations.title,
                        "readOnlyHint": tool.annotations.readOnlyHint,
                        "destructiveHint": tool.annotations.destructiveHint,
                        "idempotentHint": tool.annotations.idempotentHint,
                        "openWorldHint": tool.annotations.openWorldHint,
                    }
                    if tool.annotations
                    else None
                ),
                "meta": {"_tool_name_": tool.name, "_server_name_": server_name}
                | (tool.meta or {}),
            }
            for tool in tools
            if (server_name, tool.name) not in black_list_tools
        ]
        # Server name unique check
        if server_name in json_tools:
            raise Exception(
                f"Server name {server_name} already exists, please check mcp_config.py"
            )
        # Tool name check
        for t in tools_dict:
            tool_name = t.get("meta", {}).get("_tool_name_", "")
            server_name = t.get("meta", {}).get("_server_name_", "")
            full_name = tool_name
            if "-" in tool_name:
                raise Exception(
                    f"Tool name {tool_name} format illegal, only support [A-z0-9_]!"
                )
            if tool_name in tool_names:
                full_name = gen_unique_name(tool_name, tool_names)
                t["name"] = full_name
                logger.warning(
                    f"Tool name {tool_name} already exists, gen_unique_name to {full_name}"
                )
            if len(full_name) > 50:
                raise Exception(
                    f"Tool full name {full_name} is too long(max 50 chars), please reduce server_name[{server_name}] or tool_name[{tool_name}]!"
                )
            tool_names.add(full_name)
        json_tools[server_name] = tools_dict

    if json_tools:
        with open(Path(__file__).resolve().parent / "mcp_tool_schema.json", "w") as f:
            f.write(json.dumps(json_tools, indent=4, ensure_ascii=False))
    else:
        logger.error("No results for list_results!")


if __name__ == "__main__":
    asyncio.run(list_mcp_server_tools())
