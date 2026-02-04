# coding: utf-8
# Copyright (c) inclusionAI.
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from aworld.ralph_loop.mission.types import Mission
from aworld.ralph_loop.types import Complexity


@dataclass
class PlanStep:
    """Node in hierarchical plan tree, also is a DAG."""
    step_id: str
    title: str
    description: str
    level: int = 0
    estimated_time: float = 0.0
    complexity: str = field(default=Complexity.LOW)
    success_criteria: List[str] = field(default_factory=list)
    resources_needed: List[str] = field(default_factory=list)
    alternatives: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StrategicPlan:
    """Complete strategic plan."""
    plan_id: str
    mission: Mission
    goal: str = field(default="")
    steps: List[PlanStep] = field(default_factory=list)
    # step relations
    dependency_graph: Dict[str, List[str]] = field(default_factory=dict)

    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    version: int = 1

    critical_path: List[str] = field(default_factory=list)
    total_estimated_time: float = 0.0
    total_estimated_cost: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def get_step(self, step_id: str) -> Optional[PlanStep]:
        """Get step by ID."""
        for step in self.steps:
            if step.step_id == step_id:
                return step
        return None


@dataclass
class PlanningInput:
    """Information for planning."""
    # user input or mission (Indicates processed user input)
    user_input: Any = field(default=None)
    mission: Mission = field(default=None)
    # physical constraint
    constraints: Dict[str, Any] = field(default_factory=dict)
    resources: Dict[str, Any] = field(default_factory=dict)
    # user preference
    preferences: Dict[str, Any] = field(default_factory=dict)
    # external feedback
    feedback: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def complexity(self) -> str:
        if self.user_input:
            return Complexity.HIGH
        else:
            return self.mission.complexity


@dataclass
class PlanDiff:
    """Difference between plan and replan."""
    old_plan: StrategicPlan
    new_plan: StrategicPlan
    summary: str = field(default="")
    added_steps: List[PlanStep] = field(default_factory=list)
    removed_steps: List[PlanStep] = field(default_factory=list)
    modified_steps: List[tuple] = field(default_factory=list)
    dependency_changes: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PlanReview:
    """Plan result review structure."""
    is_valid: bool
    feasible: bool = True
    consistent: bool = True
    complete: bool = True
    issues: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    # confidence below 0.5, trigger HITL
    confidence: float = 1.0
    plan_diff: PlanDiff = field(default=None)
    metadata: Dict[str, Any] = field(default_factory=dict)
