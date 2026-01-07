import asyncio
import json
import threading
import uuid
from typing import Optional, List, Dict, Any
from .base import AppProtocol
from ..core.agent_registry import LocalAgentRegistry
from ..executors.local import LocalAgentExecutor
from aworld.core.context.amni import TaskInput, ApplicationContext
from aworld.core.context.amni.config import AmniConfigFactory

try:
    from fastmcp import FastMCP
except ImportError:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        raise ImportError(
            "FastMCP is not installed. Please install it with: pip install fastmcp"
        )


class McpProtocol(AppProtocol):
    """
    MCP (Model Context Protocol) adapter for AWorldApp using FastMCP.
    Exposes agents, chat, and tools interfaces compatible with HTTP protocol.
    
    This protocol MUST be used with AWorldApp for unified lifecycle management.
    Do not call start()/stop() directly - use AWorldApp.run() instead.
    """
    
    def __init__(
        self,
        name: str = "AWorldAgent",
        transport: str = "stdio",
        host: Optional[str] = None,
        port: Optional[int] = None,
    ):
        """
        Initialize MCP Protocol.
        
        Args:
            name: MCP server name
            transport: Transport type, either "stdio", "sse", or "streamable-http"
            host: Host for SSE/streamable-http transport (required if transport="sse" or "streamable-http")
            port: Port for SSE/streamable-http transport (required if transport="sse" or "streamable-http")
            
        Note:
            - "stdio": Standard input/output mode (for CLI usage)
            - "sse": Server-Sent Events mode (HTTP+SSE)
            - "streamable-http": Streamable HTTP mode (uses SSE transport, compatible with MCP streamable-http clients)
        """
        self.name = name
        self.transport = transport
        self.host = host
        self.port = port
        self.server = FastMCP(name)
        self._running = False
        self._register_tools()
    
    def _register_tools(self) -> None:
        """Register MCP tools for agents, chat, and other functionality."""
        
        @self.server.tool(description="List all available agents.")
        async def list_agents() -> str:
            """
            List all available agents.
            Returns a JSON string containing agent information.
            
            Returns:
                JSON string with list of agents, each containing:
                - name: Agent name
                - desc: Description (optional)
                - metadata: Additional metadata (optional)
            """
            try:
                agents = LocalAgentRegistry.list_agents()
                agents_list = [
                    {
                        "name": agent.name,
                        "desc": agent.desc
                    }
                    for agent in agents
                ]
                return json.dumps({"agents": agents_list}, indent=2, ensure_ascii=False)
            except Exception as e:
                return json.dumps({"error": str(e)}, indent=2)
        
        
        @self.server.tool(description="Get information about a specific agent.")
        async def get_agent_info(agent_name: str) -> str:
            """
            Get detailed information about a specific agent.
            
            Args:
                agent_name: Name of the agent
                
            Returns:
                JSON string with agent information
            """
            try:
                agent = LocalAgentRegistry.get_agent(agent_name)
                if agent:
                    return json.dumps({
                        "name": agent.name,
                        "desc": agent.desc,
                        "metadata": agent.metadata
                    }, indent=2, ensure_ascii=False)
                
                return json.dumps({"error": f"Agent '{agent_name}' not found"}, indent=2)
            except Exception as e:
                return json.dumps({"error": str(e)}, indent=2)
        
        @self.server.tool(description="Run a task directly with an agent.")
        async def run_task(
            agent_name: str,
            task_input: str,
            user_id: Optional[str] = None,
            session_id: Optional[str] = None,
            task_id: Optional[str] = None,
            debug_mode: bool = False,
            max_steps: int = 100,
            endless_threshold: int = 3,
            ext_info: Optional[str] = None,
        ) -> str:
            """
            Run a task directly with an agent.
            This executes the task synchronously and returns the result.
            
            Args:
                agent_name: Name of the agent to execute the task
                task_input: Task input content. Can be:
                    - A plain string (user message or instruction)
                    - A JSON string representing a list of message items
                    Example JSON format:
                    '[{"type": "text", "text": "xxxx"}, {"type": "file", "file": {"filename": "test.txt", "content": "test content", "mime_type": "text/plain"}}]'
                user_id: Optional user ID (auto-generated if not provided)
                session_id: Optional session ID (auto-generated if not provided)
                task_id: Optional task ID (auto-generated if not provided)
                debug_mode: Enable debug mode (default: False)
                max_steps: Maximum execution steps (default: 100)
                endless_threshold: Endless loop detection threshold (default: 3)
                ext_info: Optional extra information as JSON string (will be parsed and passed as **kwargs)
                
            Returns:
                JSON string with task execution result
            """
            try:
                # Get agent from registry
                agent = LocalAgentRegistry.get_agent(agent_name)
                if not agent:
                    return json.dumps({
                        "error": f"Agent '{agent_name}' not found",
                        "available_agents": [a.name for a in LocalAgentRegistry.list_agents()]
                    }, indent=2, ensure_ascii=False)
                
                # Generate IDs if not provided
                if user_id is None:
                    user_id = str(uuid.uuid4().hex)
                if session_id is None:
                    session_id = str(uuid.uuid4().hex)
                if task_id is None:
                    task_id = str(uuid.uuid4().hex)
                
                # Parse task_input - support both string and list[dict] format
                # Format: [{"type": "text", "text": "..."}, {"type": "file", "file": {...}}]
                parsed_task_input = task_input
                if isinstance(task_input, str):
                    # Try to parse as JSON (list of message items)
                    try:
                        parsed_json = json.loads(task_input)
                        if isinstance(parsed_json, list):
                            # Extract text from message items
                            text_parts = []
                            for item in parsed_json:
                                if isinstance(item, dict):
                                    if item.get("type") == "text" and "text" in item:
                                        text_parts.append(item["text"])
                                    elif "text" in item:
                                        text_parts.append(item["text"])
                            if text_parts:
                                parsed_task_input = "\n".join(text_parts)
                            else:
                                # If no text found, use original string
                                parsed_task_input = task_input
                        # If parsed_json is not a list, keep original string
                    except (json.JSONDecodeError, ValueError):
                        # Not valid JSON, treat as plain string
                        parsed_task_input = task_input
                elif isinstance(task_input, list):
                    # Already a list, extract text
                    text_parts = []
                    for item in task_input:
                        if isinstance(item, dict):
                            if item.get("type") == "text" and "text" in item:
                                text_parts.append(item["text"])
                            elif "text" in item:
                                text_parts.append(item["text"])
                    if text_parts:
                        parsed_task_input = "\n".join(text_parts)
                    else:
                        parsed_task_input = str(task_input)
                
                # Parse ext_info if provided
                kwargs = {}
                if ext_info:
                    try:
                        if isinstance(ext_info, str):
                            kwargs = json.loads(ext_info)
                        elif isinstance(ext_info, dict):
                            kwargs = ext_info
                        else:
                            kwargs = {}
                    except json.JSONDecodeError as e:
                        return json.dumps({
                            "success": False,
                            "error": f"Invalid ext_info JSON format: {str(e)}"
                        }, indent=2, ensure_ascii=False)
                
                # Get context config from agent
                context_config = agent.context_config if hasattr(agent, 'context_config') else AmniConfigFactory.create()
                
                # Create temporary context to get swarm
                temp_task_input = TaskInput(
                    user_id=user_id,
                    session_id=session_id,
                    task_id=task_id,
                    task_content="",
                    origin_user_input=""
                )
                temp_context = await ApplicationContext.from_input(
                    temp_task_input,
                    context_config=context_config
                )
                
                # Get swarm from agent
                swarm = await agent.get_swarm(temp_context)
                
                # Create executor
                executor = LocalAgentExecutor(
                    swarm=swarm,
                    context_config=context_config,
                    console=None,  # No console output for MCP
                    session_id=session_id
                )
                
                # Execute chat (LocalAgentExecutor uses chat method)
                result = await executor.chat(parsed_task_input)
                
                # Return result
                return json.dumps({
                    "success": True,
                    "agent_name": agent_name,
                    "task_id": task_id,
                    "user_id": user_id,
                    "session_id": session_id,
                    "result": result if isinstance(result, str) else str(result)
                }, indent=2, ensure_ascii=False)
                
            except Exception as e:
                import traceback
                error_msg = traceback.format_exc()
                return json.dumps({
                    "success": False,
                    "error": str(e),
                    "traceback": error_msg
                }, indent=2, ensure_ascii=False)
        
        @self.server.tool(description="Health check endpoint.")
        async def health_check() -> str:
            """
            Health check endpoint.
            
            Returns:
                JSON string with health status
            """
            try:
                agents = LocalAgentRegistry.list_agents()
                return json.dumps({
                    "status": "ok",
                    "agents_count": len(agents)
                }, indent=2)
            except Exception as e:
                return json.dumps({"status": "error", "error": str(e)}, indent=2)
    
    async def start(self) -> None:
        """Start the MCP server."""
        if self._running:
            return
        
        self._running = True
        
        # Create an event to signal server startup
        server_started = threading.Event()
        server_error = threading.Event()
        error_message = [None]
        
        if self.transport == "stdio":
            # Run in stdio mode (blocking)
            # This will run in a separate thread to avoid blocking
            def run_stdio():
                try:
                    print(f"🚀 Starting MCP server '{self.name}' in stdio mode...")
                    server_started.set()
                    self.server.run(transport="stdio")
                except Exception as e:
                    error_message[0] = str(e)
                    server_error.set()
                    print(f"❌ MCP server error: {e}")
                    import traceback
                    traceback.print_exc()
            
            thread = threading.Thread(target=run_stdio, daemon=False)
            thread.start()
            
            # Wait for server to start or error
            server_started.wait(timeout=5.0)
            if server_error.is_set():
                raise RuntimeError(f"MCP server failed to start: {error_message[0]}")
            
            # Keep the async function running by waiting indefinitely
            # The thread will keep the server running
            while thread.is_alive():
                await asyncio.sleep(1.0)
            
        elif self.transport == "sse" or self.transport == "streamable-http":
            # Run in SSE/streamable-http mode
            if not self.host or not self.port:
                raise ValueError("host and port are required for SSE/streamable-http transport")
            
            transport_name = "streamable-http" if self.transport == "streamable-http" else "SSE"
            
            def run_server():
                try:
                    print(f"🚀 Starting MCP server '{self.name}' in {transport_name} mode on {self.host}:{self.port}...")
                    if self.transport == "streamable-http":
                        print(f"📡 MCP server will be accessible at http://{self.host}:{self.port}/")
                    # streamable-http server uses SSE transport under the hood
                    # server.run() is blocking and starts uvicorn server
                    self.server.run(transport=transport_name, host=self.host, port=self.port)
                    # This line won't be reached until server stops
                except Exception as e:
                    error_message[0] = str(e)
                    server_error.set()
                    print(f"❌ MCP server error: {e}")
                    import traceback
                    traceback.print_exc()
            
            thread = threading.Thread(target=run_server, daemon=False)
            thread.start()
            
            # Wait for server to initialize - uvicorn needs time to start up
            # FastMCP's server.run() starts uvicorn which needs time to initialize
            import time
            max_wait = 10.0  # Maximum wait time in seconds
            wait_interval = 0.1  # Check interval
            waited = 0.0
            
            # Wait for server thread to be alive and check for errors
            while waited < max_wait:
                if server_error.is_set():
                    raise RuntimeError(f"MCP server failed to start: {error_message[0]}")
                if not thread.is_alive():
                    raise RuntimeError("MCP server thread exited unexpectedly")
                
                # Try to check if server is ready by attempting a connection
                # This helps ensure the server is actually accepting connections
                try:
                    import socket
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(0.5)
                    result = sock.connect_ex((self.host if self.host != "0.0.0.0" else "127.0.0.1", self.port))
                    sock.close()
                    if result == 0:
                        # Port is open, server is likely ready
                        # Wait a bit more for FastMCP internal initialization to complete
                        time.sleep(1.0)
                        break
                except Exception:
                    # Connection check failed, continue waiting
                    pass
                
                time.sleep(wait_interval)
                waited += wait_interval
            
            if waited >= max_wait:
                # Server didn't become ready in time, but thread is still alive
                # This might be okay if server is still starting, so we'll continue
                # but log a warning
                print(f"⚠️ MCP server may not be fully ready yet, but continuing...")
            else:
                # Server is ready, give FastMCP a bit more time for internal initialization
                # This helps avoid "Received request before initialization was complete" warnings
                time.sleep(0.5)
            
            # Mark server as started after initialization
            server_started.set()
            
            # Keep the async function running by waiting indefinitely
            # The thread will keep the server running
            while thread.is_alive():
                await asyncio.sleep(1.0)
        else:
            raise ValueError(
                f"Unsupported transport: {self.transport}. Use 'stdio', 'sse', or 'streamable-http'"
            )
    
    async def stop(self) -> None:
        """Stop the MCP server."""
        self._running = False
        # FastMCP doesn't have explicit stop method, 
        # but setting _running to False will prevent restart
        # The server will stop when the process exits

