# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
import json
import os
import traceback
from typing import Any, Dict, Tuple, List, Optional

from aworld.config import ToolConfig
from aworld.core.common import Observation, ActionModel, ActionResult, ToolActionInfo, ParamInfo
from aworld.core.context.amni import AmniContext
from aworld.core.event.base import Message
from aworld.core.tool.action import ToolAction
from aworld.core.tool.base import ToolFactory, AsyncTool
from aworld.logs.util import logger
from aworld.tools.utils import build_observation

CONTEXT_MESSAGE = "CONTEXT_MESSAGE"
RECEIVER_INBOX_KEY = "_inbox"
DEFAULT_POLL_INTERVAL = 0.5  # Default polling interval in seconds
DEFAULT_POLL_TIMEOUT = 60000.0  # Default polling timeout in seconds


class ContextMessageAction(ToolAction):
    """Agent Message Support. Definition of Context message publish and listen supported action."""

    PUBLISH_MESSAGE = ToolActionInfo(
        name="publish_message",
        input_params={
            "receiver": ParamInfo(
                name="receiver",
                type="str",
                required=True,
                desc="receiver identifier to send message to"
            ),
            "message": ParamInfo(
                name="message",
                type="str",
                required=True,
                desc="message content to send"
            )
        },
        desc="publish a message to a receiver's inbox"
    )

    LISTEN_MESSAGE = ToolActionInfo(
        name="listen_message",
        input_params={
            "signal_key": ParamInfo(
                name="signal_key",
                type="str",
                required=True,
                desc="signal key to poll for messages"
            ),
            "poll_interval": ParamInfo(
                name="poll_interval",
                type="float",
                required=False,
                desc=f"polling interval in seconds (default: {DEFAULT_POLL_INTERVAL})"
            ),
            "poll_timeout": ParamInfo(
                name="poll_timeout",
                type="float",
                required=False,
                desc=f"polling timeout in seconds (default: {DEFAULT_POLL_TIMEOUT})"
            )
        },
        desc="listen for messages by polling a signal key until content is available"
    )


@ToolFactory.register(name=CONTEXT_MESSAGE,
                      desc=CONTEXT_MESSAGE,
                      supported_action=ContextMessageAction)
class ContextMessageTool(AsyncTool):
    """Tool for publishing and listening to messages via context inboxes.
    
    This tool provides two main capabilities:
    1. publish_message: Send messages to receivers by appending to their inbox
    2. listen_message: Poll a signal key until messages are available
    
    Example:
        # Publish a message
        action = ActionModel(
            tool_name="CONTEXT_MESSAGE",
            action_name="publish_message",
            params={"receiver": "agent_1", "message": "Hello"}
        )
        
        # Listen for messages
        action = ActionModel(
            tool_name="CONTEXT_MESSAGE",
            action_name="listen_message",
            params={"signal_key": "my_signal_key"}
        )
    """
    
    def __init__(self, conf: ToolConfig, **kwargs) -> None:
        super(ContextMessageTool, self).__init__(conf, **kwargs)
        self.cur_observation = None
        self.content = None
        self._publisher: Optional[Any] = None
        self._publisher_lock: Optional[asyncio.Lock] = None
        self.init()
        self.step_finished = True

    async def reset(self, *, seed: int | None = None, options: Dict[str, str] | None = None) -> Tuple[
        Observation, dict[str, Any]]:
        """Reset the tool state.
        
        Args:
            seed: Random seed (optional)
            options: Reset options (optional)
            
        Returns:
            Tuple of initial observation and info dict
        """
        await super().reset(seed=seed, options=options)
        await self.close()
        self.step_finished = True
        return build_observation(observer=self.name(),
                                 ability=ContextMessageAction.PUBLISH_MESSAGE.value.name), {}

    def init(self) -> None:
        """Initialize the tool."""
        self.initialized = True

    async def close(self) -> None:
        """Close the tool resources."""
        if self._publisher:
            try:
                await self._publisher.disconnect()
                logger.info("📡 ContextMessageTool|close: Disconnected EnvChannelPublisher")
            except Exception as e:
                logger.warn(f"⚠️ ContextMessageTool|close: Error disconnecting publisher: {e}")
            finally:
                self._publisher = None

    async def finished(self) -> bool:
        """Check if the tool execution is finished.
        
        Returns:
            bool: True if finished, False otherwise
        """
        return self.step_finished

    async def _get_publisher(self):
        """
        Get or create EnvChannelPublisher instance.
        
        Returns:
            EnvChannelPublisher instance or None if env_channel is not available
        """
        # Initialize lock if not already initialized
        if self._publisher_lock is None:
            self._publisher_lock = asyncio.Lock()
        
        async with self._publisher_lock:
            if self._publisher is None:
                try:
                    from env_channel.client import EnvChannelPublisher
                    
                    # Get server URL from environment or use default
                    server_url = os.environ.get("ENV_CHANNEL_SERVER_URL", "ws://localhost:8765/channel")
                    
                    self._publisher = EnvChannelPublisher(
                        server_url=server_url,
                        auto_connect=True,
                        auto_reconnect=True,
                    )
                    logger.info(f"📡 ContextMessageTool|_get_publisher: Created EnvChannelPublisher for {server_url}")
                except ImportError:
                    logger.debug("⚠️ ContextMessageTool|_get_publisher: env_channel package not installed, "
                               "EnvChannel publishing will be disabled")
                    return None
                except Exception as e:
                    logger.error(f"❌ ContextMessageTool|_get_publisher: Failed to create publisher: {e}", exc_info=True)
                    return None
            
            return self._publisher

    async def _publish_message(self, receiver: str, message: str, context: AmniContext, namespace: str = "default") -> str:
        """
        Publish a message to a receiver's inbox and/or EnvChannel.
        
        This method supports two publishing mechanisms:
        1. Context inbox (legacy): Stores message in context for polling
        2. EnvChannel (new): Publishes message via WebSocket to EnvChannel server
        
        Args:
            receiver: Receiver identifier (used as topic for EnvChannel)
            message: Message content to send (can be str or dict)
            context: AmniContext instance
            namespace: Namespace for context operations
            
        Returns:
            str: Success message
            
        Raises:
            ValueError: If receiver or message is invalid
        """
        if not receiver:
            raise ValueError("receiver cannot be empty")
        if not message:
            raise ValueError("message cannot be empty")
        
        # Try to publish via EnvChannel first
        publisher = await self._get_publisher()
        if publisher:
            try:
                # Convert message to dict if it's a string
                if isinstance(message, str):
                    try:
                        import json
                        # Try to parse as JSON, if fails, wrap in dict
                        try:
                            message_dict = json.loads(message)
                        except (json.JSONDecodeError, TypeError):
                            message_dict = {"content": message, "receiver": receiver}
                    except Exception:
                        message_dict = {"content": message, "receiver": receiver}
                elif isinstance(message, dict):
                    message_dict = message
                else:
                    message_dict = {"content": str(message), "receiver": receiver}
                
                # Use receiver as topic (channel name)
                topic = "GAMMING"
                await publisher.publish(topic=topic, message=message_dict)
                logger.info(f"📡 ContextMessageTool|_publish_message: Published message to EnvChannel topic '{topic}'")
            except Exception as e:
                logger.warn(f"⚠️ ContextMessageTool|_publish_message: Failed to publish via EnvChannel: {e}, "
                           f"falling back to context inbox")
                # Fall through to context inbox method
        
        # Also publish to context inbox for backward compatibility
        # Construct the key for receiver's inbox
        receiver_key = f"{receiver}_{RECEIVER_INBOX_KEY}"
        
        # Get existing inbox or create new list
        inbox = context.get(receiver_key, namespace=namespace)
        if inbox is None:
            inbox = []
            logger.info(f"📨 ContextMessageTool|_publish_message: Created new inbox for receiver '{receiver}'")
        elif not isinstance(inbox, list):
            # If key exists but is not a list, convert it to a list
            logger.warn(f"⚠️ ContextMessageTool|_publish_message: Key '{receiver_key}' exists but is not a list, converting to list")
            inbox = [inbox] if inbox else []
        
        # Append message to inbox
        inbox.append(message)
        context.put(receiver_key, inbox, namespace=namespace)
        
        logger.info(f"📨 ContextMessageTool|_publish_message: Published message to receiver '{receiver}', "
                   f"inbox size: {len(inbox)}")
        
        return f"Message published to receiver '{receiver}' successfully"

    async def _listen_message(self, signal_key: str, context: AmniContext, 
                             poll_interval: float = DEFAULT_POLL_INTERVAL,
                             poll_timeout: float = DEFAULT_POLL_TIMEOUT,
                             namespace: str = "default") -> str:
        """Listen for messages by polling a signal key.
        
        Args:
            signal_key: Signal key to poll
            context: AmniContext instance
            poll_interval: Polling interval in seconds
            poll_timeout: Polling timeout in seconds
            namespace: Namespace for context operations
            
        Returns:
            str: Message content when available
            
        Raises:
            ValueError: If signal_key is invalid
            TimeoutError: If polling times out
        """
        if not signal_key:
            raise ValueError("signal_key cannot be empty")
        
        import time
        start_time = time.time()
        poll_count = 0
        
        logger.info(f"👂 ContextMessageTool|_listen_message(namespace:{namespace}): Starting to poll signal key '{signal_key}', "
                   f"interval: {poll_interval}s, timeout: {poll_timeout}s")
        
        while True:
            # Check timeout
            elapsed_time = time.time() - start_time
            if elapsed_time >= poll_timeout:
                raise TimeoutError(f"Polling timeout after {poll_timeout}s for signal key '{signal_key}'")
            
            # Poll the signal key
            content = context.get(signal_key, namespace=namespace)
            poll_count += 1
            
            # Check if content is available
            if content is not None:
                # Handle different content types
                if isinstance(content, list):
                    # If content is a list, get all messages at once
                    if len(content) > 0:
                        all_messages = content.copy()  # Get all messages
                        # Clear the key after retrieving all messages
                        context.put(signal_key, None, namespace=namespace)
                        logger.info(f"✅ ContextMessageTool|_listen_message: Received {len(all_messages)} message(s) from signal key '{signal_key}' "
                                   f"after {poll_count} polls ({elapsed_time:.2f}s)")
                        # Return JSON serialized result
                        return json.dumps(all_messages, ensure_ascii=False)
                elif content != "":
                    # For non-list content, return it as JSON array and clear the key
                    context.put(signal_key, None, namespace=namespace)
                    logger.info(f"✅ ContextMessageTool|_listen_message: Received message from signal key '{signal_key}' "
                               f"after {poll_count} polls ({elapsed_time:.2f}s)")
                    # Return as JSON array with single element
                    return json.dumps([content], ensure_ascii=False)
            
            # Wait before next poll
            await asyncio.sleep(poll_interval)
            
            # Log progress periodically
            if poll_count % 10 == 0:
                logger.debug(f"🔄 ContextMessageTool|_listen_message: Polling signal key '{signal_key}', "
                           f"attempts: {poll_count}, elapsed: {elapsed_time:.2f}s")

    async def do_step(self, actions: list[ActionModel], message: Message = None, **kwargs) -> Tuple[
        Observation, float, bool, bool, Dict[str, Any]]:
        """Execute one step of the tool.
        
        Args:
            actions: List of actions to execute
            message: Message containing context
            **kwargs: Additional keyword arguments
            
        Returns:
            Tuple of (observation, reward, terminated, truncated, info)
        """
        self.step_finished = False
        reward = 0.
        fail_error = ""
        observation = build_observation(observer=self.name(),
                                        ability=ContextMessageAction.PUBLISH_MESSAGE.value.name)
        info = {}
        
        try:
            if not actions:
                raise ValueError("actions is empty")
            if not isinstance(message.context, AmniContext):
                raise ValueError("context is not AmniContext")
            
            context: AmniContext = message.context
            
            for action in actions:
                # Use agent_name as namespace if available, otherwise use default
                namespace = action.agent_name if hasattr(action, 'agent_name') and action.agent_name else 'default'
                logger.info(f"📬 ContextMessageTool|do_step: Processing action {action.action_name}, "
                           f"params: {action.params}")
                
                action_name = action.action_name
                
                if action_name == ContextMessageAction.PUBLISH_MESSAGE.value.name:
                    # Publish message action
                    receiver = action.params.get("receiver", "")
                    msg_content = action.params.get("message", "")
                    
                    if not receiver:
                        raise ValueError("receiver parameter is required")
                    if not msg_content:
                        raise ValueError("message parameter is required")
                    
                    result = await self._publish_message(receiver, msg_content, context, namespace)
                    
                elif action_name == ContextMessageAction.LISTEN_MESSAGE.value.name:
                    # Listen message action
                    signal_key = action.params.get("signal_key", "")
                    poll_interval = float(action.params.get("poll_interval", DEFAULT_POLL_INTERVAL))
                    poll_timeout = float(action.params.get("poll_timeout", DEFAULT_POLL_TIMEOUT))
                    
                    if not signal_key:
                        raise ValueError("signal_key parameter is required")
                    
                    try:
                        result = await self._listen_message(signal_key, context, poll_interval, poll_timeout, namespace)
                    except TimeoutError as timeout_err:
                        # Handle timeout gracefully: return a message indicating timeout
                        timeout_msg = f"时间已经到了（等待 {poll_timeout}s 后未收到信号 '{signal_key}'），请决定下一步"
                        logger.warning(f"⏰ ContextMessageTool|do_step: {timeout_msg}")
                        result = timeout_msg
                        # Set observation content with timeout message
                        observation.content = result
                        observation.action_result.append(
                            ActionResult(is_done=True,
                                         success=False,  # Mark as not successful due to timeout
                                         content=result,
                                         keep=False))
                        # Continue to next action or finish
                        continue
                    
                else:
                    raise ValueError(f"Invalid action name: {action_name}")
                
                # Set observation content and result
                observation.content = result
                observation.action_result.append(
                    ActionResult(is_done=True,
                                 success=True,
                                 content=f"{result}",
                                 keep=False))
            
            reward = 1.
            logger.info(f"✅ ContextMessageTool|do_step: Successfully completed {len(actions)} action(s)")
            
        except Exception as e:
            fail_error = str(e)
            logger.warn(f"❌ ContextMessageTool|do_step: Failed with error: {fail_error}, "
                       f"traceback: {traceback.format_exc()}")
        finally:
            self.step_finished = True
        
        info["exception"] = fail_error
        info.update(kwargs)
        return (observation, reward, kwargs.get("terminated", False),
                kwargs.get("truncated", False), info)

