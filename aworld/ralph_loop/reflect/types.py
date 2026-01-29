# coding: utf-8
# Copyright (c) inclusionAI.
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ReflectionType(Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    # opt suggestions
    OPTIMIZATION = "optimization"
    # pattern recognition
    PATTERN = "pattern"
    # Insight discovery
    INSIGHT = "insight"


class ReflectionLevel(Enum):
    # what happened
    SHALLOW = "shallow"
    # why happened
    MEDIUM = "medium"
    # how to improve
    DEEP = "deep"
    # reflect on reflection
    META = "meta"


@dataclass
class ReflectionInput:
    iteration: int = 0
    input_data: Any = None
    output_data: Any = None
    execution_time: float = 0.0

    success: bool = False
    error_msg: Optional[str] = None

    previous_attempts: List[Dict[str, Any]] = field(default_factory=list)
    historical_reflections: List[Dict[str, Any]] = field(default_factory=list)

    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ReflectionResult:
    reflection_type: ReflectionType
    level: ReflectionLevel

    # reflection
    summary: str = ""
    key_findings: List[str] = field(default_factory=list)
    root_causes: List[str] = field(default_factory=list)
    insights: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)

    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ReflectionHistory:
    reflections: List[ReflectionResult] = field(default_factory=list)
    total_count: int = 0
    success_count: int = 0
    failure_count: int = 0

    def add_reflection(self, reflection: ReflectionResult):
        """Add reflection record."""
        self.reflections.append(reflection)
        self.total_count += 1

        if reflection.reflection_type == ReflectionType.SUCCESS:
            self.success_count += 1
        elif reflection.reflection_type == ReflectionType.FAILURE:
            self.failure_count += 1

    def get_recent(self, n: int = 5) -> List[ReflectionResult]:
        """Get the latest n reflections."""
        return self.reflections[-n:]

    def get_by_type(self, reflection_type: ReflectionType) -> List[ReflectionResult]:
        """Get reflections by type."""
        return [r for r in self.reflections if r.reflection_type == reflection_type]
