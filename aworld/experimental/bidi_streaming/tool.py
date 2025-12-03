# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
import traceback
from typing import Dict, List, Any, Set, Optional

from aworld.core.common import Observation, ActionModel, ActionResult
from aworld.core.context.base import Context
from aworld.core.event.base import Message
from aworld.core.tool.base import AsyncBaseTool
from aworld.events import eventbus
from aworld.experimental.bidi_streaming.transport import BidiMessage, BidiEventType
from aworld.logs.util import logger


class StreamingTool(AsyncBaseTool[Observation, List[ActionModel]]):
    """Base class for tools that support streaming input and output processing.

    This class extends AsyncBaseTool to provide streaming capabilities, allowing tools
    to continuously receive input from and send output to the streaming_eventbus.
    """

    def __init__(self, conf: Dict[str, Any], **kwargs) -> None:
        super().__init__(conf, **kwargs)

        # Streaming related attributes
        self.is_streaming = False
        self._streaming_tasks: Set[asyncio.Task] = set()
        self._streaming_stop_event = asyncio.Event()
        self._streaming_buffer: List[Dict[str, Any]] = []

        # Context for streaming operations
        self.context = kwargs.get("context", None)

        # Buffer configuration
        self.buffer_size = conf.get("buffer_size", 100)

    async def start_streaming(self, context: Context = None):
        """Start the streaming mode for the tool."""
        self.is_streaming = True
        self._streaming_stop_event.clear()
        self._streaming_buffer.clear()

        # Subscribe to streaming events
        if hasattr(eventbus, 'subscribe_to_streaming'):
            await eventbus.subscribe_to_streaming(self._handle_streaming_event, f"tool_{self.name()}")

        # Start the streaming processing task
        self._add_streaming_task(self._streaming_processing_loop())

        logger.info(f"Tool {self.name()} started streaming mode")

    async def stop_streaming(self):
        """Stop the streaming mode for the tool."""
        self.is_streaming = False
        self._streaming_stop_event.set()

        # Unsubscribe from streaming events
        if hasattr(eventbus, 'unregister_streaming_handler'):
            await eventbus.unregister_streaming_handler(self._handle_streaming_event)

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
        self._streaming_buffer.clear()

        logger.info(f"Tool {self.name()} stopped streaming mode")

    async def process_streaming_input(self, message: BidiMessage, context: Context = None) -> Optional[BidiMessage]:
        """Process a streaming input message and potentially generate an output message.

        This method should be implemented by subclasses to handle specific streaming input processing.

        Args:
            message: The streaming message to process
            context: Optional context for processing

        Returns:
            An optional BidiMessage to be sent as output
        """
        # Base implementation - subclasses should override this
        return None

    async def _handle_streaming_event(self, message: BidiMessage, context: Context = None):
        """Handle incoming streaming events."""
        try:
            # Only process INPUT_EVENT types
            if message.event_type == BidiEventType.INPUT_EVENT:
                # Add to buffer if buffer is enabled
                if self.buffer_size > 0:
                    self._add_to_buffer(message.data)

                # Process the input and get output
                output_message = await self.process_streaming_input(message, context)

                # If output is generated, send it to streaming eventbus
                if output_message:
                    await self._send_streaming_output(output_message, context)
        except Exception as e:
            logger.error(f"Error handling streaming event in tool {self.name()}: {traceback.format_exc()}")

    async def _streaming_processing_loop(self):
        """Main loop for processing streaming data."""
        try:
            while not self._streaming_stop_event.is_set():
                # Process any buffered data
                if self._streaming_buffer and hasattr(self, 'process_buffer'):
                    await self.process_buffer(self._streaming_buffer.copy())

                # Sleep briefly to avoid busy waiting
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in streaming processing loop: {traceback.format_exc()}")

    async def _send_streaming_output(self, message: BidiMessage, context: Context = None):
        """Send a streaming output message to the event bus."""
        try:
            if hasattr(eventbus, 'publish_to_streaming'):
                await eventbus.publish_to_streaming(message)
                logger.debug(f"Tool {self.name()} sent streaming output: {message.data}")
        except Exception as e:
            logger.error(f"Error sending streaming output: {traceback.format_exc()}")

    def _add_to_buffer(self, data: Dict[str, Any]):
        """Add data to the streaming buffer."""
        self._streaming_buffer.append(data)
        # Maintain buffer size
        if len(self._streaming_buffer) > self.buffer_size:
            self._streaming_buffer = self._streaming_buffer[-self.buffer_size:]

    def _add_streaming_task(self, coro):
        """Add a streaming task to the set of managed tasks."""
        task = asyncio.create_task(coro)
        self._streaming_tasks.add(task)
        task.add_done_callback(self._streaming_tasks.discard)
        return task

    async def do_step(self, action: List[ActionModel], **kwargs) -> tuple[Observation, float, bool, bool, dict[str, Any]]:
        """Handle a regular (non-streaming) tool execution step."""
        try:
            message = kwargs.get('message', None)
            context = message.context if message else self.context

            # Check if this is a request to start or stop streaming
            if action and action[0].action_name == "start_streaming":
                await self.start_streaming(context)
                return Observation(text=f"Tool {self.name()} streaming started"), 0.0, False, False, {"streaming": True}

            elif action and action[0].action_name == "stop_streaming":
                await self.stop_streaming()
                return Observation(text=f"Tool {self.name()} streaming stopped"), 0.0, False, False, {"streaming": False}

            # For regular actions, handle them normally
            return await self._handle_regular_action(action, **kwargs)

        except Exception as e:
            logger.error(f"Error in do_step for tool {self.name()}: {traceback.format_exc()}")
            return Observation(text=f"Error: {str(e)}"), -1.0, True, False, {"error": str(e)}

    async def _handle_regular_action(self, action: List[ActionModel], **kwargs) -> tuple[Observation, float, bool, bool, dict[str, Any]]:
        """Handle regular (non-streaming) actions."""
        # This should be implemented by subclasses
        return Observation(text=f"Tool {self.name()} executed action {action[0].action_name if action else None}"), \
            0.0, False, False, {"action": action[0].action_name if action else None}

    async def reset(self, *, seed: int | None = None, options: Dict[str, str] | None = None) -> tuple[Observation, dict[str, Any]]:
        """Reset the tool state."""
        # Stop streaming if it's running
        if self.is_streaming:
            await self.stop_streaming()

        # Reset internal state
        self._streaming_buffer.clear()

        return Observation(text=f"Tool {self.name()} reset"), {"streaming": self.is_streaming}

    async def close(self) -> None:
        """Close the tool resources."""
        # Ensure streaming is stopped
        if self.is_streaming:
            await self.stop_streaming()

        logger.info(f"Tool {self.name()} closed")


class EchoStreamingTool(StreamingTool):
    """Example streaming tool that echoes back streaming input."""

    async def process_streaming_input(self, message: BidiMessage, context: Context = None) -> Optional[BidiMessage]:
        """Process streaming input by echoing it back."""
        try:
            # Create an echo response message
            echo_message = BidiMessage(
                event_type=BidiEventType.OUTPUT_EVENT,
                data={"echo": message.data, "tool": self.name()},
                topic=message.topic or "tool_response"
            )

            return echo_message
        except Exception as e:
            logger.error(f"Error processing streaming input in EchoStreamingTool: {traceback.format_exc()}")
            return None

    async def _handle_regular_action(self, action: List[ActionModel], **kwargs) -> tuple[Observation, float, bool, bool, dict[str, Any]]:
        """Handle regular actions for the echo tool."""
        if action:
            action_name = action[0].action_name
            params = action[0].params or {}

            return Observation(text=f"Echo tool executed action {action_name} with params: {params}"), \
                0.0, False, False, {"action": action_name, "params": params}


class TextProcessorStreamingTool(StreamingTool):
    """Example streaming tool for text processing."""

    def __init__(self, conf: Dict[str, Any], **kwargs) -> None:
        super().__init__(conf, **kwargs)
        self.processing_mode = conf.get("processing_mode", "uppercase")  # uppercase, lowercase, reverse

    async def process_streaming_input(self, message: BidiMessage, context: Context = None) -> Optional[BidiMessage]:
        """Process streaming text input based on the configured mode."""
        try:
            if isinstance(message.data, dict) and "text" in message.data:
                text = message.data["text"]

                # Process the text based on mode
                if self.processing_mode == "uppercase":
                    processed_text = text.upper()
                elif self.processing_mode == "lowercase":
                    processed_text = text.lower()
                elif self.processing_mode == "reverse":
                    processed_text = text[::-1]
                else:
                    processed_text = text

                # Create response
                response_message = BidiMessage(
                    event_type=BidiEventType.OUTPUT_EVENT,
                    data={
                        "original": text,
                        "processed": processed_text,
                        "mode": self.processing_mode,
                        "tool": self.name()
                    },
                    topic=message.topic or "text_processed"
                )

                return response_message

            return None
        except Exception as e:
            logger.error(f"Error processing text in TextProcessorStreamingTool: {traceback.format_exc()}")
            return None

    async def process_buffer(self, buffer: List[Dict[str, Any]]):
        """Process the buffer of text data."""
        # This could be used for batch processing of buffered text
        pass

    async def _handle_regular_action(self, action: List[ActionModel], **kwargs) -> tuple[Observation, float, bool, bool, dict[str, Any]]:
        """Handle regular actions to change processing mode."""
        if action:
            action_name = action[0].action_name

            if action_name == "set_mode" and action[0].params and "mode" in action[0].params:
                new_mode = action[0].params["mode"]
                if new_mode in ["uppercase", "lowercase", "reverse"]:
                    self.processing_mode = new_mode
                    return Observation(text=f"Processing mode set to {new_mode}"), \
                        0.0, False, False, {"mode": new_mode}
                else:
                    return Observation(text=f"Invalid mode: {new_mode}. Supported modes: uppercase, lowercase, reverse"), \
                        -1.0, False, False, {"error": "invalid_mode"}

        return Observation(text=f"Text processor tool in {self.processing_mode} mode"), \
            0.0, False, False, {"mode": self.processing_mode}
