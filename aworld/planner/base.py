# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""Base planner classes for AWorld."""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Dict, List, Optional, Union
from aworld.models.model_response import ModelResponse
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from aworld.core.context.base import Context



class PlanOutputParser(ABC):
    @abstractmethod
    def parse(self, ModelResponse: ModelResponse) -> Plan:
        """Parse text into a Plan object."""
        pass


class BasePlanner:
    @abstractmethod
    def build_plan_instruction(self, context: "Context", input: str) -> str:
        """Get the name of the planner."""
        pass
