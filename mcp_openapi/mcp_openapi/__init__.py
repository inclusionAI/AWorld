"""
MCP OpenAPI - A RESTful API proxy for Model Context Protocol (MCP) servers
"""

__version__ = "0.1.9"



#todo back
from .main import run, cli_main
from .server import get_tool_handler, get_model_fields
#from mcp_openapi.mcp_openapi.server import get_tool_handler,get_model_fields
