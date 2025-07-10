
from abc import abstractmethod
from aworld.core.agent.base import AgentResult
from aworld.models.model_response import ModelResponse


class AgentOutputParser:
    """Parser for responses that agent output."""
    @abstractmethod
    def parse(self, resp: ModelResponse) -> AgentResult:
        pass
