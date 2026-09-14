"""Task-generic elastic Agent decision budget contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class StepBudgetDecisionCode(str, Enum):
    WITHIN_CURRENT_LIMIT = "within_current_limit"
    PROGRESS_EXTENSION_GRANTED = "progress_extension_granted"
    UNOBSERVABLE_PROGRESS_EXTENSION_GRANTED = (
        "unobservable_progress_extension_granted"
    )
    NO_NEW_GOAL_PROGRESS = "no_new_goal_progress"
    GOAL_PROGRESS_EVIDENCE_MISSING = "goal_progress_evidence_missing"
    GOAL_PROGRESS_STALE = "goal_progress_stale"
    HARD_LIMIT_REACHED = "hard_limit_reached"


@dataclass(frozen=True, slots=True)
class ElasticStepBudgetPolicy:
    soft_limit: int
    extension_steps: int
    hard_limit: int
    recent_progress_window_steps: int

    def __post_init__(self) -> None:
        for name in (
            "soft_limit",
            "extension_steps",
            "hard_limit",
            "recent_progress_window_steps",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.hard_limit <= self.soft_limit:
            raise ValueError("hard_limit must be greater than soft_limit")


@dataclass(frozen=True, slots=True)
class ElasticStepBudgetState:
    effective_limit: int
    consumed_goal_progress_count: int = 0
    extension_count: int = 0
    total_extended_steps: int = 0


@dataclass(frozen=True, slots=True)
class ElasticStepBudgetDecision:
    terminate: bool
    code: StepBudgetDecisionCode
    current_step: int
    soft_limit: int
    effective_limit: int
    hard_limit: int
    observed_goal_progress_count: int
    consumed_goal_progress_count: int
    last_goal_progress_agent_step: int | None
    extension_count: int
    total_extended_steps: int

    def to_dict(self) -> dict[str, int | str | bool | None]:
        return {
            "schema_version": "aworld.context.elastic-step-budget/v1",
            "decision": self.code.value,
            "terminate": self.terminate,
            "current_step": self.current_step,
            "soft_limit": self.soft_limit,
            "effective_limit": self.effective_limit,
            "hard_limit": self.hard_limit,
            "observed_goal_progress_count": self.observed_goal_progress_count,
            "consumed_goal_progress_count": self.consumed_goal_progress_count,
            "last_goal_progress_agent_step": self.last_goal_progress_agent_step,
            "extension_count": self.extension_count,
            "total_extended_steps": self.total_extended_steps,
        }


def evaluate_elastic_step_budget(
    *,
    policy: ElasticStepBudgetPolicy,
    current_step: int,
    observed_goal_progress_count: int,
    last_goal_progress_agent_step: int | None,
    goal_progress_observable: bool | None = None,
    state: ElasticStepBudgetState | None = None,
) -> tuple[ElasticStepBudgetDecision, ElasticStepBudgetState]:
    """Evaluate one budget boundary without interpreting task or Tool content."""
    for name, value in (
        ("current_step", current_step),
        ("observed_goal_progress_count", observed_goal_progress_count),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")
    if last_goal_progress_agent_step is not None and (
        isinstance(last_goal_progress_agent_step, bool)
        or not isinstance(last_goal_progress_agent_step, int)
        or last_goal_progress_agent_step < 0
    ):
        raise ValueError(
            "last_goal_progress_agent_step must be a non-negative integer or None"
        )
    if goal_progress_observable is not None and not isinstance(
        goal_progress_observable, bool
    ):
        raise ValueError("goal_progress_observable must be a boolean or None")
    state = state or ElasticStepBudgetState(effective_limit=policy.soft_limit)
    if current_step < state.effective_limit:
        code = StepBudgetDecisionCode.WITHIN_CURRENT_LIMIT
        terminate = False
        next_state = state
    elif state.effective_limit >= policy.hard_limit:
        code = StepBudgetDecisionCode.HARD_LIMIT_REACHED
        terminate = True
        next_state = state
    elif goal_progress_observable is False:
        effective_limit = min(
            state.effective_limit + policy.extension_steps,
            policy.hard_limit,
        )
        next_state = ElasticStepBudgetState(
            effective_limit=effective_limit,
            consumed_goal_progress_count=state.consumed_goal_progress_count,
            extension_count=state.extension_count + 1,
            total_extended_steps=effective_limit - policy.soft_limit,
        )
        code = StepBudgetDecisionCode.UNOBSERVABLE_PROGRESS_EXTENSION_GRANTED
        terminate = current_step >= effective_limit
    elif observed_goal_progress_count <= state.consumed_goal_progress_count:
        code = StepBudgetDecisionCode.NO_NEW_GOAL_PROGRESS
        terminate = True
        next_state = state
    elif last_goal_progress_agent_step is None:
        code = StepBudgetDecisionCode.GOAL_PROGRESS_EVIDENCE_MISSING
        terminate = True
        next_state = state
    elif current_step - last_goal_progress_agent_step > policy.recent_progress_window_steps:
        code = StepBudgetDecisionCode.GOAL_PROGRESS_STALE
        terminate = True
        next_state = state
    else:
        effective_limit = min(
            state.effective_limit + policy.extension_steps,
            policy.hard_limit,
        )
        next_state = ElasticStepBudgetState(
            effective_limit=effective_limit,
            consumed_goal_progress_count=observed_goal_progress_count,
            extension_count=state.extension_count + 1,
            total_extended_steps=effective_limit - policy.soft_limit,
        )
        code = StepBudgetDecisionCode.PROGRESS_EXTENSION_GRANTED
        terminate = current_step >= effective_limit
    decision = ElasticStepBudgetDecision(
        terminate=terminate,
        code=code,
        current_step=current_step,
        soft_limit=policy.soft_limit,
        effective_limit=next_state.effective_limit,
        hard_limit=policy.hard_limit,
        observed_goal_progress_count=observed_goal_progress_count,
        consumed_goal_progress_count=next_state.consumed_goal_progress_count,
        last_goal_progress_agent_step=last_goal_progress_agent_step,
        extension_count=next_state.extension_count,
        total_extended_steps=next_state.total_extended_steps,
    )
    return decision, next_state


__all__ = [
    "ElasticStepBudgetDecision",
    "ElasticStepBudgetPolicy",
    "ElasticStepBudgetState",
    "StepBudgetDecisionCode",
    "evaluate_elastic_step_budget",
]
