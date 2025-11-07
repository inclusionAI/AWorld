import sys
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from aworld.logs.util import logger
from contextlib import AsyncExitStack


async def client_test(server_script_path: str, python_path: str = None):
    """
    Args:
        server_script_path: (.py 或 .js)
    """
    if not python_path:
        args = ['run', server_script_path]
    else:
        args = ['run', '--python', python_path, server_script_path]
    logger.info(f"args: {args}")

    server_params = StdioServerParameters(
        command='uv',
        args=args,
        env=None
    )
    exit_stack = AsyncExitStack()
    try:
        stdio, write = await exit_stack.enter_async_context(stdio_client(server_params))
        logger.info("start to connect to server")
        session = await exit_stack.enter_async_context(ClientSession(stdio, write))
        logger.info("session created")
        await asyncio.wait_for(session.initialize(), timeout=10.0)
        logger.info("session initialized")
        response = await session.list_tools()
        tools = response.tools
        logger.info("\nConnected to server, tools include:", [tool.name for tool in tools])

        tool_args = {"question": "2023年中国gdp是多少"}

        result = await session.call_tool(tools[0].name, tool_args)
        logger.info("Call tool result:", result)
    except Exception as e:
        import traceback
        logger.error(traceback.format_exc())
    finally:
        await exit_stack.aclose()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        logger.error("Usage: python client.py <path_to_server_script> [python_path]")
        sys.exit(1)
    asyncio.run(client_test(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None))
