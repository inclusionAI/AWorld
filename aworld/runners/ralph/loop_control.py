"""Pure acceptance and limit decisions shared by long-running loops.

The module deliberately does not execute a Task, validation command, or model
call.  Executors supply typed evidence; this kernel only decides whether that
evidence satisfies the goal or whether another attempt is allowed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class LoopDisposition(str, Enum):
    COMPLETE = "complete"
    CONTINUE = "continue"
    LIMIT_REACHED = "limit_reached"


@dataclass(frozen=True, slots=True)
class AcceptanceEvidence:
    """Bounded evidence supplied after one execution attempt."""

    semantic_succeeded: bool
    completion_claimed: bool = True
    verification_required: bool = False
    verification_passed: bool | None = None

    def __post_init__(self) -> None:
        for name in (
            "semantic_succeeded",
            "completion_claimed",
            "verification_required",
        ):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be a boolean")
        if self.verification_passed is not None and not isinstance(
            self.verification_passed, bool
        ):
            raise TypeError("verification_passed must be a boolean or None")

    @property
    def acceptance_satisfied(self) -> bool:
        return bool(
            self.semantic_succeeded
            and self.completion_claimed
            and (
                not self.verification_required
                or self.verification_passed is True
            )
        )


@dataclass(frozen=True, slots=True)
class ExecutionLimits:
    """Resource limits; reaching one never means that acceptance succeeded."""

    max_iterations: int | None = None

    def __post_init__(self) -> None:
        value = self.max_iterations
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int)
        ):
            raise TypeError("max_iterations must be an integer or None")
        if value is not None and value < 0:
            raise ValueError("max_iterations must be non-negative or None")

    def reached(self, current_iteration: int) -> bool:
        if isinstance(current_iteration, bool) or not isinstance(
            current_iteration, int
        ):
            raise TypeError("current_iteration must be an integer")
        if current_iteration < 1:
            raise ValueError("current_iteration must be positive")
        return bool(
            self.max_iterations is not None
            and self.max_iterations > 0
            and current_iteration >= self.max_iterations
        )


@dataclass(frozen=True, slots=True)
class LoopDecision:
    disposition: LoopDisposition
    reason: str
    acceptance_satisfied: bool


def _unsatisfied_reason(evidence: AcceptanceEvidence) -> str:
    if not evidence.semantic_succeeded:
        return "semantic_incomplete"
    if not evidence.completion_claimed:
        return "completion_claim_missing"
    if evidence.verification_required and evidence.verification_passed is False:
        return "verification_failed"
    if evidence.verification_required:
        return "verification_missing"
    return "acceptance_unsatisfied"


def decide_after_attempt(
    evidence: AcceptanceEvidence,
    *,
    current_iteration: int,
    limits: ExecutionLimits | None = None,
) -> LoopDecision:
    """Decide after an attempt, keeping acceptance separate from resources."""

    limits = limits or ExecutionLimits()
    if evidence.acceptance_satisfied:
        return LoopDecision(
            disposition=LoopDisposition.COMPLETE,
            reason="acceptance_satisfied",
            acceptance_satisfied=True,
        )
    if limits.reached(current_iteration):
        return LoopDecision(
            disposition=LoopDisposition.LIMIT_REACHED,
            reason="iteration_limit_reached",
            acceptance_satisfied=False,
        )
    return LoopDecision(
        disposition=LoopDisposition.CONTINUE,
        reason=_unsatisfied_reason(evidence),
        acceptance_satisfied=False,
    )


__all__ = [
    "AcceptanceEvidence",
    "ExecutionLimits",
    "LoopDecision",
    "LoopDisposition",
    "decide_after_attempt",
]
