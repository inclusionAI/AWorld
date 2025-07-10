import abc
from abc import abstractmethod
from aworld.core.agent.base import AgentResult
from aworld.models.model_response import ModelResponse


class AgentOutputParser:
    __metaclass__ = abc.ABCMeta

    @abstractmethod
    def parse(self, resp: ModelResponse) -> AgentResult:
        """Parser for responses that agent output."""
