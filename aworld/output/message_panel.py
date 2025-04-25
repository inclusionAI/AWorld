import logging
from typing import AsyncGenerator, List
from pydantic import BaseModel, Field
import asyncio

from datetime import datetime

from aworld.output.base import Output


class MessagePanel(BaseModel):
    """
    Message panel for handling outputs with async generator
    """
    
    panel_id: str = Field(default="", description="Unique identifier for the panel")
    visited_messages: List[Output] = Field(default_factory=list, description="List of messages/outputs")
    
    # Queue for async operations
    _queue: asyncio.Queue = None

    def __init__(self, **data):
        super().__init__(**data)
        self._queue = asyncio.Queue()

    async def add_output(self, output: Output) -> None:
        """Add an output to the panel"""
        await self._queue.put(output)

    async def get_messages_async(self) -> AsyncGenerator[Output, None]:
        """
        Get an asynchronous generator of messages
        
        Yields:
            Output objects as they arrive
        """
        # First yield existing messages
        for message in self.visited_messages:
            if message is None:  # None signals completion
                break
            yield message
            
        # Then wait for new messages
        while True:
            message = await self._queue.get()
            self.visited_messages.append(message)
            if message is None:  # None signals completion
                break
            yield message
            self._queue.task_done()

    async def mark_completed(self) -> None:
        """Mark the panel as completed"""
        await self._queue.put(None)
    @classmethod
    def create(cls, panel_id: str = "") -> "MessagePanel":
        """Create a new message panel"""
        return cls(panel_id=panel_id) 