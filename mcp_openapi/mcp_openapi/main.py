import asyncio
import json
import os
import socket
import logging
from contextlib import asynccontextmanager
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# todo back
from .server import get_model_fields, get_tool_handler

#from mcp_openapi.mcp_openapi.server import get_model_fields, get_tool_handler


# Configure logging
logger = logging.getLogger("mcp_openapi")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)


async def create_dynamic_endpoints(app: FastAPI):
    session: ClientSession = app.state.session
    if not session:
        logger.error("Session is not initialized in the app state")
        raise ValueError("Session is not initialized in the app state.")

    logger.info(f"Initializing MCP session for app: {app.title}")
    try:
        result = await session.initialize()
        logger.info(f"Session initialization result: {result}")
        server_info = getattr(result, "serverInfo", None)

        if server_info:
            app.title = server_info.name or app.title
            app.description = (
                f"{server_info.name} MCP Server" if server_info.name else app.description
            )
            app.version = server_info.version or app.version
            logger.info(f"Server info received: {server_info.name}, version: {server_info.version}")
        else:
            logger.warning(f"No server info received for {app.title}")

        logger.info("Listing available tools...")
        tools_result = await session.list_tools()
        logger.info(f"Tools result: {tools_result}")
        tools = tools_result.tools
        logger.info(f"Found {len(tools)} tools: {[tool.name for tool in tools]}")

        for tool in tools:
            endpoint_name = tool.name
            endpoint_description = tool.description
            logger.info(f"Creating endpoint for tool: {endpoint_name}, description: {endpoint_description}")

            inputSchema = tool.inputSchema
            logger.info(f"Input schema for {endpoint_name}: {inputSchema}")
            outputSchema = getattr(tool, "outputSchema", None)
            logger.info(f"Output schema for {endpoint_name}: {outputSchema}")

            form_model_fields = get_model_fields(
                f"{endpoint_name}_form_model",
                inputSchema.get("properties", {}),
                inputSchema.get("required", []),
                inputSchema.get("$defs", {}),
            )

            response_model_fields = None
            if outputSchema:
                response_model_fields = get_model_fields(
                    f"{endpoint_name}_response_model",
                    outputSchema.get("properties", {}),
                    outputSchema.get("required", []),
                    outputSchema.get("$defs", {}),
                )

            # Use app instead of session
            tool_handler = get_tool_handler(
                app,  # Pass app instead of session
                endpoint_name,
                form_model_fields,
                response_model_fields,
            )

            # Use endpoint_name as the path, ensure the path is correct
            endpoint_path = f"/{endpoint_name}"
            logger.info(f"Registering endpoint at path: {endpoint_path}")

            try:
                endpoint = app.post(
                    endpoint_path,
                    summary=endpoint_name.replace("_", " ").title(),
                    description=endpoint_description,
                    response_model_exclude_none=True,
                )(tool_handler)
                logger.info(f"Endpoint created: {endpoint_path} with handler: {endpoint}")
            except Exception as e:
                logger.error(f"Failed to create endpoint {endpoint_path}: {str(e)}")
                raise
    except Exception as e:
        logger.error(f"Error creating dynamic endpoints for {app.title}: {str(e)}", exc_info=True)
        raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    server_type = getattr(app.state, "server_type", "stdio")
    command = getattr(app.state, "command", None)
    args = getattr(app.state, "args", [])
    env = getattr(app.state, "env", {})

    logger.info(f"Initializing app: {app.title}, server_type: {server_type}")

    # Convert non-list args to list
    args = args if isinstance(args, list) else [args]

    if (server_type == "stdio" and not command) or (
            server_type == "sse" and not args[0]
    ):
        # This is the main application lifecycle handling, using AsyncExitStack to manage sub-app lifecycles
        logger.info(f"Managing main app lifespan with mounted sub-apps")
        from contextlib import AsyncExitStack
        from starlette.routing import Mount

        async with AsyncExitStack() as stack:
            for route in app.routes:
                if isinstance(route, Mount) and hasattr(route.app, "router"):
                    logger.info(f"Setting up lifespan for mounted app at: {route.path}")
                    await stack.enter_async_context(
                        route.app.router.lifespan_context(route.app)
                    )
            yield
    else:
        try:
            if server_type == "stdio":
                logger.info(f"Starting stdio client: {command} {args}")
                server_params = StdioServerParameters(
                    command=command,
                    args=args,
                    env={**env},
                )
                async with stdio_client(server_params) as (reader, writer):
                    logger.info(f"stdio client connected for {app.title}")
                    async with ClientSession(reader, writer) as session:
                        app.state.session = session
                        await create_dynamic_endpoints(app)
                        yield
            elif server_type == "sse":
                url = args[0]  # args[0] is the URL after conversion above
                logger.info(f"Starting SSE client for URL: {url}")
                sse_read_timeout = getattr(app.state, "sse_read_timeout", None)
                logger.info(f"SSE read timeout: {sse_read_timeout}")
                try:
                    async with sse_client(url=url, sse_read_timeout=sse_read_timeout) as (
                            reader,
                            writer,
                    ):
                        logger.info(f"SSE client connected successfully for {app.title}")
                        async with ClientSession(reader, writer) as session:
                            app.state.session = session
                            logger.info(f"Creating dynamic endpoints for {app.title}...")
                            await create_dynamic_endpoints(app)
                            logger.info(f"All endpoints created for {app.title}")
                            yield
                except Exception as e:
                    logger.error(f"Error connecting to SSE endpoint {url}: {str(e)}", exc_info=True)
                    raise
        except Exception as e:
            logger.error(f"Error in lifespan for {app.title}: {str(e)}", exc_info=True)
            raise


async def run(
        # host: str = "127.0.0.1",
        port: int = 9000,
        cors_allow_origins=["*"],
        **kwargs,
):
    path_prefix = kwargs.get("path_prefix") or "/"
    # MCP Config
    config_path = kwargs.get("config_path")
    # mcpo server
    name = kwargs.get("name") or "MCP OpenAPI Proxy"
    description = (
            kwargs.get("description") or "Automatically generated API from MCP Tool Schemas"
    )
    version = kwargs.get("version") or "1.0"

    logger.info("Starting MCPO Server...")
    logger.info(f"  Name: {name}")
    logger.info(f"  Version: {version}")
    logger.info(f"  Description: {description}")
    logger.info(f"  Hostname: {socket.gethostname()}")
    logger.info(f"  Port: {port}")
    logger.info(f"  CORS Allowed Origins: {cors_allow_origins}")

    main_app = FastAPI(
        title=name,
        description=description,
        version=version,
        lifespan=lifespan,
    )

    main_app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_allow_origins or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Add root path endpoint to provide API guide
    @main_app.get("/", include_in_schema=True)
    async def api_guide():
        """MCP OpenAPI Proxy Service API Guide"""
        return {
            "title": "MCP OpenAPI Proxy Service API Guide",
            "description": "This is the API guide for the MCP OpenAPI proxy service, providing interfaces to access MCP tools",
            "api_endpoints": {
                "/list-all-tools": "Get the list of tools and their schemas from all MCP servers",
                "/docs": "OpenAPI documentation interface",
                "/{server_name}/tools": "Get the list of tools and their schemas from a specific MCP server",
                "/{server_name}/health": "Check the health status of a specific MCP server",
                "/{server_name}/{tool_name}": "Call a specific tool on a specific MCP server"
            },
            "servers": list(mcp_servers.keys())
        }

    # 1. Get mcp_servers
    if not config_path:
        logger.error("MCPO server_command or config_path must be provided.")
        raise ValueError("You must provide either server_command or config.")

    logger.info(f"Loading MCP server configurations from: {config_path}")
    with open(config_path, "r") as f:
        config_data = json.load(f)
    if not config_data:
        logger.error("MCP server configurations are empty.")
        raise ValueError("MCP server configurations are empty.")

    mcp_servers = config_data.get("mcpServers", {})
    if not mcp_servers:
        logger.error(f"No 'mcpServers' found in config file: {config_path}")
        raise ValueError("No 'mcpServers' found in config file.")

    # 2. Check if mcp_servers configuration is valid
    for server_name_cfg, server_cfg_details in mcp_servers.items():
        if server_cfg_details.get("type", "") == "stdio":
            if not server_cfg_details.get("command"):
                raise ValueError(f"Unknown configuration for MCP server: {server_name_cfg} not command")
        elif server_cfg_details.get("type", "") == "sse":
            if not server_cfg_details.get("url"):
                raise ValueError(f"Unknown configuration for MCP server: {server_name_cfg} not url")
        else:
            raise ValueError(f"Unknown configuration for MCP server: {server_name_cfg} type is error")

    # 3. Process mcp_servers
    main_app.description += "\n\n- **available tools**："

    # Store schema information for all servers' tools
    all_tools_schemas = {}

    for server_name, server_cfg in mcp_servers.items():
        logger.info(f"Setting up MCP server: {server_name}")
        sub_app = FastAPI(
            title=f"{server_name}",
            description=f"{server_name} MCP Server\n\n- [back to tool list](/docs)",
            version="1.0",
            lifespan=lifespan,
        )
        sub_app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_allow_origins or ["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # Store server name in sub-app state
        sub_app.state.server_name = server_name

        # Define health check endpoint using factory pattern
        def create_health_check(server_name_val):
            async def health_check():
                # Use server name captured in factory function
                return {"status": "ok", "server_name": server_name_val}

            return health_check

        # Register health check endpoint
        sub_app.get("/health")(create_health_check(server_name))

        # Define tools list endpoint using factory pattern
        def create_list_tools(server_name_val, server_cfg):
            async def list_tools():
                """Get the list of all tools and their schema information from the current MCP server"""
                try:
                    logger.info(f"Getting tools for server: {server_name_val}")

                    # Get server configuration
                    server_config_type = server_cfg.get("type", "")

                    if server_config_type == "stdio":
                        logger.info(f"Connecting to stdio server: {server_name_val}")
                        command = server_cfg.get("command")
                        args_list = server_cfg.get("args", [])
                        env_dict = {**os.environ, **server_cfg.get("env", {})}

                        if not command:
                            return {"error": f"Command not found for stdio server {server_name_val}"}

                        server_params = StdioServerParameters(
                            command=command,
                            args=args_list,
                            env=env_dict,
                        )

                        # Create dedicated connection to get tools list
                        logger.info(
                            f"Creating stdio connection for {server_name_val} with command: {command} args: {args_list}")
                        async with stdio_client(server_params) as (reader, writer):
                            async with ClientSession(reader, writer) as session:
                                # Initialize session
                                await session.initialize()
                                # Get tools list
                                tools_result = await session.list_tools()
                                tools = tools_result.tools

                                # Build tool information list
                                tools_info = [
                                    {
                                        "name": tool.name,
                                        "description": tool.description,
                                        "parameters": tool.inputSchema
                                    }
                                    for tool in tools
                                ]

                                logger.info(f"Found {len(tools_info)} tools for stdio server {server_name_val}")
                                return {
                                    "server_name": server_name_val,
                                    "server_type": "stdio",
                                    "tools_count": len(tools_info),
                                    "tools": tools_info
                                }

                    elif server_config_type == "sse":
                        logger.info(f"Connecting to SSE server: {server_name_val}")
                        url = server_cfg.get("url")
                        if not url:
                            return {"error": f"URL not found for SSE server {server_name_val}"}

                        sse_read_timeout = server_cfg.get("sse_read_timeout")

                        # Create dedicated connection to get tools list
                        logger.info(f"Creating SSE connection for {server_name_val} with URL: {url}")
                        async with sse_client(url=url, sse_read_timeout=sse_read_timeout) as (reader, writer):
                            async with ClientSession(reader, writer) as session:
                                # Initialize session
                                await session.initialize()
                                # Get tools list
                                tools_result = await session.list_tools()
                                tools = tools_result.tools

                                # Build tool information list
                                tools_info = [
                                    {
                                        "name": tool.name,
                                        "description": tool.description,
                                        "parameters": tool.inputSchema
                                    }
                                    for tool in tools
                                ]

                                logger.info(f"Found {len(tools_info)} tools for SSE server {server_name_val}")
                                return {
                                    "server_name": server_name_val,
                                    "server_type": "sse",
                                    "tools_count": len(tools_info),
                                    "tools": tools_info
                                }
                    else:
                        return {"error": f"Unsupported server type: {server_config_type}"}

                except Exception as e:
                    logger.error(f"Error in list_tools for {server_name_val}: {str(e)}", exc_info=True)
                    return {"error": f"Failed to list tools: {str(e)}"}

            return list_tools

        # Register tools list endpoint, pass server configuration
        sub_app.get("/list_tools")(create_list_tools(server_name, server_cfg))

        server_config_type = server_cfg.get("type")
        if server_config_type == "stdio":
            logger.info(f"  Type: stdio, Command: {server_cfg['command']}")
            sub_app.state.server_type = "stdio"
            sub_app.state.command = server_cfg["command"]
            sub_app.state.args = server_cfg.get("args", [])
            sub_app.state.env = {**os.environ, **server_cfg.get("env", {})}
        elif server_config_type == "sse":
            logger.info(f"  Type: sse, URL: {server_cfg['url']}")
            sub_app.state.server_type = "sse"
            # Use URL string directly, lifespan will handle conversion
            sub_app.state.args = server_cfg["url"]
            # Add other SSE configuration parameters
            if "sse_read_timeout" in server_cfg:
                sub_app.state.sse_read_timeout = server_cfg["sse_read_timeout"]
        else:
            error_msg = f"Unknown configuration for MCP server: {server_name} type is error"
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Simplify path mounting, directly use the same method as mcpo
        mount_path = f"{path_prefix}{server_name}"
        logger.info(f"Mounting {server_name} at path: {mount_path}")
        main_app.mount(mount_path, sub_app)
        main_app.description += f"\n    - [{server_name}]({mount_path}/docs)"

        # Store server information
        all_tools_schemas[server_name] = {
            "mount_path": mount_path
        }

    # Add endpoint to get all tools schema
    @main_app.get("/list_tools")
    async def list_all_tools():
        """Get the list of available tools and their schema information from all MCP servers"""
        result = {}

        for server_name, info in all_tools_schemas.items():
            try:
                # Try to get the tools list from the sub-app
                mount_path = info["mount_path"]
                # Find the sub-app using routes
                sub_app = None
                for route in main_app.routes:
                    if hasattr(route, "path") and route.path == mount_path:
                        sub_app = route.app
                        break

                if not sub_app:
                    logger.warning(f"Cannot find sub-app {server_name}")
                    result[server_name] = []
                    continue

                # Get server type and connection parameters
                server_type = getattr(sub_app.state, "server_type", "stdio")

                if server_type == "stdio":
                    command = getattr(sub_app.state, "command", None)
                    args_list = getattr(sub_app.state, "args", [])
                    env = getattr(sub_app.state, "env", {})

                    server_params = StdioServerParameters(
                        command=command,
                        args=args_list,
                        env=env,
                    )

                    # Create a new connection and session for the current request
                    async with stdio_client(server_params) as (reader, writer):
                        async with ClientSession(reader, writer) as session:
                            # Initialize session
                            await session.initialize()
                            # Get tools list
                            tools_result = await session.list_tools()
                            tools = tools_result.tools

                            # Build tool information
                            tools_info = [
                                {
                                    "name": tool.name,
                                    "description": tool.description,
                                    "parameters": tool.inputSchema
                                }
                                for tool in tools
                            ]
                            result[server_name] = tools_info

                elif server_type == "sse":
                    args_list = getattr(sub_app.state, "args", [])
                    url = args_list if isinstance(args_list, str) else args_list[0]
                    sse_read_timeout = getattr(sub_app.state, "sse_read_timeout", None)

                    # Create a new connection and session for the current request
                    async with sse_client(url=url, sse_read_timeout=sse_read_timeout) as (reader, writer):
                        async with ClientSession(reader, writer) as session:
                            # Initialize session
                            await session.initialize()
                            # Get tools list
                            tools_result = await session.list_tools()
                            tools = tools_result.tools

                            # Build tool information
                            tools_info = [
                                {
                                    "name": tool.name,
                                    "description": tool.description,
                                    "parameters": tool.inputSchema
                                }
                                for tool in tools
                            ]
                            result[server_name] = tools_info
                else:
                    logger.warning(f"Unsupported server type: {server_type}")
                    result[server_name] = []
            except Exception as e:
                logger.warning(f"Error getting tools list for {server_name}: {str(e)}")
                result[server_name] = []

        return result

    # 4. Start
    logger.info("Uvicorn server starting...")

    config = uvicorn.Config(
        app=main_app,
        host="0.0.0.0",
        port=port,
        log_level="info",
    )
    server = uvicorn.Server(config)
    await server.serve()


def cli_main():
    """Command line entry point function"""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="MCP OpenAPI Proxy Server")
    parser.add_argument("--config_path", "-c", help="Path to configuration file",
                        default="/Users/honglifeng/Documents/project/product/mcp_env/AWorld/mcp_openapi/config.json")
    # parser.add_argument("--host", help="Host to bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, help="Port to bind", default=9002)

    args = parser.parse_args()

    config_path = args.config_path

    if not os.path.exists(config_path):
        print(f"Error: Config file not found at {config_path}")
        sys.exit(1)

    logger.info(f"Starting with config: {config_path}")
    asyncio.run(run(
        # host=args.host,
        port=args.port,
        config_path=config_path
    ))


if __name__ == "__main__":
    cli_main()
