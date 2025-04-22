from typing import Callable, List

from aworld.output.artifact import Artifact


class WorkspaceObserver:
    """Base class for workspace observers"""
    def on_create(self, artifact: Artifact) -> None:
        pass
        
    def on_update(self, artifact: Artifact) -> None:
        pass
        
    def on_delete(self, artifact: Artifact) -> None:
        pass

class DecoratorBasedObserver(WorkspaceObserver):
    """Decorator-based observer implementation"""
    def __init__(self):
        self.create_handlers: List[Callable[[Artifact], None]] = []
        self.update_handlers: List[Callable[[Artifact], None]] = []
        self.delete_handlers: List[Callable[[Artifact], None]] = []
    
    def on_create(self, artifact: Artifact) -> None:
        for handler in self.create_handlers:
            try:
                handler(artifact)
            except Exception as e:
                print(f"Create handler failed: {e}")
    
    def on_update(self, artifact: Artifact) -> None:
        for handler in self.update_handlers:
            try:
                handler(artifact)
            except Exception as e:
                print(f"Update handler failed: {e}")
    
    def on_delete(self, artifact: Artifact) -> None:
        for handler in self.delete_handlers:
            try:
                handler(artifact)
            except Exception as e:
                print(f"Delete handler failed: {e}")

# Global observer instance
_observer = DecoratorBasedObserver()

def get_observer() -> DecoratorBasedObserver:
    """Get the global observer instance"""
    return _observer

def on_artifact_create(func: Callable[[Artifact], None]) -> Callable[[Artifact], None]:
    """Decorator for artifact creation handlers"""
    _observer.create_handlers.append(func)
    return func

def on_artifact_update(func: Callable[[Artifact], None]) -> Callable[[Artifact], None]:
    """Decorator for artifact update handlers"""
    _observer.update_handlers.append(func)
    return func

def on_artifact_delete(func: Callable[[Artifact], None]) -> Callable[[Artifact], None]:
    """Decorator for artifact deletion handlers"""
    _observer.delete_handlers.append(func)
    return func



