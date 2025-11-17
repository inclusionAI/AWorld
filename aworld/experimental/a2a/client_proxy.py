import httpx
import json
from uuid import uuid4
from typing import Union, Any, Optional
from collections.abc import AsyncIterator
from a2a.types import (
    AgentCard,
    Message as A2AMessage,
    SendMessageResponse,
    Role,
    TextPart,
    TaskArtifactUpdateEvent,
    TaskStatusUpdateEvent,
    TaskState,
)
from pathlib import Path
from a2a.client.client_factory import ClientFactory as A2AClientFactory
from a2a.client.client import ClientConfig as A2AClientConfig, Client as A2AClient, ClientEvent
from a2a.client.card_resolver import A2ACardResolver
from a2a.client.middleware import ClientCallContext
from urllib.parse import urlparse
from aworld.experimental.a2a.config import ClientConfig
from aworld.logs.util import logger
from aworld.core.task import Task, TaskResponse
from aworld.config import RunConfig


class A2AClientProxy:

    def __init__(
        self,
        agent_card: Union[AgentCard, str],
        config: ClientConfig,
    ):
        self._config = config
        self._agent_card_source = None
        self._agent_card: Optional[AgentCard] = None

        if isinstance(agent_card, AgentCard):
            self._agent_card = agent_card
        elif isinstance(agent_card, str):
            if not agent_card.strip():
                raise ValueError("agent_card string cannot be empty")
            self._agent_card_source = agent_card.strip()
        else:
            raise TypeError(
                "agent_card must be AgentCard, URL string, or file path string, "
                f"got {type(agent_card)}"
            )
        self._httpx_client = httpx.AsyncClient(timeout=self._config.timeout)
        self._a2a_client_factory = self._init_a2a_client_factory(self._httpx_client)
        self._a2a_client: Optional[A2AClient] = None

    def _init_a2a_client_factory(self, _httpx_client) -> A2AClientFactory:
        a2a_client_config = A2AClientConfig(
            streaming=self._config.streaming,
            polling=self._config.polling,
            httpx_client=_httpx_client,
            supported_transports=self._config.supported_transports,
            grpc_channel_factory=self._config.grpc_channel_factory,
            use_client_preference=self._config.use_client_preference,
            accepted_output_modes=self._config.accepted_output_modes,
            push_notification_configs=self._config.push_notification_configs,
        )
        return A2AClientFactory(
            config=a2a_client_config,
            consumers=self._config.consumers,
        )

    async def _ensure_a2a_client(self):
        if self._a2a_client is None:
            await self.get_or_init_agent_card()
            self._a2a_client = self._a2a_client_factory.create(self._agent_card)

    async def _resolve_agent_card(self) -> AgentCard:
        if self._agent_card_source.startswith(("http://", "https://")):
            try:
                parsed_url = urlparse(self._agent_card_source)
                if not parsed_url.scheme or not parsed_url.netloc:
                    raise ValueError(f"Invalid URL format: {self._agent_card_source}")

                base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
                relative_card_path = parsed_url.path
                resolver = A2ACardResolver(
                    httpx_client=self._httpx_client,
                    base_url=base_url,
                )
                return await resolver.get_agent_card(
                    relative_card_path=relative_card_path
                )
            except Exception as e:
                logger.error(f"Failed to resolve AgentCard from URL {self._agent_card_source}: {e}")
                raise e
        else:
            try:
                path = Path(self._agent_card_source)
                if not path.exists():
                    raise FileNotFoundError(f"Agent card file not found: {self._agent_card_source}")
                if not path.is_file():
                    raise ValueError(f"Path is not a file: {self._agent_card_source}")

                with path.open("r", encoding="utf-8") as f:
                    agent_json_data = json.load(f)
                    return AgentCard(**agent_json_data)
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON in agent card file {self._agent_card_source}: {e}")
                raise e
            except Exception as e:
                logger.error(f"Failed to read agent card file {self._agent_card_source}: {e}")
                raise e

    async def get_or_init_agent_card(self) -> AgentCard:
        if self._agent_card is None:
            self._agent_card = await self._resolve_agent_card()
        return self._agent_card

    def _build_a2a_message(self, message: A2AMessage | dict[str, Any] | str) -> A2AMessage:
        if isinstance(message, A2AMessage):
            a2a_message = message
        elif isinstance(message, dict):
            a2a_message = A2AMessage(**message)
        elif isinstance(message, str):
            a2a_message = A2AMessage(
                role=Role.user,
                parts=[TextPart(text=message)],
                message_id=uuid4().hex,
            )
        else:
            raise ValueError(f"Invalid message type: {type(message)}")
        return a2a_message

    def _convert_task_to_request(self, task: Task, run_conf: RunConfig = None) -> A2AMessage:
        a2a_text_message = self._build_a2a_message(task.input)
        request_meta = {
            'task_id': task.id,
            'user_id': task.user_id,
            'session_id': task.session_id,
            'tool_names': task.tool_names,
            'mcp_servers_conf': task.mcp_servers_conf,
            'task_conf': task.conf,
            'run_conf': run_conf,
            'streaming_mode': self._config.streaming_mode,
        }
        a2a_text_message.metadata = request_meta

        return a2a_text_message

    async def _do_send_message(self, message: A2AMessage | dict[str, Any] | str, context: dict[str, Any] = None) -> AsyncIterator[ClientEvent | A2AMessage]:
        '''
        Send a message to the A2A server.

        Args:
            message (A2AMessage | dict[str, Any] | str): The message to send.
                If it is a dict, it should be in the format of MessageSendParams. like:
                    {
                        'role': 'user',
                        'parts': [{'kind': 'text', 'text': 'CAD'}],
                        'message_id': uuid4().hex,
                        'task_id': task_id,
                        'context_id': context_id,
                    }
            context (dict[str, Any], optional): The context to send with the message. Defaults to None.

        Returns:
            AsyncIterator[ClientEvent | A2AMessage]: An async iterator of events and messages from the server.
        '''
        await self._ensure_a2a_client()
        a2a_message = self._build_a2a_message(message)
        logger.debug(f"send_message metadata: {a2a_message.metadata}")
        if context:
            call_context = ClientCallContext(state=context)
        else:
            call_context = None
        async for event in self._a2a_client.send_message(a2a_message, context=call_context):
            yield event

    async def _handle_a2a_response(self, a2a_response: ClientEvent | A2AMessage, task: Task) -> ClientEvent | TaskResponse:
        logger.debug(f"send_task receive event: {a2a_response}")
        if isinstance(a2a_response, tuple):
            # streaming task
            a2a_task, update_event = a2a_response
            if update_event is None and a2a_task.artifacts:
                return TaskResponse(
                    id=task.id,
                    answer=a2a_task.artifacts[0].parts[0].root.text,
                    success=True,
                )
            if update_event and isinstance(update_event, TaskArtifactUpdateEvent):
                if update_event.last_chunk:
                    answer = update_event.artifact.parts[0].root.text,
                    return TaskResponse(
                        id=task.id,
                        answer=answer,
                        success=True,
                    )
            if update_event and isinstance(update_event, TaskStatusUpdateEvent):
                if update_event.status.state == TaskState.failed:
                    return TaskResponse(
                        id=task.id,
                        answer="",
                        msg=update_event.status.reason,
                        success=False,
                    )
            return update_event
        elif isinstance(a2a_response, A2AMessage):
            # non-streaming task
            answer = a2a_response.parts[0].text
            return TaskResponse(
                id=task.id,
                answer=answer,
                success=True,
            )

    async def send_task(self, task: Task, run_conf: RunConfig = None) -> TaskResponse:
        a2a_text_message = self._convert_task_to_request(task, run_conf)
        try:
            async for a2a_response in self._do_send_message(a2a_text_message):
                event = await self._handle_a2a_response(a2a_response, task)
                if event and isinstance(event, TaskResponse):
                    return event
        except Exception as e:
            logger.error(f"task: {task.id} send message failed. {e}")
            raise e

    async def send_task_stream(self, task: Task, run_conf: RunConfig = None) -> AsyncIterator[ClientEvent | TaskResponse]:
        await self.get_or_init_agent_card()
        if not self._config.streaming:
            raise ValueError("Streaming is not enabled for this client.")
        if not self._agent_card.capabilities.streaming:
            raise ValueError("Streaming is not enabled for this agent.")
        a2a_text_message = self._convert_task_to_request(task, run_conf)
        try:
            async for a2a_response in self._do_send_message(a2a_text_message):
                yield await self._handle_a2a_response(a2a_response, task)
        except Exception as e:
            logger.error(f"task: {task.id} send message failed. {e}")
            raise e
