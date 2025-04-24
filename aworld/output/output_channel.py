from functools import wraps
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field
from tornado.process import task_id

from aworld.events.event import Event
from aworld.events.pub_event import publish_event
from aworld.output.base import Output, MessageOutput
from aworld.output.artifact import Artifact
from aworld.output.workspace import WorkSpace
from aworld.output.message_panel import MessagePanel
from aworld.utils.common import sync_exec


class OutputRenderer(BaseModel):
    """Base class for output renderers"""
    
    async def render(self, output: Output) -> None:
        """Render the output"""
        raise NotImplementedError


class MessagePanelRenderer(OutputRenderer):
    """Renderer for message panel outputs"""
    
    panel: MessagePanel = None
    
    def __init__(self, panel_id: Optional[str] = None):
        super().__init__()
        self.panel = MessagePanel.create(panel_id)
    
    async def render(self, output: Output) -> None:
        """Render output to message panel"""
        await self.panel.add_output(output)


class WorkspaceRenderer(OutputRenderer):
    """Renderer for workspace outputs"""
    workspace: WorkSpace = Field(default=None, description="internal workspace")
    
    def __init__(self, workspace: WorkSpace):
        super().__init__()
        self.workspace = workspace
    
    async def render(self, output: Output) -> None:
        """Render output to workspace"""
        if isinstance(output, Artifact):
            # Case 1: Output is an Artifact
            await self.workspace.add_artifact(artifact=output)
        elif hasattr(output, 'parts'):
            # Case 2: Output contains Artifacts in parts
            for part in output.parts:
                if isinstance(part, Artifact):
                    await self.workspace.add_artifact(artifact=part)


class OutputChannelEvent(Event):
    event_code: str = Field(default=None, description="event_code")
    event_group: str = Field(default=None, description="event_group")
    task_id: str = Field(default=None, description="channel_id")
    type: str = Field(default=None, description="output type")
    output: Output = Field(default=None, description="output")
    renderer_target: str = Field(default=None, description="renderer_target")

    @classmethod
    def from_output(cls, channel: "OutputChannel", output: Output, renderer_target: str):
        # Create and return a new instance of OutputChannelEvent
        return cls(
            event_code="OUTPUT_CHANNEL_EVENT",
            event_group="default",
            task_id=channel.task_id,
            type=output.output_type(),
            output=output,
            renderer_target=renderer_target
        )

class OutputChannel(BaseModel):
    """Channel for managing and dispatching outputs"""

    task_id: str
    outputs: List[Output] = []
    
    # Renderers for different output types
    message_renderer: Optional[MessagePanelRenderer] = None
    workspace_renderer: Optional[WorkspaceRenderer] = None

    @classmethod
    def create(cls, task_id: str, workspace: Optional[WorkSpace] = None) -> "OutputChannel":
        """
        Create and initialize a new OutputChannel
        
        Args:
            chat_id: Unique identifier for the chat/session
            workspace: Optional workspace for handling artifacts
            
        Returns:
            Initialized OutputChannel instance
        """
        channel = cls(task_id=task_id)
        channel.setup_renderers(workspace=workspace)
        return channel

    def setup_renderers(self, workspace: Optional[WorkSpace] = None):
        """Setup output renderers"""
        self.message_renderer = MessagePanelRenderer("panel#" + self.task_id)
        if workspace:
            self.workspace_renderer = WorkspaceRenderer(workspace)

    def add_output(self, output: Output) -> None:
        """Add and dispatch an output"""
        sync_exec(self.async_add_output, output)

    async def async_add_output(self, output: Output) -> None:
        """Add and dispatch an output"""
        # Store output
        self.outputs.append(output)
        
        # Dispatch to appropriate renderer
        await self._dispatch_output(output)

    async def _dispatch_output(self, output: Output) -> None:
        """
        Dispatch output to appropriate renderer based on type
        
        Rules:
        1. If output is an Artifact -> workspace_renderer
        2. If output contains Artifacts in parts -> workspace_renderer
        3. Otherwise -> message_panel
        """
        should_dispatch_to_workspace = False
        
        # Case 1: Check if output itself is an Artifact
        if isinstance(output, Artifact):
            should_dispatch_to_workspace = True
            
        # Case 2: Check if output contains Artifacts in parts
        elif hasattr(output, 'parts'):
            for part in output.parts:
                if isinstance(part, Artifact):
                    should_dispatch_to_workspace = True
                    break
        
        # Dispatch based on the check results
        if should_dispatch_to_workspace and self.workspace_renderer:
            await self.workspace_renderer.render(output)
            # publish_event(await self.build_event(output, 'workspace'))
        elif self.message_renderer:
            # Case 3: Default to message panel
            await self.message_renderer.render(output)
            # publish_event(await self.build_event(output, 'message'))

    async def build_event(self, output: Output, render_type):
        return OutputChannelEvent.from_output(self, output,render_type )

    async def get_messages_async(self):
        return self.message_renderer.panel.get_messages_async()

    async def mark_completed(self):
        await self.message_renderer.panel.mark_completed()
