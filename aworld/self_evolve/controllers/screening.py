"""Shared control-health and deadline policy for candidate screening."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from aworld.self_evolve.budget import BudgetDecision
from aworld.self_evolve.failure_events import (
    FailureOwner,
    FailureScope,
    FailureStage,
    ReplayExecutionStatus,
    ReplayFailureEvent,
)
from aworld.self_evolve.types import GateResult


SCREENING_BUDGET_CENSORED_CODE = "screening_budget_censored"


@dataclass(frozen=True)
class CandidateScreeningController:
    """Owns screening policies that must be consistent across candidates."""

    support_control_failure_patience: int = 3

    def __post_init__(self) -> None:
        if (
            isinstance(self.support_control_failure_patience, bool)
            or self.support_control_failure_patience <= 0
        ):
            raise ValueError("support_control_failure_patience must be positive")

    def hard_limit_seconds(self, decision: BudgetDecision) -> float | None:
        """Return the reserved wall envelope as an executable stage deadline."""

        usage = decision.estimate.resolved_usage()
        if usage is None or usage.wall_seconds <= 0:
            return None
        return float(usage.wall_seconds)

    def support_specific_control_circuit_breaker_gate(
        self,
        *,
        control_identity: Mapping[str, object],
        control_observations: Mapping[str, Mapping[str, object]],
    ) -> GateResult | None:
        """Skip an exact support/envelope control after repeated timeouts."""

        fingerprint = control_identity.get("control_identity_fingerprint")
        if not isinstance(fingerprint, str) or not fingerprint:
            return None
        observation = control_observations.get(fingerprint)
        if not isinstance(observation, Mapping):
            return None
        attempts = _non_negative_int(observation.get("baseline_attempt_count"))
        successes = _non_negative_int(observation.get("baseline_success_count"))
        timeouts = _non_negative_int(observation.get("baseline_timeout_count"))
        failure_patience = self.support_control_failure_patience
        if (
            attempts < failure_patience
            or successes > 0
            or timeouts < failure_patience
        ):
            return None
        event = ReplayFailureEvent(
            code="screening_support_control_circuit_open",
            owner=FailureOwner.FRAMEWORK,
            stage=FailureStage.EVALUATION,
            scope=FailureScope.MEMBER,
            repairable=True,
            category="measurement_control",
            summary=(
                "exact support-specific screening control was skipped after "
                "repeated baseline timeouts"
            ),
            diagnostics={
                "control_identity_fingerprint": fingerprint,
                "baseline_attempt_count": attempts,
                "baseline_timeout_count": timeouts,
                "failure_patience": failure_patience,
            },
        )
        payload = event.to_dict()
        timeout_event = ReplayFailureEvent(
            code="replay_member_phase_timeout",
            owner=FailureOwner.FRAMEWORK,
            stage=FailureStage.EVALUATION,
            scope=FailureScope.MEMBER,
            repairable=True,
            category="measurement_control",
            summary="historical baseline attempts exhausted this timeout envelope",
            diagnostics={
                "phase": "baseline",
                "termination_budget_axis": "member_phase_wall_seconds",
                "control_identity_fingerprint": fingerprint,
            },
        )
        return GateResult(
            gate_name="candidate_replay",
            passed=False,
            reason=(
                "support-specific control circuit is open for this exact "
                "timeout envelope"
            ),
            details={
                "code": "screening_support_control_circuit_open",
                "failure_class": "framework",
                "failure_owner": FailureOwner.FRAMEWORK.value,
                "failure_scope": FailureScope.MEMBER.value,
                "failure_stage": FailureStage.EVALUATION.value,
                "repairable": True,
                "screening_outcome": "invalid_control",
                "candidate_execution_observed": False,
                "candidate_intervention_required": True,
                "candidate_intervention_observed": None,
                "baseline_status": ReplayExecutionStatus.FAILED.value,
                "candidate_status": ReplayExecutionStatus.BLOCKED.value,
                "baseline_failure": timeout_event.to_dict(),
                "control_identity": dict(control_identity),
                "control_health_observation": dict(observation),
                "failure_event": payload,
                "causal_failure_events": [payload],
                "resume_safe": False,
            },
        )

    def stage_budget_censor_gate(
        self,
        *,
        hard_limit_seconds: float,
        elapsed_seconds: float,
        candidate_execution_observed: bool,
    ) -> GateResult:
        """Describe an exhausted screening envelope without candidate blame."""

        event = ReplayFailureEvent(
            code=SCREENING_BUDGET_CENSORED_CODE,
            owner=FailureOwner.FRAMEWORK,
            stage=FailureStage.EVALUATION,
            scope=FailureScope.MEMBER,
            repairable=False,
            category="candidate_screening",
            summary=(
                "candidate screening exhausted its reserved wall-time envelope "
                "before a directional comparison completed"
            ),
            diagnostics={
                "censor_basis": "stage_deadline",
                "hard_limit_seconds": hard_limit_seconds,
                "elapsed_seconds": elapsed_seconds,
                "termination_budget_axis": "screening_stage_wall_seconds",
                "candidate_execution_observed": candidate_execution_observed,
            },
        )
        payload = event.to_dict()
        return GateResult(
            gate_name="candidate_screening",
            passed=False,
            reason=(
                "representative screening reached its reserved stage deadline; "
                "authoritative replay must decide the candidate"
            ),
            details={
                "code": SCREENING_BUDGET_CENSORED_CODE,
                "failure_class": "framework",
                "failure_owner": FailureOwner.FRAMEWORK.value,
                "failure_scope": FailureScope.MEMBER.value,
                "failure_stage": FailureStage.EVALUATION.value,
                "repairable": False,
                "screening_outcome": "right_censored",
                "screening_budget_censored": True,
                "screening_censor_basis": "stage_deadline",
                "termination_budget_axis": "screening_stage_wall_seconds",
                "hard_limit_seconds": hard_limit_seconds,
                "elapsed_seconds": elapsed_seconds,
                "candidate_execution_observed": candidate_execution_observed,
                "resume_safe": False,
                "failure_event": payload,
                "causal_failure_events": [payload],
            },
        )

    def attempt_is_budget_censored(self, attempt: Mapping[str, object]) -> bool:
        details = attempt.get("details")
        return bool(
            isinstance(details, Mapping)
            and details.get("code") == SCREENING_BUDGET_CENSORED_CODE
            and details.get("screening_outcome") == "right_censored"
        )

    def gate_is_budget_censored(self, gate: GateResult | None) -> bool:
        return bool(
            gate is not None
            and isinstance(gate.details, Mapping)
            and gate.details.get("code") == SCREENING_BUDGET_CENSORED_CODE
            and gate.details.get("screening_outcome") == "right_censored"
        )


def budget_decision_wall_limit_seconds(
    decision: BudgetDecision,
) -> float | None:
    """Compatibility helper for callers migrating to the controller."""

    return CandidateScreeningController().hard_limit_seconds(decision)


def support_specific_control_circuit_breaker_gate(
    *,
    control_identity: Mapping[str, object],
    control_observations: Mapping[str, Mapping[str, object]],
    failure_patience: int = 3,
) -> GateResult | None:
    """Compatibility helper for callers migrating to the controller."""

    return CandidateScreeningController(
        support_control_failure_patience=failure_patience,
    ).support_specific_control_circuit_breaker_gate(
        control_identity=control_identity,
        control_observations=control_observations,
    )


def screening_stage_budget_censor_gate(
    *,
    hard_limit_seconds: float,
    elapsed_seconds: float,
    candidate_execution_observed: bool,
) -> GateResult:
    """Compatibility helper for callers migrating to the controller."""

    return CandidateScreeningController().stage_budget_censor_gate(
        hard_limit_seconds=hard_limit_seconds,
        elapsed_seconds=elapsed_seconds,
        candidate_execution_observed=candidate_execution_observed,
    )


def screening_attempt_is_budget_censored(
    attempt: Mapping[str, object],
) -> bool:
    return CandidateScreeningController().attempt_is_budget_censored(attempt)


def screening_gate_is_budget_censored(gate: GateResult | None) -> bool:
    return CandidateScreeningController().gate_is_budget_censored(gate)


def _non_negative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))
