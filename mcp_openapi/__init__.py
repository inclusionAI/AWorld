"""
MCP OpenAPI - A RESTful API proxy for Model Context Protocol (MCP) servers
"""

__version__ = "0.1.9"

# 导入主模块和server模块
from .mcp_openapi.main import run, cli_main
from .mcp_openapi.server import get_tool_handler, get_model_fields

__all__ = [
    "__version__",
    "run",
    "cli_main",
    "get_tool_handler",
    "get_model_fields"
] 