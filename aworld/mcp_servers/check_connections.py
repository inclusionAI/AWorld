import asyncio

from loguru import logger

from aworld.mcp.utils import mcp_tool_desc_transform

if __name__ == "__main__":
    mcp_tools = asyncio.run(mcp_tool_desc_transform(["orcid"]))
    logger.success(mcp_tools)
