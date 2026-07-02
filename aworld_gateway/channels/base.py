from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from aworld_gateway.config import BaseChannelConfig
from aworld_gateway.types import OutboundEnvelope


@dataclass(frozen=True)
class ChannelMetadata:
    name: str
    implemented: bool


class ChannelAdapter(ABC):
    def __init__(self, config: BaseChannelConfig | None = None) -> None:
        self._config = config

    @classmethod
    @abstractmethod
    def metadata(cls) -> ChannelMetadata:
        raise NotImplementedError

    @abstractmethod
    async def start(self) -> None:
        raise NotImplementedError

    async def stop(self) -> None:
        return None

    @abstractmethod
    async def send(self, envelope: OutboundEnvelope) -> Any:
        raise NotImplementedError
