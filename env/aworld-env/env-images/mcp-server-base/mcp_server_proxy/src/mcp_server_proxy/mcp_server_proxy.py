import traceback
from typing import Any, Awaitable, Callable, Sequence
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ContentBlock
import logging
from mcp.types import Tool as ClientTool
from datetime import datetime

from .mcp_server_executor import MCPServerExecutor
from .mcp_server_loader import MCPServerLoader

logger = logging.getLogger(__name__)


DATETIME_FMT = "%Y/%m/%d %H:%M:%S.%f"


class MCPServerProxy(FastMCP):
    def __init__(self, *args, **kwargs):
        # Extract on_init callback from kwargs
        self._pre_request_callback = kwargs.pop("pre_request_callback", None)
        super().__init__(*args, **kwargs)
        self._last_active_time = self.now()
        self._bind_session_id = ""
        self._mcp_tool_schema: dict[str, list[dict[str, Any]]] = {}
        self._mcp_server_executors: dict[str, MCPServerExecutor] = {}
        self._mcp_server_loader = MCPServerLoader()

    async def initialize(self):
        self._load_tool_schema()
        self._load_mcp_servers()

    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> Sequence[ContentBlock] | dict[str, Any]:
        """Call a tool by name with arguments."""
        self._last_active_time = self.now()
        try:
            context = self.get_context()
            request_mcp_server_executor = self._get_request_mcp_server_executor(
                tool_name=name
            )

            return await request_mcp_server_executor.call_tool(
                name, arguments, context=context, convert_result=False
            )
        except:
            logger.error(f"Error calling tool {name}: {traceback.format_exc()}")
            raise

    async def list_tools(self) -> list[ClientTool]:
        """List all available tools."""
        self._last_active_time = self.now()
        try:
            request_mcp_servers = self._get_request_mcp_servers()

            request_tools = [
                tool
                for server_name, server_tools in self._mcp_tool_schema.items()
                if server_name in request_mcp_servers
                for tool in server_tools
            ]

            return [
                ClientTool(
                    name=tool.get("name", ""),
                    title=tool.get("title", ""),
                    description=tool.get("description", ""),
                    inputSchema=tool.get("inputSchema", {}),
                    annotations=tool.get("annotations", {}),
                    _meta=tool.get("_meta", {}),
                )
                for tool in request_tools
            ]
        except:
            logger.error(f"Error listing tools: {traceback.format_exc()}")
            raise

    def get_last_active_time(self) -> str:
        return self._last_active_time

    def get_bind_session_id(self) -> str:
        return self._bind_session_id

    def now(self) -> str:
        return datetime.now().strftime(DATETIME_FMT)

    def _load_tool_schema(self):
        self._mcp_tool_schema = self._mcp_server_loader.load_mcp_tool_schema()
        mcp_tool_schema = ""
        for server_name, server_tools in self._mcp_tool_schema.items():
            mcp_tool_schema += f"  {server_name}:\n"
            for tool in server_tools:
                mcp_tool_schema += f"    - {tool.get('name', '')}\n"
        logger.info(f"Loaded MCP tool schema: mcp_tool_schema={mcp_tool_schema}")

    def _load_mcp_servers(self):
        for (
            server_name,
            config,
        ) in self._mcp_server_loader.load_mcp_servers_config().items():
            self._mcp_server_executors[server_name] = MCPServerExecutor(
                server_name, config
            )

    def _get_request_mcp_servers(self) -> list[str]:
        context = self.get_context()
        request_servers = context.request_context.request.headers.get("MCP_SERVERS")
        if request_servers:
            return [server.strip() for server in request_servers.split(",")]
        else:
            return list(self._mcp_server_executors.keys())

    def _get_request_mcp_server_executor(self, tool_name: str) -> MCPServerExecutor:
        request_mcp_servers = self._get_request_mcp_servers()

        request_tools = {
            server_name: tool
            for server_name, server_tools in self._mcp_tool_schema.items()
            if server_name in request_mcp_servers
            for tool in server_tools
            if tool.get("name", "") == tool_name
        }

        if not request_tools:
            raise ToolError(f"Tool {tool_name} not found")
        else:
            if len(request_tools) > 1:
                logger.warning(
                    f"Tool {tool_name} found in multiple MCP servers: {request_tools}"
                )
            server_name = list(request_tools.keys())[0]
            return self._mcp_server_executors[server_name]

    def streamable_http_app(self):
        """Override streamable_http_app to add on_init middleware."""
        from starlette.middleware import Middleware
        from starlette.middleware.base import BaseHTTPMiddleware
        from starlette.requests import Request

        class PreRequestMiddleware(BaseHTTPMiddleware):
            """Middleware to execute on_init callback on first MCP request."""

            def __init__(
                self,
                app,
                pre_request_callback: (
                    Callable[[Request], Awaitable[None]] | None
                ) = None,
            ):
                super().__init__(app)
                self._pre_request_callback = pre_request_callback

            async def dispatch(self, request: Request, call_next):
                # Check if this is the first request and we have an on_init callback
                if self._pre_request_callback:
                    try:
                        await self._pre_request_callback(request)
                    except BaseException as e:
                        logger.error(f"Error executing pre_request_callback: {e}")

                response = await call_next(request)
                return response

        # Get the original app
        app = super().streamable_http_app()

        # Add our on_init middleware if callback is provided
        if self._pre_request_callback:
            pre_request_middleware = Middleware(
                PreRequestMiddleware,
                pre_request_callback=self._pre_request_callback,
            )
            app.user_middleware = [pre_request_middleware, *app.user_middleware]

        return app
