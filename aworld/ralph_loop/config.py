# coding: utf-8
# Copyright (c) inclusionAI.
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Union

from pydantic import Field

from aworld.config import ModelConfig, TaskConfig, BaseConfig
from aworld.evaluations.base import EvalCriteria, Scorer
from aworld.evaluations.reflect import Reflector
from aworld.ralph_loop.detect.stop_condition import StopCondition
# from aworld.ralph_loop.mission.analyzer import Analyzer
# from aworld.ralph_loop.mission.enhancer import ContextEnhancer
# from aworld.ralph_loop.mission.types import MissionType
# from aworld.ralph_loop.plan.base import BasePlanner, BasePlanReviewer, BasePlanOptimizer
from aworld.ralph_loop.types import ConflictStrategy


@dataclass
class ValidationConfig:
    """Configuration for validation module."""
    enabled: bool = True
    validators: List[Union[str, EvalCriteria, Scorer]] = field(default_factory=lambda: [])
    conflict_strategy: str = ConflictStrategy.MERGE
    model_config: ModelConfig = None
    parallel: int = 1
    timeout: float = 30.0
    min_score_threshold: float = 0.6


@dataclass
class ReflectionConfig:
    """Configuration for reflection module."""
    enabled: bool = True
    reflectors: List[Union[str, Reflector]] = field(default_factory=list)
    conflict_strategy: str = ConflictStrategy.MERGE
    model_config: ModelConfig = None
    reflection_level: str = "MEDIUM"


@dataclass
class StopConditionConfig:
    """Configuration for stop detection."""

    stop_detectors: List[StopCondition] = field(default_factory=list)
    conflict_strategy: str = ConflictStrategy.MERGE
    max_iterations: int = 1
    timeout: Optional[float] = 3600.0
    max_consecutive_failures: int = 3
    max_cost: Optional[float] = 100.0
    enable_user_interrupt: bool = True
    custom_conditions: List[str] = field(default_factory=list)


@dataclass
class MissionConfig:
    """Configuration for mission processing."""

    input_type: str = 'hybrid'
    model_config: ModelConfig = field(default_factory=ModelConfig)
    # analyzer: Optional[Analyzer] = None
    # enhancer: Optional[ContextEnhancer] = None


@dataclass
class PlanningConfig:
    """Configuration for strategic planning module."""

    enabled: bool = False
    # planner: Optional[BasePlanner] = None

    # reuse the GeneralPlanner
    model_config: Optional[ModelConfig] = None
    system_prompt: Optional[str] = ""
    # reviewer: Optional[BasePlanReviewer] = None
    # optimizer: Optional[BasePlanOptimizer] = None


@dataclass
class StateConfig:
    """Configuration for state management."""

    enable_history: bool = True
    max_history_size: int = 1000
    enable_metrics: bool = True


class RalphConfig(BaseConfig):
    """Unified configuration for Ralph Loop.

    This configuration class combines all component configurations and provides sensible defaults for different use cases.
    """
    model_config = {"arbitrary_types_allowed": True}
    mission: MissionConfig = Field(default_factory=MissionConfig)
    planning: PlanningConfig = Field(default_factory=PlanningConfig)
    validation: ValidationConfig = Field(default_factory=ValidationConfig)
    reflection: ReflectionConfig = Field(default_factory=ReflectionConfig)
    stop_condition: StopConditionConfig = Field(default_factory=StopConditionConfig)
    state: StateConfig = Field(default_factory=StateConfig)

    workspace: str = "."
    # Global settings
    llm_config: ModelConfig = Field(default_factory=ModelConfig)
    task_config: Optional[TaskConfig] = None

    @classmethod
    def create(cls, model_config: Optional[ModelConfig] = None) -> 'RalphConfig':
        """Create default configuration with all features."""
        config = cls()

        if model_config:
            config.llm_config = model_config
            config.mission.model_config = model_config
            config.planning.model_config = model_config
            config.validation.model_config = model_config
            config.reflection.model_config = model_config

        return config
