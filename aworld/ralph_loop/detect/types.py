# coding: utf-8
# Copyright (c) inclusionAI.
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

from aworld.ralph_loop.state.types import LoopState, LoopContext
from aworld.ralph_loop.types import CompletionCriteria


class StopType(Enum):
    NONE = "none"

    # success
    COMPLETION = "normal_completion"
    CUSTOM_STOPPED = "custom_stopped"

    # limited
    MAX_ITERATIONS = "max_iterations"
    TIMEOUT = "timeout"
    MAX_TOKENS = "max_tokens"
    MAX_COST = "max_cost"
    MAX_ENDLESS = "max_endless"

    # failure
    MAX_CONSECUTIVE_FAILURES = "max_consecutive_failures"
    VALIDATION_FAILURE = "validation_failure"

    # interrupt
    USER_INTERRUPTED = "user_interrupted"
    EXTERNAL_SIGNAL = "external_signal"

    # system error
    SYSTEM_ERROR = "system_error"
    RESOURCE_EXHAUSTED = "resource_exhausted"

    def exit_code(self) -> int:
        mapping = {
            # success: 0
            StopType.COMPLETION: 0,
            StopType.CUSTOM_STOPPED: 0,

            # limited: 1
            StopType.MAX_ITERATIONS: 1,
            StopType.MAX_TOKENS: 1,
            StopType.TIMEOUT: 1,
            StopType.MAX_COST: 1,
            StopType.MAX_ENDLESS: 1,

            # failure: 2
            StopType.MAX_CONSECUTIVE_FAILURES: 2,
            StopType.VALIDATION_FAILURE: 2,

            # interrupt: 3
            StopType.USER_INTERRUPTED: 3,
            StopType.EXTERNAL_SIGNAL: 3,

            # system error: 4
            StopType.SYSTEM_ERROR: 4,
            StopType.RESOURCE_EXHAUSTED: 4,
        }
        return mapping.get(self, 1)

    def is_success(self) -> bool:
        return self.exit_code() == 0

    def is_failure(self) -> bool:
        return self.exit_code() == 2

    def is_error(self) -> bool:
        return self.exit_code() == 4


@dataclass
class StopState:
    loop_state: LoopState
    loop_context: LoopContext
    completion_criteria: CompletionCriteria
    metadata: Dict[str, Any] = field(default_factory=dict)

    def elapsed_time(self) -> float:
        return self.loop_state.elapsed()

    def is_within_budget(self) -> bool:
        criteria = self.completion_criteria
        if 0 < criteria.max_cost <= self.loop_state.cumulative_cost:
            return False
        return True


@dataclass
class StopDecision:
    should_stop: bool
    stop_type: StopType = StopType.NONE
    confidence: float = 1.0
    reason: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.should_stop
