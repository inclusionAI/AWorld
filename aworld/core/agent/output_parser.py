# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc

from aworld.core.agent.base import AgentResult
from aworld.models.model_response import ModelResponse


class AgentOutputParser:
    __metaclass__ = abc.ABCMeta

    @abc.abstractmethod
    def parse(self, resp: ModelResponse) -> AgentResult:
        """Parser for responses that agent output."""
