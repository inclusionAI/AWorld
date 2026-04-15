from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from aworld_gateway.config import BaseChannelConfig


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
    def start(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        return None
