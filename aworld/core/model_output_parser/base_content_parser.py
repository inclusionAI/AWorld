# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc
from typing import Any

from aworld.logs.util import logger
from aworld.models.model_response import ModelResponse


class BaseContentParser(abc.ABC):
    """Base class for all concrete content parsers"""

    @property
    @abc.abstractmethod
    def parser_type(self) -> str:
        """Parser type, e.g. 'tool', 'reasoning', 'code'"""
        pass

    @abc.abstractmethod
    async def parse(self, resp: ModelResponse, **kwargs) -> Any:
        """Parse text content and return structured data"""
        pass
