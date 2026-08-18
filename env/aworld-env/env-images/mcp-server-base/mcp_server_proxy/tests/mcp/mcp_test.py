import base64
import os
from typing import List
import asyncio
from contextlib import asynccontextmanager
import json
import logging
import subprocess
from typing import AsyncGenerator, Any
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import (
    LoggingMessageNotificationParams,
    ElicitResult,
    ElicitRequestParams,
)
from mcp.shared.context import RequestContext
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)

load_dotenv()


def read_env(e): return (os.getenv(f"URL_{e}"), os.getenv(f"TOKEN_{e}"))


base_url, token = read_env("MCP_DEBUG")

env = {"E2B_API_KEY": "xxxx"}

tool_test_cases = [
    # {
    #     "tool_name": "browser_navigate",
    #     "args": {"url": "https://www.alipay.com"},
    # },
    # {
    #     "tool_name": "browser_wait_for",
    #     "args": {"time": 3000},
    # },
    # {
    #     "tool_name": "browser_take_screenshot",
    #     "args": {},
    # },
    {
        "tool_name": "read_url",
        "args": {"url": "https://www.weather.com.cn/weather/101210101.shtml"},
    },
]


@asynccontextmanager
async def mcp_client(
    base_url: str,
    token: str,
    session_id: str | None = None,
    image_version: str | None = None,
    headers: dict[str, str] = {},
    env: dict[str, str] = {},
    mcp_servers: List[str] = [],
) -> AsyncGenerator[ClientSession, None]:
    headers = headers or {}
    headers["Authorization"] = f"Bearer {token}"
    if mcp_servers:
        headers["MCP_SERVERS"] = ",".join(mcp_servers)

    if session_id:
        headers["SESSION_ID"] = session_id

    if image_version:
        headers["IMAGE_VERSION"] = image_version

    if env:
        headers["IMAGE_ENV"] = json.dumps(env)

    url = f"{base_url}/mcp"
    async with streamablehttp_client(
        url=url,
        headers=headers,
        timeout=300,
    ) as (
        read_stream,
        write_stream,
        get_session_id,
    ):

        async def logging_callback(params: LoggingMessageNotificationParams):
            logger.info(f"Receive logging callback: {params}")

        async def elicitation_callback(
            context: RequestContext["ClientSession", Any],
            params: ElicitRequestParams,
        ) -> ElicitResult:
            logger.info(f"Receive elicitation callback: {params}")
            return ElicitResult(action="accept", content={"user_name": "John"})

        async with ClientSession(
            read_stream=read_stream,
            write_stream=write_stream,
            logging_callback=logging_callback,
            elicitation_callback=elicitation_callback,
        ) as session:
            logger.info(f"MCP client connected: url={url}")
            await session.initialize()
            logger.info(
                f"MCP client session initialized: url={url}, session_id={get_session_id()}"
            )

            yield session


async def main():
    async with mcp_client(base_url, token, env=env) as session:
        ls = await session.list_tools()
        assert ls and ls.tools, "list_tools return null"
        tools = ls.tools
        logger.info(
            f"list_tools return:\n  - {'\n  - '.join([t.name for t in tools])}")

        async def progress_callback(
            progress: float, total: float | None, message: str | None
        ):
            logger.info(
                f"Receive progress callback: progress={progress}, total={total}, message={message}"
            )

            if "```tool_card" in message:
                data = json.loads(message.split(
                    "```tool_card")[1].split("```")[0])
                vnc_url = f"{base_url}{data.get('card_data').get('url')}"
                logger.info(f"VNC URL: {vnc_url}")
                subprocess.run(["open", vnc_url])

        for t in tool_test_cases:
            tool_name = t["tool_name"]
            args = t["args"]
            logger.info(f"call tool: {tool_name}")
            result = await session.call_tool(
                tool_name, args, progress_callback=progress_callback
            )
            content = result.content[0]
            if content.type == "image":
                try:
                    image_data = base64.b64decode(content.data)
                    with open("screenshot.png", "wb") as f:
                        f.write(image_data)
                    logger.info("Image saved as screenshot.png")
                except BaseException as e:
                    logger.error(f"Failed to decode and save image: {e}")
            else:
                logger.info(f"tool result: {content.text[:300]}")

        input("Press Enter to continue...")


if __name__ == "__main__":
    asyncio.run(main())
