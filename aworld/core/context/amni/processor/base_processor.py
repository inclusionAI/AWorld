# coding: utf-8
# Copyright (c) 2025 inclusionAI.

from abc import ABC, abstractmethod
from typing import Optional

from aworld.core.common import Observation
from aworld.core.context.base import Context
from ..event import ContextMessagePayload


class BaseContextProcessor(ABC):
    """Base class for memory processors"""
    
    def __init__(self, config):
        self.config = config

    @abstractmethod
    async def process(self, context: Context, event: ContextMessagePayload, **kwargs) -> Optional[Observation]:
        """Process messages"""
        pass
