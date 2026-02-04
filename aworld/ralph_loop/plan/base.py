# coding: utf-8
# Copyright (c) inclusionAI.
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any

from aworld.ralph_loop.plan.types import PlanningInput, StrategicPlan, PlanReview


class BasePlanner(ABC):
    @abstractmethod
    async def plan(self, plan_input: PlanningInput) -> StrategicPlan:
        """Create a strategic plan from input.

        Args:
            plan_input: Planning input containing mission, constraints, resources

        Returns:
            StrategicPlan ready for validation and execution.
        """

    @abstractmethod
    async def replan(self, plan: StrategicPlan, feedback: Optional[Dict[str, Any]] = None) -> StrategicPlan:
        """Create a new plan based on triggers.

        Args:
            plan: Mission planning information.
            feedback: The information from the human or the model to replan the plan.

        Returns:
            StrategicPlan ready for validation and execution.
        """


class BasePlanReviewer(ABC):
    """Review the plan to ensure its rationality and correctness, suggested processing."""

    @abstractmethod
    async def review(self, plan: StrategicPlan, old_plan: Optional[StrategicPlan] = None) -> PlanReview:
        """Review the strategic plan.

        Args:
            plan: Mission planning information.
            old_plan: Mission old planning information.

        Returns:
            PlanReview information.
        """


class BasePlanOptimizer(ABC):
    """Optimize strategic plan for better performance, optional processing."""

    @abstractmethod
    async def optimize(self, plan: StrategicPlan) -> StrategicPlan:
        """Optimization processing of task planning.

        Args:
            plan: Mission planning information.

        Returns:
            More efficient StrategicPlan ready for validation and execution.
        """
