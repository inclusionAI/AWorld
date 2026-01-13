"""
Base protocol for agent executors.
"""
import uuid
from datetime import datetime
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

    def _generate_session_id(self) -> str:
        """
        Generate a new session ID.

        Returns:
            A new session ID string

        Example:
            >>> executor = LocalAgentExecutor(swarm)
            >>> new_id = executor._generate_session_id()
        """
        return f"session_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"

__all__ = ["AgentExecutor"]

