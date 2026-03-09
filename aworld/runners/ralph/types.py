# coding: utf-8
# Copyright (c) inclusionAI.
from dataclasses import dataclass, field
from typing import Callable, Any


class Complexity:
    """complexity levels."""
    # one step
    TRIVIAL = "trivial"
    # few steps
    LOW = "low"
    # need plan
    MEDIUM = "medium"
    # task decomposition
    HIGH = "high"
    # multi-stage
    COMPLEX = "complex"


class ConflictStrategy:
    """Component conflict strategy."""
    MERGE: str = "merge"
    OVERWRITE: str = "overwrite"
    APPEND: str = "append"
    UPDATE: str = "update"


@dataclass
class CompletionCriteria:
    """Mission completion criteria are multi-dimension, and meeting one of them is considered complete."""
    max_iterations: int = field(default=10000)
    timeout: float = field(default=0)
    max_tokens: int = field(default=0)
    max_cost: float = field(default=0)
    max_endless: int = field(default=20)
    max_consecutive_failures: int = field(default=10)
    answer: Any = field(default=None)
    custom_stop: Callable[..., bool] = field(default=None)
