import uvicorn
from contextlib import asynccontextmanager
from typing import Union, Optional, Callable, List, Dict, Any, TYPE_CHECKING
from fastapi import FastAPI, Request, APIRouter, HTTPException, status
from pydantic import BaseModel
from .base import AppProtocol
from ..core.agent_registry import LocalAgentRegistry
from ..executors.local import LocalAgentExecutor
from aworld.core.context.amni import TaskInput, ApplicationContext
from aworld.core.context.amni.config import AmniConfigFactory
from aworld.core.agent.swarm import Swarm

if TYPE_CHECKING:
    from aworld.core.context.amni.state.common import OpenAIChatCompletionForm
else:
    from aworld.core.context.amni.state.common import OpenAIChatCompletionForm


async def handle_chat_completion_local(
    form_data: OpenAIChatCompletionForm,
    request: Request
) -> Dict[str, Any]:
    """
    Handle chat completion requests using local executor.
    
    Args:
        form_data: OpenAI chat completion form data
        request: FastAPI request object
        
    Returns:
        Chat completion response in OpenAI format
        
    Example:
        >>> from aworld.core.context.amni.state.common import OpenAIChatCompletionForm
        >>> form_data = OpenAIChatCompletionForm(
        ...     model="MyAgent",
        ...     messages=[{"role": "user", "content": "Hello"}]
        ... )
        >>> response = await handle_chat_completion_local(form_data, request)
    """
    import uuid
    import json
    
    # Get agent from registry
    agent = LocalAgentRegistry.get_agent(form_data.model)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{form_data.model}' not found"
        )
    
    # Extract user message from messages
    messages = form_data.messages or []
    if not messages:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Messages cannot be empty"
        )
    
    # Get the last user message
    user_message = ""
    for msg in reversed(messages):
        if isinstance(msg, dict):
            role = msg.get("role", "")
            content = msg.get("content", "")
        else:
            role = getattr(msg, "role", "")
            content = getattr(msg, "content", "")
        
        if role == "user":
            if isinstance(content, str):
                user_message = content
            elif isinstance(content, list):
                # Extract text from content list
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        user_message = item.get("text", "")
                        break
            break
    
    if not user_message:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No user message found in messages"
        )
    
    # Get session_id and user_id from headers or generate new ones
    session_id = request.headers.get("x-aworld-session-id") or str(uuid.uuid4().hex)
    user_id = request.headers.get("x-aworld-user-id") or "http_user"
    task_id = request.headers.get("x-aworld-task-id") or str(uuid.uuid4().hex)
    
    try:
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
            console=None,  # No console output for HTTP API
            session_id=session_id
        )
        
        # Execute chat
        response_content = await executor.chat(user_message)
        
        # Format response in OpenAI format
        response = {
            "id": f"chatcmpl-{task_id}",
            "object": "chat.completion",
            "created": int(uuid.uuid4().time_low),
            "model": form_data.model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": response_content
                    },
                    "finish_reason": "stop"
                }
            ],
            "usage": {
                "prompt_tokens": 0,  # TODO: Calculate actual token usage
                "completion_tokens": 0,  # TODO: Calculate actual token usage
                "total_tokens": 0
            }
        }
        
        return response
        
    except Exception as e:
        import traceback
        error_msg = f"Error executing chat completion: {str(e)}"
        print(f"❌ {error_msg}")
        print(traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_msg
        )


def register_chat_routes(app: FastAPI) -> bool:
    """
    Register chat completion routes on a FastAPI app.
    This function is idempotent - it will skip registration if routes already exist.
    
    Args:
        app: FastAPI application instance
        
    Returns:
        True if routes were registered, False if they already existed
        
    Example:
        >>> app = FastAPI()
        >>> register_chat_routes(app)
        True
        >>> register_chat_routes(app)  # Second call will skip
        False
    """
    # Check if routes are already registered
    for route in app.routes:
        if hasattr(route, 'path') and hasattr(route, 'methods'):
            path = route.path
            methods = route.methods or set()
            if 'POST' in methods and ('/v1/chat/completions' in path or '/chat/completions' in path):
                print(f"ℹ️ Chat routes already registered at {path}, skipping...")
                return False
    
    # Try to register routes
    try:
        from aworld.core.context.amni.state.common import OpenAIChatCompletionForm
        
        chat_router = APIRouter()
        
        @chat_router.post("/v1/chat/completions")
        @chat_router.post("/chat/completions")
        async def handle_chat_completion(form_data: OpenAIChatCompletionForm, request: Request):
            """
            Handle chat completion requests.
            This endpoint supports both OpenAI-compatible API and local agent execution.
            """
            return await handle_chat_completion_local(form_data, request)
        
        app.include_router(chat_router, tags=["chat"])
        print("✅ Chat completion routes registered successfully")
        return True
    except ImportError as e:
        # If dependencies are not available, skip chat route registration
        # This allows the app to work without chat functionality if needed
        print(f"⚠️ Failed to register chat routes (ImportError): {e}")
        import traceback
        traceback.print_exc()
        return False
    except Exception as e:
        print(f"❌ Failed to register chat routes: {e}")
        import traceback
        traceback.print_exc()
        return False


class TeamInfo(BaseModel):
    """Information about an agent."""
    name: str
    desc: Optional[str] = None
    metadata: Optional[dict] = None


@asynccontextmanager
async def default_lifespan(app: FastAPI):
    """
    Default lifespan context manager for FastAPI app.
    Logs registered agents at startup.
    """
    # Log registered agents
    agents = LocalAgentRegistry.list_agents()
    print(f"✅ Registered agents: {[a.name for a in agents]}")
    
    yield


def create_app(
    title: str = "AWorld Agent App",
    version: str = "0.1.0",
    lifespan: Optional[Callable[[FastAPI], any]] = None
) -> FastAPI:
    """
    Create a FastAPI application with standard configuration.
    
    This function creates a FastAPI app instance that should be used with AWorldApp
    and HttpProtocol for unified lifecycle management.
    
    Args:
        title: Application title
        version: Application version
        lifespan: Optional lifespan context manager. If None, uses default_lifespan.
        
    Returns:
        Configured FastAPI application instance
        
    Example:
        >>> from aworldappinfra.application import AWorldApp
        >>> from aworldappinfra.protocols.http import HttpProtocol
        >>> 
        >>> # HttpProtocol automatically calls create_app internally
        >>> aworld_app = AWorldApp()
        >>> aworld_app.add_protocol(HttpProtocol(title="My Agent App", host="0.0.0.0", port=8000))
        >>> aworld_app.run()
    """
    if lifespan is None:
        lifespan = default_lifespan
        
    app = FastAPI(title=title, version=version, lifespan=lifespan)

    @app.get("/")
    def read_root():
        return {"message": f"{title} is running!"}

    @app.get("/health")
    def health_check():
        return {"status": "ok"}

    @app.get("/agents", response_model=List[TeamInfo])
    def list_agents():
        """List available agents."""
        agents = LocalAgentRegistry.list_agents()
        return [
            TeamInfo(
                name=agent.name,
                desc=agent.desc,
                metadata=agent.metadata
            ) for agent in agents
        ]
    
    # Debug endpoint to list all routes
    @app.get("/debug/routes")
    def debug_routes():
        """Debug endpoint to list all registered routes."""
        routes = []
        for route in app.routes:
            if hasattr(route, 'path') and hasattr(route, 'methods'):
                routes.append({
                    "path": route.path,
                    "methods": list(route.methods) if route.methods else [],
                    "name": getattr(route, 'name', None)
                })
        return {"routes": routes}
    
    # Register chat completion routes
    register_chat_routes(app)
    
    return app

class HttpProtocol(AppProtocol):
    """
    HTTP Protocol adapter for AWorldApp using Uvicorn.
    Automatically creates FastAPI app if not provided, and registers chat completion routes.
    
    This protocol MUST be used with AWorldApp for unified lifecycle management.
    Do not call start()/stop() directly - use AWorldApp.run() instead.
    
    Example:
        >>> from aworldappinfra.application import AWorldApp
        >>> from aworldappinfra.protocols.http import HttpProtocol
        >>> 
        >>> # Simple usage - auto-create FastAPI app
        >>> aworld_app = AWorldApp()
        >>> aworld_app.add_protocol(HttpProtocol(title="My Agent App", host="0.0.0.0", port=8000))
        >>> aworld_app.run()
        
        >>> # Advanced usage - provide custom FastAPI app
        >>> from aworldappinfra.protocols.http import create_app
        >>> custom_app = create_app(title="Custom App", lifespan=custom_lifespan)
        >>> aworld_app.add_protocol(HttpProtocol(custom_app, host="0.0.0.0", port=8000))
        >>> aworld_app.run()
    """
    
    def __init__(
        self, 
        app_str: Optional[Union[str, FastAPI]] = None,
        host: str = "0.0.0.0", 
        port: int = 8000, 
        reload: bool = False,
        # Parameters for auto-creating FastAPI app
        title: str = "AWorld Agent App",
        version: str = "0.1.0",
        lifespan: Optional[Callable[[FastAPI], any]] = None,
        **kwargs
    ):
        """
        Initialize HTTP Protocol.
        
        Args:
            app_str: Optional. Import string for the ASGI app (e.g. 'my_module:app') or FastAPI app instance.
                     If None, a FastAPI app will be automatically created using create_app().
                     Note: For reload=True, this MUST be an import string.
            host: Bind host
            port: Bind port
            reload: Enable auto-reload
            title: Application title (used when app_str is None)
            version: Application version (used when app_str is None)
            lifespan: Optional lifespan context manager (used when app_str is None)
            **kwargs: Additional arguments passed to uvicorn.Config
            
        Note:
            This protocol should be registered with AWorldApp, not started directly.
            Use AWorldApp.add_protocol() and AWorldApp.run() for proper lifecycle management.
        """
        # Auto-create FastAPI app if not provided
        if app_str is None:
            app_str = create_app(title=title, version=version, lifespan=lifespan)
        
        # If app_str is a FastAPI instance, register chat routes automatically
        if isinstance(app_str, FastAPI):
            self._register_chat_routes(app_str)
        
        self.config = uvicorn.Config(
            app_str, 
            host=host, 
            port=port, 
            reload=reload, 
            **kwargs
        )
        self.server = uvicorn.Server(self.config)
    
    def _register_chat_routes(self, app: FastAPI) -> None:
        """
        Register chat completion routes on the FastAPI app.
        Uses the shared register_chat_routes function to avoid duplication.
        
        Args:
            app: FastAPI application instance
        """
        register_chat_routes(app)

    async def start(self) -> None:
        """Start the Uvicorn server."""
        # Uvicorn's serve() method handles signals by default, which might conflict 
        # if multiple protocols try to handle signals. 
        # For multi-protocol setups, we might need to disable uvicorn's signal handling.
        await self.server.serve()

    async def stop(self) -> None:
        """Stop the Uvicorn server."""
        self.server.should_exit = True

