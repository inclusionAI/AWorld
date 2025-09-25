import traceback
from typing import Callable, List, Dict, Any, Optional, Union
import inspect

from aworld.logs.util import logger
from aworld.output.artifact import Artifact


class WorkspaceObserver:
    """Base class for workspace observers"""

    async def on_create(self, workspace_id: str, artifact: Artifact) -> None:
        pass

    async def on_update(self, workspace_id: str, artifact: Artifact) -> None:
        pass

    async def on_delete(self, workspace_id: str, artifact: Artifact) -> None:
        pass


class Handler:
    """Handler wrapper to support both functions and class methods"""

    def __init__(self, func: Callable, instance=None, workspace_id: Optional[str] = None,
                 filters: Optional[Dict[str, Any]] = None):
        self.func = func
        self.instance = instance  # Class instance if method
        self.workspace_id = workspace_id  # Specific workspace to monitor
        self.filters = filters or {}  # Additional filters (e.g., artifact_type)

    async def __call__(self, artifact: Artifact, **kwargs) -> Any:
        """Call the handler with appropriate arguments"""
        # Check if this handler should process the artifact
        if self.workspace_id and kwargs.get('workspace_id') != self.workspace_id:
            return None

        # Check additional filters
        for key, value in self.filters.items():
            if key == 'artifact_type':
                if artifact.artifact_type != value and artifact.artifact_type.value != value:
                    return None
            elif key in artifact.metadata and artifact.metadata[key] != value:
                return None

        # Get function signature to determine what arguments it expects
        sig = inspect.signature(self.func)
        param_count = len(sig.parameters)

        # Call based on whether it's a method or function, and parameter count
        if self.instance:
            if param_count == 0:  # Just self
                return await self.func() if inspect.iscoroutinefunction(self.func) else self.func()
            elif param_count == 1:  # Self + artifact
                return await self.func(artifact) if inspect.iscoroutinefunction(self.func) else self.func(artifact)
            else:  # Self + artifact + kwargs
                return await self.func(artifact, **kwargs) if inspect.iscoroutinefunction(self.func) else self.func(
                    artifact, **kwargs)
        else:
            if param_count == 0:  # No parameters
                return await self.func() if inspect.iscoroutinefunction(self.func) else self.func()
            elif param_count == 1:  # Just artifact
                return await self.func(artifact) if inspect.iscoroutinefunction(self.func) else self.func(artifact)
            else:  # Artifact + kwargs
                return await self.func(artifact, **kwargs) if inspect.iscoroutinefunction(self.func) else self.func(
                    artifact, **kwargs)


class DecoratorBasedObserver(WorkspaceObserver):
    """Enhanced decorator-based observer implementation"""

    def __init__(self):
        self.create_handlers: List[Handler] = []
        self.update_handlers: List[Handler] = []
        self.delete_handlers: List[Handler] = []

    async def on_create(self, workspace_id: str, artifact: Artifact, **kwargs) -> List[Any]:
        """Process artifact creation with all handlers"""
        results = []
        for handler in self.create_handlers:
            try:
                result = await handler(workspace_id=workspace_id, artifact=artifact, **kwargs)
                if result is not None:
                    results.append(result)
            except Exception as e:
                print(f"Create handler failed:  error is {e}: {traceback.format_exc()}")
        return results

    async def on_update(self, artifact: Artifact, **kwargs) -> List[Any]:
        """Process artifact update with all handlers"""
        results = []
        for handler in self.update_handlers:
            try:
                result = await handler(artifact, **kwargs)
                if result is not None:
                    results.append(result)
            except Exception as e:
                print(f"Update handler failed: {e}")
        return results

    async def on_delete(self, artifact: Artifact, **kwargs) -> List[Any]:
        """Process artifact deletion with all handlers"""
        results = []
        for handler in self.delete_handlers:
            try:
                result = await handler(artifact, **kwargs)
                if result is not None:
                    results.append(result)
            except Exception as e:
                print(f"Delete handler failed: {e}")
        return results

    def register_create_handler(self, func, instance=None, workspace_id=None, filters=None):
        """Register a handler for artifact creation"""
        logger.info(f"[📂WORKSPACE]✨ Registering create handler for {func}")
        self.create_handlers.append(Handler(func, instance, workspace_id, filters))
        return func

    def un_register_create_handler(self, func, instance=None, workspace_id=None):
        """Register a handler for artifact creation"""
        logger.info(f"[📂WORKSPACE] UnRegister create handler for {func}")
        for handler in self.create_handlers:
            if handler.func == func:
                self.create_handlers.remove(handler)
                logger.info(f"[📂WORKSPACE] UnRegister create handler for {func} success")

    def register_update_handler(self, func, instance=None, workspace_id=None, filters=None):
        """Register a handler for artifact update"""
        logger.info(f"[📂WORKSPACE]✨ Registering update handler for {func}")
        self.update_handlers.append(Handler(func, instance, workspace_id, filters))
        return func

    def register_delete_handler(self, func, instance=None, workspace_id=None, filters=None):
        """Register a handler for artifact deletion"""
        logger.info(f"[📂WORKSPACE]✨ Registering delete handler for {func}")
        self.delete_handlers.append(Handler(func, instance, workspace_id, filters))
        return func


# Global observer instance
_observer = DecoratorBasedObserver()


def get_observer() -> DecoratorBasedObserver:
    """Get the global observer instance"""
    return _observer


def on_artifact_create(func=None, workspace_id=None, filters=None):
    """
    Decorator for artifact creation handlers
    
    Can be used as a simple decorator (@on_artifact_create) or with parameters:
    @on_artifact_create(workspace_id='abc', filters={'artifact_type': 'WEB_PAGES'})
    """
    if func is None:
        # Called with parameters
        def decorator(f):
            return _observer.register_create_handler(f, None, workspace_id, filters)

        return decorator

    # Called as simple decorator
    return _observer.register_create_handler(func)


def on_artifact_update(func=None, workspace_id=None, filters=None):
    """
    Decorator for artifact update handlers
    
    Can be used as a simple decorator (@on_artifact_update) or with parameters:
    @on_artifact_update(workspace_id='abc', filters={'artifact_type': 'WEB_PAGES'})
    """
    if func is None:
        # Called with parameters
        def decorator(f):
            return _observer.register_update_handler(f, None, workspace_id, filters)

        return decorator

    # Called as simple decorator
    return _observer.register_update_handler(func)


def on_artifact_delete(func=None, workspace_id=None, filters=None):
    """
    Decorator for artifact deletion handlers
    
    Can be used as a simple decorator (@on_artifact_delete) or with parameters:
    @on_artifact_delete(workspace_id='abc', filters={'artifact_type': 'WEB_PAGES'})
    """
    if func is None:
        # Called with parameters
        def decorator(f):
            return _observer.register_delete_handler(f, None, workspace_id, filters)

        return decorator

    # Called as simple decorator
    return _observer.register_delete_handler(func)
