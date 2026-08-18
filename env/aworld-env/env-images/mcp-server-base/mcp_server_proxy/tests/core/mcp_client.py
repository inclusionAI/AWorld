import asyncio
from contextlib import asynccontextmanager
import json
import subprocess
import logging
from pathlib import Path
from typing import Any, AsyncGenerator, List

from mcp import ClientSession
from mcp.types import (
    LoggingMessageNotificationParams,
    ElicitResult,
    ElicitRequestParams,
)
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.context import RequestContext

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def mcp_client(
    base_url: str,
    token: str,
    session_id: str | None = None,
    image_version: str | None = None,
    headers: dict[str, str] = {},
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

    url = f"{base_url}/mcp"
    async with streamablehttp_client(
        url=url,
        headers=headers,
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
