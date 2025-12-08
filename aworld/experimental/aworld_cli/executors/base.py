"""
Base protocol for agent executors.
"""
from typing import Protocol


class AgentExecutor(Protocol):
    """
    Protocol for agent executors.
    
    All executor implementations must implement the chat method.
    
    Example:
        class MyExecutor(AgentExecutor):
            async def chat(self, message: str) -> str:
                # Implementation
                return response
    """
    async def chat(self, message: str) -> str:
        """
        Execute a chat message and return the response.
        
        Args:
            message: User message to process
            
        Returns:
            Agent response as string
        """
        ...

__all__ = ["AgentExecutor"]

