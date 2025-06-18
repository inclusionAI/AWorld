from abc import abstractmethod
from typing import AsyncGenerator
from .data_model import ChatCompletionRequest


class BaseAWorldAgent:
    @abstractmethod
    def agent_name(self) -> str:
        pass

    @abstractmethod
    def agent_description(self) -> str:
        pass

    @abstractmethod
    async def run(self, request: ChatCompletionRequest) -> AsyncGenerator[str, None]:
        pass