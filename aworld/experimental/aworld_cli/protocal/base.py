"""
Base protocol interface for aworld-cli protocols.
Independent protocol system that doesn't depend on aworldappinfra.
"""
from typing import Protocol, Any


class AppProtocol(Protocol):
    """
    Protocol interface for application protocols (e.g., HTTP, MCP, WebSocket).
    
    All protocol implementations must implement start() and stop() methods.
    
    Example:
        class MyProtocol(AppProtocol):
            async def start(self) -> None:
                # Start the protocol server
                pass
            
            async def stop(self) -> None:
                # Stop the protocol server
                pass
    """
    
    async def start(self) -> None:
        """
        Start the protocol server.
        
        This method should initialize and start the protocol's server/service.
        It should be idempotent - calling it multiple times should be safe.
        """
        ...
    
    async def stop(self) -> None:
        """
        Stop the protocol server.
        
        This method should gracefully shut down the protocol's server/service.
        It should be idempotent - calling it multiple times should be safe.
        """
        ...
    
    def get_client(self) -> Any:
        """
        Get the client instance from this protocol (optional).
        
        Not all protocols provide clients. Only protocols that support
        direct client access should implement this method.
        
        Returns:
            Client instance (type depends on protocol implementation)
        """
        ...


__all__ = ["AppProtocol"]

