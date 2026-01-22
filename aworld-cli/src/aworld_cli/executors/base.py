"""
Base protocol for agent executors.
"""
from typing import Protocol, Union, List


class AgentExecutor(Protocol):
    """
    Protocol for agent executors.
    
    All executor implementations must implement the chat method.
    Supports both text and multimodal (text + images) input.
    
    Example:
        class MyExecutor(AgentExecutor):
            async def chat(self, message: Union[str, list[dict]]) -> str:
                # Implementation
                return response
    """
    async def chat(self, message: Union[str, tuple[str, List[str]]]) -> str:
        """
        Execute a chat message and return the response.
        
        Args:
            message: User message to process (string or tuple of (text, image_urls) for multimodal)
                    Multimodal format: (text, [image_data_url1, image_data_url2, ...])
            
        Returns:
            Agent response as string
        """
        ...

__all__ = ["AgentExecutor"]

