# coding: utf-8
# Copyright (c) inclusionAI.
from dataclasses import field, dataclass
from typing import Any, Dict, List, Literal, Optional

from aworld.ralph_loop.types import CompletionCriteria, Complexity

# Mission input types
MissionType = Literal['text', 'json', 'voice', 'image', 'video', 'hybrid']


@dataclass
class MissionIntent:
    """Structured intent representation."""
    primary: str = ""
    secondary: List[str] = field(default_factory=list)
    confidence: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Mission:
    """Mission representation, different input types to text uniformly."""
    original: Any = field(default=None)
    input_type: MissionType = field(default="text")
    text: str = field(default="")
    desc: str = field(default="")
    completion_criteria: CompletionCriteria = field(default_factory=CompletionCriteria)
    metadata: Dict[str, Any] = field(default_factory=dict)

    ## based on analysis of mission
    intent: MissionIntent = field(default_factory=MissionIntent)
    complexity: str = field(default=Complexity.LOW)
    # entity can be structured
    entities: List[str] = field(default_factory=list)
    estimated_time: float = 0.0
    success_probability: float = 1.0
    risks: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)

    def update(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)
        return self
