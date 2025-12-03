import asyncio
import traceback
from typing import List, Optional, Set, Any, Dict

from aworld.core.agent.base import BaseAgent, AgentResult
from aworld.core.common import ActionResult, Observation, ActionModel, Config, TaskItem
from aworld.core.context.base import Context
from aworld.events.manager import EventManager
from aworld.events import eventbus
from aworld.sandbox.base import Sandbox
from aworld.logs.util import logger
from aworld.experimental.bidi_streaming.transport import BidiEvent, BidiMessage, BidiEventType


class LiveAgent(BaseAgent[Observation, List[ActionModel]]):

    def __init__(self,
                 name: str,
                 conf: Config | None = None,
                 desc: str = None,
                 agent_id: str = None,
                 *,
                 task: Any = None,
                 tool_names: List[str] = None,
                 agent_names: List[str] = None,
                 mcp_servers: List[str] = None,
                 mcp_config: Dict[str, Any] = None,
                 feedback_tool_result: bool = True,
                 wait_tool_result: bool = False,
                 sandbox: Sandbox = None,
                 **kwargs):
        super().__init__(name, conf, desc, agent_id,
                         task=task,
                         tool_names=tool_names,
                         agent_names=agent_names,
                         mcp_servers=mcp_servers,
                         mcp_config=mcp_config,
                         feedback_tool_result=feedback_tool_result,
                         wait_tool_result=wait_tool_result,
                         sandbox=sandbox,
                         **kwargs)

        # Streaming related attributes
        self.is_streaming = False
        self._streaming_tasks: Set[asyncio.Task] = set()
        self._streaming_stop_event = asyncio.Event()
        self.context = kwargs.get("context", None)

    async def process_streaming_input(self, message: BidiMessage, context: Context = None) -> Optional[BidiMessage]:
        """Process a streaming input message for the base Agent class."""
        try:
            # Handle different types of streaming inputs
            if message.event_type == BidiEventType.INPUT_EVENT:
                # Process the input event and generate a response
                # This is a basic implementation that can be overridden by subclasses
                logger.info(f"Agent {self.id()} processing streaming input: {message.data}")

                # Create an output message
                output_message = BidiMessage(BidiEvent(event_type=BidiEventType.OUTPUT_EVENT,
                                                       data={"processed": True, "original": message.data}
                                                       ),
                                             topic=message.topic
                                             )

                return output_message

            return None
        except Exception as e:
            logger.error(f"Error processing streaming input: {traceback.format_exc()}")
            return None

    async def streaming_run(self, message: BidiMessage, context: Context = None) -> AgentResult:
        """Run the agent in streaming mode."""
        if not self.is_streaming:
            await self.start_streaming(context)
        try:
            # Process the input message
            output_message = await self.process_streaming_input(message, context)

            # If an output message is generated, send it back
            if output_message:
                await self.send_streaming_output(output_message, context)
        except Exception as e:
            logger.error(f"Error handling streaming event: {traceback.format_exc()}")

        await self._streaming_stop_event.wait()

        return AgentResult(actions=[], current_state=None)

    async def start_streaming(self, context: Context = None):
        """Start the streaming mode for the agent."""
        self.is_streaming = True
        self._streaming_stop_event.clear()
        logger.info(f"Agent {self.id()} started streaming mode")

    async def stop_streaming(self):
        """Stop the streaming mode for the agent."""
        self.is_streaming = False
        self._streaming_stop_event.set()

        # Cancel all streaming tasks
        for task in self._streaming_tasks:
            if not task.done():
                task.cancel()

        # Wait for tasks to complete
        for task in self._streaming_tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass

        self._streaming_tasks.clear()
        logger.info(f"Agent {self.id()} stopped streaming mode")

    async def send_streaming_output(self, message: BidiMessage, context: Context = None):
        """Send a streaming output message to the event bus."""
        if context and hasattr(context, 'event_manager'):
            await context.event_manager.publish_to_streaming(message)
        else:
            logger.warning(f"Event manager not available for streaming publish")

    def _add_streaming_task(self, coro):
        """Add a streaming task to the set of managed tasks."""
        task = asyncio.create_task(coro)
        self._streaming_tasks.add(task)
        task.add_done_callback(self._streaming_tasks.discard)
        return task
