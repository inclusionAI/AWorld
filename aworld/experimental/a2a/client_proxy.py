import httpx
import json
from typing import Union
from a2a.types import AgentCard
from pathlib import Path
from a2a.client.client_factory import ClientFactory as A2AClientFactory
from a2a.client.client import ClientConfig as A2AClientConfig
from a2a.client.card_resolver import A2ACardResolver
from urllib.parse import urlparse
from aworld.experimental.a2a.config import ClientConfig
from aworld.logs.util import logger


class A2AClientProxy:

    def __init__(
        self,
        agent_card: Union[AgentCard, str],
        config: ClientConfig,
    ):
        self._config = config
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
        self._a2a_client_factory = self.init_a2a_client_factory(self._httpx_client)

    def init_a2a_client_factory(self, _httpx_client) -> A2AClientFactory:
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

    async def get_agent_card(self) -> AgentCard:
        if self._agent_card is None:
            self._agent_card = await self._resolve_agent_card()
        return self._agent_card

    # async def send_message(self, message: MessageSendParams | dict[str, Any] | str) -> SendMessageResponse:
    #     a2a_client = self._a2a_client_factory.create_client(self._agent_card)
    #     return await a2a_client.send_message(message)
