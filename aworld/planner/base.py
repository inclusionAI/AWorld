# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""Base planner classes for AWorld."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, TYPE_CHECKING
from pydantic import BaseModel

if TYPE_CHECKING:
    from aworld.core.context.base import Context


class Plan(BaseModel):
    """A plan consisting of multiple steps."""
    
    steps: List["Step"]
    """The steps in the plan."""


class Step(BaseModel):
    """A single step in a plan."""
    
    value: str
    """The description of the step."""


class PlanOutputParser(ABC):
    """Base class for parsing plan outputs."""
    
    @abstractmethod
    def parse(self, text: str) -> Plan:
        """Parse text into a Plan object."""
        pass


class BasePlanner:
    """Base planner for AWorld agents.
    
    This class is inspired by langchain-experimental planners but adapted
    for AWorld's Context and ModelResponse systems.
    """

    @abstractmethod
    def build_plan_instruction(self, context: "Context", input: str) -> str:
        """Get the name of the planner."""
        pass
    
    @abstractmethod
    def plan(self, context: "Context", input: str, **kwargs: Any) -> Plan:
        """Given input and context, decide what to do.
        
        Args:
            context: The AWorld context object.
            inputs: Input variables for planning.
            **kwargs: Additional keyword arguments.
            
        Returns:
            A Plan object containing the planned steps.
        """
        pass
    
    @abstractmethod
    async def aplan(self, context: "Context", input: str, **kwargs: Any) -> Plan:
        """Given input and context, asynchronously decide what to do.
        
        Args:
            context: The AWorld context object.
            inputs: Input variables for planning.
            **kwargs: Additional keyword arguments.
            
        Returns:
            A Plan object containing the planned steps.
        """
        pass 