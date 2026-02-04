"""
Local agent registry for aworld-cli.
Independent registry system that doesn't depend on aworldappinfra.
"""
import inspect
from threading import RLock
from typing import Dict, Iterable, Callable, Optional, List, Union, ClassVar, Awaitable, TYPE_CHECKING

from pydantic import BaseModel, PrivateAttr, Field

from aworld.core.agent.swarm import Swarm
from aworld.core.context.amni import AmniContextConfig, AmniConfigFactory
from aworld.core.context.base import Context
from aworld.logs.util import logger

if TYPE_CHECKING:
    from aworld.core.agent.base import BaseAgent
    from aworld.agents.llm_agent import Agent
else:
    # Try to import Agent for runtime type checking
    try:
        from aworld.core.agent.base import BaseAgent
        from aworld.agents.llm_agent import Agent
    except ImportError:
        BaseAgent = None
        Agent = None


class LocalAgent(BaseModel):
    """Represents a local agent configuration with swarm and context components.
    
    A LocalAgent defines a complete agent setup including:
    - The swarm (agent group) that executes tasks
    - Context configuration for managing application state
    - Metadata for additional agent information
    
    The swarm can be provided as instances or callables/factories for lazy initialization.
    
    Example:
        >>> def build_swarm() -> Swarm:
        ...     return Swarm(agent1, agent2)
        >>> agent = LocalAgent(
        ...     name="DemoAgent",
        ...     desc="A demo agent",
        ...     swarm=build_swarm,
        ...     context_config=AmniConfigFactory.create(),
        ...     metadata={"version": "1.0.0"}
        ... )
        >>> swarm = await agent.get_swarm()
    """
    
    name: str = None
    """Agent name identifier. Required for registration."""
    
    desc: str = None
    """Agent description or purpose."""
    
    path: Optional[str] = Field(default=None, description="File path where the agent is defined")
    """File path where the @agent decorator is located.
    
    This is automatically set by the @agent decorator based on the source file location
    where the agent is defined. Used for tracking the source file of the agent.
    """
    
    swarm: Union[Swarm, Callable[..., Swarm], Callable[..., Awaitable[Swarm]]] = Field(
        default=None, 
        description="Swarm instance or callable", 
        exclude=True
    )
    """Swarm instance or callable that returns a Swarm. 
    
    Can be:
    - A Swarm instance
    - A synchronous callable that takes Context and returns Swarm
    - An async callable that takes Context and returns Awaitable[Swarm]
    
    If callable, will be invoked when get_swarm() is called to enable lazy initialization.
    """
    
    context_config: AmniContextConfig = Field(
        default_factory=AmniContextConfig, 
        description="Context config", 
        exclude=True
    )
    """Configuration for application context management."""

    metadata: dict = None
    """Additional metadata dictionary for agent information (e.g., version, creator, etc.)."""
    
    hooks: Optional[List[str]] = Field(default=None, description="Executor hooks configuration")
    """Executor hooks configuration.
    
    List of hook names (registered with HookFactory). Each hook class must:
    1. Inherit from ExecutorHook (or its subclasses like PostBuildContextHook)
    2. Implement the point() method to return its hook point
    3. Be registered with HookFactory using @HookFactory.register(name="HookName")
    
    Hooks are automatically grouped by their hook point (returned by hook.point() method).
    
    Hook points available:
    - pre_input_parse: Before parsing user input
    - post_input_parse: After parsing user input (e.g., image processing)
    - pre_build_context: Before building context
    - post_build_context: After building context
    - pre_build_task: Before building Task
    - post_build_task: After building Task
    - pre_run_task: Before running task
    - post_run_task: After running task
    - on_task_error: When task execution fails
    
    Example:
        >>> agent = LocalAgent(
        ...     name="MyAgent",
        ...     hooks=["ImageParseHook"],  # Hook name registered with HookFactory
        ...     ...
        ... )
    """
    
    register_dir: Optional[str] = Field(default=None, description="Directory where agent is registered")
    """Directory path where the agent is registered from.
    
    This is automatically set by the @agent decorator based on the file location
    where the agent is defined. Used for filtering agents by source directory.
    """

    async def get_swarm(self, context: Context = None) -> Swarm:
        """Get the Swarm instance, initializing if necessary.
        
        Supports multiple swarm initialization patterns:
        - Direct Swarm instance: returns immediately
        - Async callable: awaits the callable with context (if needed)
        - Sync callable: calls the callable with context (if needed)
        
        The method intelligently detects whether the callable requires context:
        - If the function has parameters, it will try to pass context
        - If the function has no parameters, it will be called without arguments
        - If context is None and function requires it, it will still be passed (may cause error)
        
        The created Swarm instance is cached in self.swarm after first initialization,
        so subsequent calls will return the cached instance directly.
        
        Returns:
            The Swarm instance for this agent.
            
        Example:
            >>> agent = LocalAgent(swarm=lambda: Swarm(agent1, agent2))
            >>> swarm = await agent.get_swarm()  # Swarm is created here and cached
            >>> swarm2 = await agent.get_swarm()  # Returns cached swarm
            
            >>> async def build_swarm(ctx: Context) -> Swarm:
            ...     return Swarm(agent1, agent2)
            >>> agent = LocalAgent(swarm=build_swarm)
            >>> swarm = await agent.get_swarm(context)  # Created and cached
        """
        if isinstance(self.swarm, Swarm):
            logger.info(f"Using existing swarm for agent {self.name}")
            return self.swarm
        if callable(self.swarm):
            logger.info(f"Initializing swarm for agent {self.name}")
            swarm_func = self.swarm
            swarm_instance = None
            
            if inspect.iscoroutinefunction(swarm_func):
                # Async callable
                sig = inspect.signature(swarm_func)
                param_count = len(sig.parameters)
                
                # Try to call with context if function has parameters
                if param_count > 0:
                    try:
                        swarm_instance = await swarm_func(context)
                    except TypeError as e:
                        # If context is None and function requires it, try without arguments
                        if "required" in str(e).lower() or "missing" in str(e).lower():
                            if context is None:
                                try:
                                    swarm_instance = await swarm_func()
                                except Exception as fallback_error:
                                    raise
                            else:
                                raise
                        else:
                            raise
                    except Exception as e:
                        raise
                else:
                    # Function has no parameters, call without arguments
                    try:
                        swarm_instance = await swarm_func()
                    except Exception as e:
                        raise
            else:
                # Sync callable
                sig = inspect.signature(swarm_func)
                param_count = len(sig.parameters)
                
                # Try to call with context if function has parameters
                if param_count > 0:
                    try:
                        swarm_instance = swarm_func(context)
                    except TypeError as e:
                        # If context is None and function requires it, try without arguments
                        if "required" in str(e).lower() or "missing" in str(e).lower():
                            if context is None:
                                try:
                                    swarm_instance = swarm_func()
                                except Exception as fallback_error:
                                    raise
                            else:
                                raise
                        else:
                            raise
                    except Exception as e:
                        raise
                else:
                    # Function has no parameters, call without arguments
                    try:
                        swarm_instance = swarm_func()
                    except Exception as e:
                        raise
            
            # Cache the created swarm instance
            if swarm_instance is not None:
                self.swarm = swarm_instance
                logger.info(f"Cached swarm instance for agent {self.name}")
                return swarm_instance
        
        return self.swarm

    model_config = {"arbitrary_types_allowed": True}


class LocalAgentRegistry(BaseModel):
    """
    A threadsafe registry for managing LocalAgent objects.

    This registry supports register, unregister, fetch and list operations with
    concurrency safety via an internal re-entrant lock.

    Example:
        >>> registry = LocalAgentRegistry()
        >>> agent = LocalAgent(name="demo", desc="Demo agent")
        >>> registry.register(agent)
        >>> assert registry.exists("demo") is True
        >>> got = registry.get("demo")
        >>> names = registry.list_names()
        >>> registry.unregister("demo")
        >>> registry.clear()
        
    Static method example:
        >>> agent = LocalAgent(name="demo", desc="Demo agent")
        >>> LocalAgentRegistry.register(agent)
        >>> assert LocalAgentRegistry.exists("demo") is True
    """

    _lock: RLock = PrivateAttr(default_factory=RLock)
    _agents: Dict[str, LocalAgent] = PrivateAttr(default_factory=dict)
    
    # Class-level singleton instance (use ClassVar to exclude from Pydantic model fields)
    _instance: ClassVar[Optional['LocalAgentRegistry']] = None
    _instance_lock: ClassVar[RLock] = RLock()

    @classmethod
    def get_instance(cls) -> 'LocalAgentRegistry':
        """Get the singleton instance of LocalAgentRegistry.

        Returns:
            The singleton LocalAgentRegistry instance.

        Example:
            >>> registry = LocalAgentRegistry.get_instance()
            >>> agent = LocalAgent(name="demo")
            >>> registry.register(agent)
        """
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def register(cls, agent: LocalAgent) -> None:
        """Register a LocalAgent using the singleton instance.

        This is a static class method that delegates to the singleton instance's register method.

        Args:
            agent: The LocalAgent instance to register.

        Returns:
            None

        Raises:
            ValueError: If name is empty or already exists.

        Example:
            >>> agent = LocalAgent(name="demo", desc="Demo agent")
            >>> LocalAgentRegistry.register(agent)
        """
        cls.get_instance().register_agent(agent)

    @classmethod
    def list_agents(cls) -> List[LocalAgent]:
        """List all agents using the singleton instance.

        This is a static class method that delegates to the singleton instance's list method.

        Returns:
            A list of LocalAgent instances.

        Example:
            >>> agents = LocalAgentRegistry.list_agents()
            >>> for agent in agents:
            ...     print(agent.name)
        """
        return cls.get_instance().list()

    @classmethod
    def list_agent_names(cls) -> List[str]:
        """List all agent names using the singleton instance.

        Returns:
            A list of registered agent names.
        """
        return cls.get_instance().list_names()

    @classmethod
    def get_agent(cls, agent_id: str, version: Optional[str] = None) -> Optional[LocalAgent]:
        """Get an agent by agent_id using the singleton instance.

        This is a static class method that delegates to the singleton instance's get method.

        Args:
            agent_id: The agent identifier (name) to query.
            version: Optional version string (e.g., "v0", "v1"). If not provided, returns the latest version.

        Returns:
            The LocalAgent instance if exists, else None.

        Example:
            >>> agent = LocalAgentRegistry.get_agent("demo")
            >>> if agent:
            ...     print(agent.name)
            >>> # Get specific version
            >>> agent_v1 = LocalAgentRegistry.get_agent("demo", version="v1")
        """
        return cls.get_instance().get(agent_id, version)

    def register_agent(self, agent: LocalAgent) -> None:
        """Register a LocalAgent, requiring a unique non-empty name.
        Supports multi-version registration: agents with the same name but different versions can coexist.

        Args:
            agent: The LocalAgent instance to register.

        Returns:
            None

        Raises:
            ValueError: If name is empty.
        """
        if not agent or not agent.name:
            raise ValueError("LocalAgent.name is required for registration")
        
        # Extract version from metadata or path
        version = None
        if agent.metadata and "version" in agent.metadata:
            version = agent.metadata["version"]
        elif agent.register_dir:
            # Try to extract version from directory path (e.g., {name}_v{N}/)
            import re
            import os
            dir_name = os.path.basename(agent.register_dir.rstrip('/'))
            match = re.match(r'^(.+)_v(\d+)$', dir_name)
            if match:
                version = f"v{match.group(2)}"
        
        # Use name:version as key for multi-version support, or just name if no version
        agent_key = f"{agent.name}:{version}" if version else agent.name
        
        with self._lock:
            # Allow multiple versions of the same agent name
            if agent_key in self._agents:
                logger.warning(f"LocalAgent '{agent_key}' is already registered, updating...")
            self._agents[agent_key] = agent

    def upsert(self, agent: LocalAgent) -> None:
        """Insert or update a LocalAgent.

        Args:
            agent: The LocalAgent instance to insert or update.

        Returns:
            None
        """
        if not agent or not agent.name:
            raise ValueError("LocalAgent.name is required for upsert")
        with self._lock:
            self._agents[agent.name] = agent

    def register_many(self, agents: Iterable[LocalAgent]) -> None:
        """Register multiple agents atomically.

        Args:
            agents: Iterable of LocalAgent to register.

        Returns:
            None

        Raises:
            ValueError: If any agent name is invalid or duplicated in registry/batch.

        Example:
            >>> registry = LocalAgentRegistry()
            >>> registry.register_many([LocalAgent(name="a"), LocalAgent(name="b")])
        """
        to_add: Dict[str, LocalAgent] = {}
        for a in agents:
            if not a or not a.name:
                raise ValueError("All LocalAgent items must have non-empty name")
            if a.name in to_add:
                raise ValueError(f"Duplicated agent name in batch: {a.name}")
            to_add[a.name] = a
        with self._lock:
            conflict = [name for name in to_add if name in self._agents]
            if conflict:
                raise ValueError(f"Agent(s) already registered: {', '.join(conflict)}")
            self._agents.update(to_add)

    def unregister(self, name: str) -> bool:
        """Unregister an agent by name.

        Args:
            name: Agent name.

        Returns:
            True if removed, False if not present.
        """
        if not name:
            return False
        with self._lock:
            return self._agents.pop(name, None) is not None

    def get(self, name: str, version: Optional[str] = None) -> Optional[LocalAgent]:
        """Get an agent by name, optionally with version.

        Args:
            name: Agent name.
            version: Optional version string (e.g., "v0", "v1"). If not provided, returns the latest version.

        Returns:
            The LocalAgent instance if exists, else None.
        """
        if not name:
            return None
        with self._lock:
            # If version is specified, try exact match first
            if version:
                agent_key = f"{name}:{version}"
                if agent_key in self._agents:
                    return self._agents[agent_key]
            
            # Try direct name match (for backward compatibility)
            if name in self._agents:
                return self._agents[name]
            
            # Find all agents with this name (multi-version support)
            matching_agents = []
            for key, agent in self._agents.items():
                if key == name or key.startswith(f"{name}:"):
                    matching_agents.append((key, agent))
            
            if not matching_agents:
                return None
            
            # If only one match, return it
            if len(matching_agents) == 1:
                return matching_agents[0][1]
            
            # Multiple versions found, return the latest one
            # Extract version numbers and sort
            def extract_version_from_key(key: str) -> int:
                if ':' in key:
                    version_str = key.split(':', 1)[1]
                    # Extract version number from "v0", "v1", etc.
                    import re
                    match = re.match(r'v(\d+)', version_str)
                    return int(match.group(1)) if match else 0
                return 0  # No version suffix means v0
            
            # Sort by version number (descending) and return the latest
            matching_agents.sort(key=lambda x: extract_version_from_key(x[0]), reverse=True)
            return matching_agents[0][1]

    def list(self) -> List[LocalAgent]:
        """List all agents.

        Returns:
            A list of LocalAgent instances.
        """
        with self._lock:
            return list(self._agents.values())

    def list_names(self) -> List[str]:
        """List all agent names (deduplicated, without version suffixes).

        Returns:
            A list of registered agent names (unique, without version information).
        """
        with self._lock:
            names = set()
            for key in self._agents.keys():
                # Extract name from key (remove version suffix if present)
                if ':' in key:
                    name = key.split(':', 1)[0]
                else:
                    name = key
                names.add(name)
            return sorted(list(names))

    def exists(self, name: str) -> bool:
        """Check if an agent exists by name.

        Args:
            name: Agent name.

        Returns:
            True if exists, else False.
        """
        if not name:
            return False
        with self._lock:
            return name in self._agents

    def clear(self) -> None:
        """Clear all registered agents.

        Returns:
            None
        """
        with self._lock:
            self._agents.clear()


def agent(
    name: Optional[str] = None,
    desc: Optional[str] = None,
    context_config: Optional[AmniContextConfig] = None,
    metadata: Optional[dict] = None,
    hooks: Optional[List[str]] = None,
    register_dir: Optional[str] = None
) -> Callable:
    """Decorator for registering LocalAgent instances.
    
    This decorator provides a convenient way to register LocalAgent instances.
    It supports two usage patterns:
    
    1. Parameterized decorator - decorate a build_swarm function:
        >>> @agent(
        ...     name="MyAgent",
        ...     desc="My agent description",
        ...     context_config=AmniConfigFactory.create(...),
        ...     metadata={"version": "1.0.0"}
        ... )
        >>> def build_my_swarm() -> Swarm:
        ...     return Swarm(...)
        
        If the function returns an Agent instance instead of Swarm, it will be
        automatically wrapped as Swarm(agent):
        >>> @agent(name="MyAgent", desc="My agent")
        >>> def build_agent() -> Agent:
        ...     return MyAgent(...)  # Returns Agent, will be wrapped as Swarm(agent)
    
    2. Function decorator - decorate a function that returns LocalAgent:
        >>> @agent
        >>> def my_agent() -> LocalAgent:
        ...     return LocalAgent(
        ...         name="MyAgent",
        ...         desc="My agent description",
        ...         swarm=build_my_swarm,
        ...         context_config=AmniConfigFactory.create(...),
        ...         metadata={"version": "1.0.0"}
        ...     )
    
    Args:
        name: Agent name identifier. Required when decorating build_swarm function.
        desc: Agent description or purpose.
        context_config: Configuration for application context management.
        metadata: Additional metadata dictionary for agent information.
        hooks: Optional list of hook names (registered with HookFactory).
        register_dir: Optional directory path where agent is registered. If not provided,
                     will be automatically detected from the function's source file location.
    
    Returns:
        A decorator function that registers the LocalAgent.
    
    Example:
        >>> from aworld.core.context.amni.config import AmniConfigLevel
        >>> 
        >>> @agent(
        ...     name="MyAgent",
        ...     desc="My agent description",
        ...     context_config=AmniConfigFactory.create(
        ...         AmniConfigLevel.NAVIGATOR,
        ...         debug_mode=True
        ...     ),
        ...     metadata={"version": "1.0.0"}
        ... )
        >>> def build_my_swarm() -> Swarm:
        ...     return Swarm(...)
        
        Example with Agent return type (automatically wrapped):
        >>> @agent(name="SingleAgent", desc="Single agent")
        >>> def build_my_agent() -> Agent:
        ...     return MyAgent(...)  # Automatically wrapped as Swarm(agent)
    """
    # If called without parentheses: @agent
    if callable(name):
        func = name
        # Function decorator: @agent (without parameters)
        # Try to get register_dir and path from function's source file
        func_register_dir = None
        func_path = None
        try:
            source_file = inspect.getsourcefile(func)
            if source_file:
                from pathlib import Path
                func_register_dir = str(Path(source_file).parent.resolve())
                func_path = str(Path(source_file).resolve())
        except Exception:
            pass
        
        if inspect.iscoroutinefunction(func):
            async def async_wrapper(*args, **kwargs):
                result = await func(*args, **kwargs)
                if isinstance(result, LocalAgent):
                    # Set register_dir if not already set
                    if not result.register_dir and func_register_dir:
                        result.register_dir = func_register_dir
                    # Set path if not already set
                    if not result.path and func_path:
                        result.path = func_path
                    logger.info(f"Registering agent: {result.name}")
                    LocalAgentRegistry.register(result)
                return result
            return async_wrapper
        else:
            def sync_wrapper(*args, **kwargs):
                result = func(*args, **kwargs)
                if isinstance(result, LocalAgent):
                    # Set register_dir if not already set
                    if not result.register_dir and func_register_dir:
                        result.register_dir = func_register_dir
                    # Set path if not already set
                    if not result.path and func_path:
                        result.path = func_path
                    logger.info(f"Registering agent: {result.name}")
                    LocalAgentRegistry.register(result)
                return result
            return sync_wrapper
    
    # Parameterized decorator: @agent(name="...", ...)
    def decorator(func: Callable) -> Callable:
        if not name:
            raise ValueError("name is required when using @agent decorator with parameters")
        
        # Get register_dir and path from function's source file if not explicitly provided
        func_register_dir = register_dir
        func_path = None
        if not func_register_dir:
            try:
                source_file = inspect.getsourcefile(func)
                if source_file:
                    from pathlib import Path
                    func_register_dir = str(Path(source_file).parent.resolve())
                    func_path = str(Path(source_file).resolve())
            except Exception:
                pass
        else:
            # If register_dir is provided, try to get path from function's source file
            try:
                source_file = inspect.getsourcefile(func)
                if source_file:
                    from pathlib import Path
                    func_path = str(Path(source_file).resolve())
            except Exception:
                pass
        
        # Create a wrapper function that checks return type and wraps Agent to Swarm if needed
        # Preserve the original function signature using functools.wraps
        import functools
        
        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def swarm_wrapper(*args, **kwargs):
                result = await func(*args, **kwargs)
                # Check if result is an Agent instance, wrap it in Swarm if so
                if Agent is not None and isinstance(result, Agent):
                    return Swarm(result)
                return result
        else:
            @functools.wraps(func)
            def swarm_wrapper(*args, **kwargs):
                result = func(*args, **kwargs)
                # Check if result is an Agent instance, wrap it in Swarm if so
                if Agent is not None and isinstance(result, BaseAgent):
                    return Swarm(result)
                return result
        
        # Create and register LocalAgent immediately when decorator is applied
        # Use the wrapper function as swarm factory
        local_agent = LocalAgent(
            name=name,
            desc=desc,
            swarm=swarm_wrapper,  # Use the wrapper function as swarm factory
            context_config=context_config or AmniConfigFactory.create(),
            metadata=metadata or {"creator": "aworld-cli", "version": "1.0.0"},
            hooks=hooks,
            register_dir=func_register_dir,
            path=func_path
        )

        logger.info(f"Registering agent: {local_agent.name}")

        LocalAgentRegistry.register(local_agent)
        
        # Return the wrapper function
        return swarm_wrapper
    
    return decorator


__all__ = ["LocalAgent", "LocalAgentRegistry", "agent"]

