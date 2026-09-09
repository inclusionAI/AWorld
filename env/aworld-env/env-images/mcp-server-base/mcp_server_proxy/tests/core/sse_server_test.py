import logging

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

mcp = FastMCP(port=8081)


@mcp.tool()
async def hello(name: str):
    """
    Say hello to the world
    """
    return f"Hello {name}"


class HelloResponse(BaseModel):
    data: str = Field(description="The data to return")


@mcp.tool()
async def bar(name: str) -> HelloResponse:
    """
    Say hello to the world
    """
    return HelloResponse(data=f"Hello {name} from bar")


if __name__ == "__main__":
    mcp.run(transport="sse")
