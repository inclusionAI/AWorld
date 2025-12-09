# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Any, List, Optional, Union

from aworld.config import BaseConfig, ModelConfig
from aworld.core.common import ActionResult
from aworld.core.tool.base import BaseTool, AsyncBaseTool


# ------------------------------------------ Generation ----------------------------------------------- #

class GenerationStrategy:
    """Generation strategy."""
    LLM = "llm"
    MODEL = "model"
    TEMPLATE = "template"


class ExamplesStrategy:
    ZERO_SHOT = 'zero'
    FEW_SHOT = 'few'


class Complexity:
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @staticmethod
    def default_distribute():
        return {
            Complexity.LOW: 0.5,
            Complexity.MEDIUM: 0.3,
            Complexity.HIGH: 0.2
        }

    @staticmethod
    def types():
        return [k for k in Complexity.__dict__ if not k.startswith('__') and not k == 'types']


class Diversity:
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @staticmethod
    def default_distribute():
        return {
            Diversity.LOW: 0.5,
            Diversity.MEDIUM: 0.3,
            Diversity.HIGH: 0.2
        }

    @staticmethod
    def types():
        return [k for k in Diversity.__dict__ if not k.startswith('__') and not k == 'types']


@dataclass
class TreeNode:
    """Represents a node level."""
    name: str = field()
    description: str = field()
    children: Dict[str, 'TreeNode'] = field(default_factory=dict)
    parent: Optional['TreeNode'] = field(default=None)
    level: int = field(default=0)

    def add_child(self, child: 'TreeNode'):
        """Add a child node"""
        child.parent = self
        child.level = self.level + 1
        self.children[child.name] = child

    def del_child(self, child_name: str):
        """Delete a child node"""
        if child_name in self.children:
            del self.children[child_name]

    def get_all_descendants(self) -> List['TreeNode']:
        """Get all descendant nodes"""
        descendants = []
        for child in self.children.values():
            descendants.append(child)
            descendants.extend(child.get_all_descendants())
        return descendants


# ------------------------------------------ Tool Generation ----------------------------------------------- #

class ToolBuildConfig(BaseConfig):
    llm_config: Optional[ModelConfig] = None
    source_paths: List[str] = None


@dataclass
class ToolSpec:
    """Tool specification structure."""
    name: str = field()
    description: str = field()
    category: str = field(default="")
    tree_node: TreeNode = field(default=None)
    parameters: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    output_parameters: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    capabilities: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    constraints: List[str] = field(default_factory=list)
    diversity: str = field(default=Diversity.MEDIUM)
    complexity: str = field(default=Complexity.MEDIUM)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GeneratedTool:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    spec: ToolSpec = field(default=None)
    tool_cls: Optional[type] = field(default=None)
    tool_instance: Optional[Union[BaseTool, AsyncBaseTool]] = field(default=None)
    examples: List[Dict[str, Any]] = field(default_factory=list)
    complexity_score: float = field(default=0.1)
    diversity_score: float = field(default=0.1)
    active: bool = field(default=True)
    success_rate: float = field(default=1.0)
    timeout_rate: float = field(default=1e-5)
    error_rate: float = field(default=1e-6)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.spec.name,
            "description": self.spec.description,
            "input_params": self.spec.input_params,
            "output_params": self.spec.output_params,
            "metadata": {
                "category": self.spec.category,
                "functionalities": self.spec.capabilities,
                "dependencies": self.spec.dependencies,
                "complexity": self.spec.complexity,
                "diversity": self.spec.diversity,
                "active": self.active
            },
            "examples": self.examples
        }


@dataclass
class ToolCallResult:
    step: int = field(default=0)
    tool_name: str = field(default="")
    tool_call_id: str = field(default=None)
    parameters: Dict[str, Any] = field(default_factory=dict)
    result: Optional[ActionResult] = None
    execution_time: float = field(default=0.)
    success: bool = field(default=True)
    error: Optional[str] = field(default=None)


# ------------------------------------------ Task Generation ----------------------------------------------- #

class TaskType(Enum):
    SINGLE_TOOL = "single_tool"
    MULTI_TOOL = "multi_tool"
    TOOL_CHAIN = "tool_chain"
    PARALLEL_TOOL = "parallel_tool"


@dataclass
class GeneratedTask:
    question: str = field(default="")
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    description: str = field(default="")
    expected_tools: List[str] = field(default_factory=list)
    expected_output: str = field(default="")
    complexity: int = field(default=1)
    category: str = field(default="")
    task_type: TaskType = field(default=TaskType.SINGLE_TOOL)
    context: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


# ------------------------------------------ Task Solve ----------------------------------------------- #

class StrategyType(Enum):
    """Task process strategy type."""
    AUTO = "auto"
    SINGLE_STEP = "single_step"
    MULTI_STEP = "multi_step"
    PARALLEL = "parallel"
    ITERATIVE = "iterative"
