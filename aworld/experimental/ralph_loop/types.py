# coding: utf-8
# Copyright (c) inclusionAI.
from dataclasses import dataclass, field
from typing import Callable, Any


@dataclass
class CompletionCriteria:
    max_iterations: int = field(default=10000)
    timeout: int = field(default=0)
    max_tokens: int = field(default=0)
    max_cost: float = field(default=0)
    max_endless: int = field(default=20)
    max_consecutive_failures: int = field(default=10)
    answer: Any = field(default=None)
    custom_stop: Callable[..., bool] = field(default=None)
