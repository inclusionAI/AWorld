# coding: utf-8
# Copyright (c) inclusionAI.
from abc import ABC, abstractmethod

from aworld.ralph_loop.mission.types import Mission
from aworld.ralph_loop.state.types import LoopContext


class ContextEnhancer(ABC):
    """Enhance the global context with a mission."""

    @abstractmethod
    async def enhance(self, mission: Mission, context: LoopContext) -> Mission:
        """Add mission-related info to the global loop context."""


class DefaultContextEnhancer(ContextEnhancer):

    async def enhance(self, mission: Mission, context: LoopContext) -> Mission:
        return mission


class HistoryEnhancer(ContextEnhancer):

    async def enhance(self, mission: Mission, context: LoopContext) -> Mission:
        """Add a historical related on the mission to the global loop context."""


class KnowledgeEnhancer(ContextEnhancer):

    async def enhance(self, mission: Mission, context: LoopContext) -> Mission:
        """Add knowledge related to the mission to the global loop context."""
