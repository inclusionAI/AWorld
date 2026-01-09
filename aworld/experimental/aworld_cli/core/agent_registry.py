"""
Local agent registry for aworld-cli.
Independent registry system that doesn't depend on aworldappinfra.
"""
import inspect
from typing import Dict, Iterable, Callable, Optional, List, Union, ClassVar, Awaitable, TYPE_CHECKING
from threading import RLock

from pydantic import BaseModel, PrivateAttr, Field

from aworld.core.agent.swarm import Swarm
from aworld.core.context.amni import AmniContextConfig, AmniConfigFactory
from aworld.core.context.base import Context

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
        
        Returns:
            The Swarm instance for this agent.
            
        Example:
            >>> agent = LocalAgent(swarm=lambda: Swarm(agent1, agent2))
            >>> swarm = await agent.get_swarm()  # Swarm is created here
            
            >>> async def build_swarm(ctx: Context) -> Swarm:
            ...     return Swarm(agent1, agent2)
            >>> agent = LocalAgent(swarm=build_swarm)
            >>> swarm = await agent.get_swarm(context)
        """
        if isinstance(self.swarm, Swarm):
            return self.swarm
        if callable(self.swarm):
            swarm_func = self.swarm
            if inspect.iscoroutinefunction(swarm_func):
                # Async callable
                sig = inspect.signature(swarm_func)
                param_count = len(sig.parameters)
                
                # Try to call with context if function has parameters
                if param_count > 0:
                    try:
                        return await swarm_func(context)
                    except TypeError as e:
                        # If context is None and function requires it, try without arguments
                        if "required" in str(e).lower() or "missing" in str(e).lower():
                            if context is None:
                                try:
                                    return await swarm_func()
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
                        return await swarm_func()
                    except Exception as e:
                        raise
            else:
                # Sync callable
                sig = inspect.signature(swarm_func)
                param_count = len(sig.parameters)
                
                # Try to call with context if function has parameters
                if param_count > 0:
                    try:
                        return swarm_func(context)
                    except TypeError as e:
                        # If context is None and function requires it, try without arguments
                        if "required" in str(e).lower() or "missing" in str(e).lower():
                            if context is None:
                                try:
                                    return swarm_func()
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
                        return swarm_func()
                    except Exception as e:
                        raise
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
    def get_agent(cls, agent_id: str) -> Optional[LocalAgent]:
        """Get an agent by agent_id using the singleton instance.

        This is a static class method that delegates to the singleton instance's get method.

        Args:
            agent_id: The agent identifier (name) to query.

        Returns:
            The LocalAgent instance if exists, else None.

        Example:
            >>> agent = LocalAgentRegistry.get_agent("demo")
            >>> if agent:
            ...     print(agent.name)
        """
        return cls.get_instance().get(agent_id)

    def register_agent(self, agent: LocalAgent) -> None:
        """Register a LocalAgent, requiring a unique non-empty name.

        Args:
            agent: The LocalAgent instance to register.

        Returns:
            None

        Raises:
            ValueError: If name is empty or already exists.
        """
        if not agent or not agent.name:
            raise ValueError("LocalAgent.name is required for registration")
        with self._lock:
            if agent.name in self._agents:
                # logger.warning(f"LocalAgent '{agent.name}' is already registered")
                return
            self._agents[agent.name] = agent

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

    def get(self, name: str) -> Optional[LocalAgent]:
        """Get an agent by name.

        Args:
            name: Agent name.

        Returns:
            The LocalAgent instance if exists, else None.
        """
        if not name:
            return None
        with self._lock:
            return self._agents.get(name)

    def list(self) -> List[LocalAgent]:
        """List all agents.

        Returns:
            A list of LocalAgent instances.
        """
        with self._lock:
            return list(self._agents.values())

    def list_names(self) -> List[str]:
        """List all agent names.

        Returns:
            A list of registered agent names.
        """
        with self._lock:
            return list(self._agents.keys())

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
    metadata: Optional[dict] = None
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
        if inspect.iscoroutinefunction(func):
            async def async_wrapper(*args, **kwargs):
                result = await func(*args, **kwargs)
                if isinstance(result, LocalAgent):
                    LocalAgentRegistry.register(result)
                return result
            return async_wrapper
        else:
            def sync_wrapper(*args, **kwargs):
                result = func(*args, **kwargs)
                if isinstance(result, LocalAgent):
                    LocalAgentRegistry.register(result)
                return result
            return sync_wrapper
    
    # Parameterized decorator: @agent(name="...", ...)
    def decorator(func: Callable) -> Callable:
        if not name:
            raise ValueError("name is required when using @agent decorator with parameters")
        
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
            metadata=metadata or {"creator": "aworld-cli", "version": "1.0.0"}
        )
        LocalAgentRegistry.register(local_agent)
        
        # Return the wrapper function
        return swarm_wrapper
    
    return decorator


__all__ = ["LocalAgent", "LocalAgentRegistry", "agent"]

