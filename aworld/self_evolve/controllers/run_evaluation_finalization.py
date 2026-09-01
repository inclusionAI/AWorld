"""Typed candidate evaluation finalization and outcome construction."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from aworld.self_evolve.budget import CandidateAttemptStage
from aworld.self_evolve.campaign_policy import is_verified_apply_policy
from aworld.self_evolve.causal_admission import (
    causal_admission_prerequisite_blocker,
)
from aworld.self_evolve.controllers.measurement import (
    measurement_promotion_gate,
)
from aworld.self_evolve.controllers.run_evaluation_execution import (
    CandidateEvaluationExecutionResult,
)
from aworld.self_evolve.controllers.run_execution import (
    CandidateEvaluationRequest,
    CandidateEvaluationResult,
    iteration_report_item,
    iteration_state,
)
from aworld.self_evolve.controllers.run_replay_execution import (
    CandidateReplayExecutionResult,
)
from aworld.self_evolve.measurement import (
    ControlledExperimentSpec,
    MeasurementPolicyMode,
    MeasurementSummary,
)
from aworld.self_evolve.types import EvaluationSummary, GateResult


MeasurementMaterializer = Callable[..., MeasurementSummary]
TypedGateFailureMapper = Callable[[GateResult], GateResult]
CandidateFeedbackBuilder = Callable[..., tuple[EvaluationSummary, ...]]


@dataclass(frozen=True)
class CandidateEvaluationFinalizationRequest:
    """Frozen execution evidence and optional measurement authority."""

    evaluation: CandidateEvaluationRequest
    replay: CandidateReplayExecutionResult
    execution: CandidateEvaluationExecutionResult
    measurement_experiment: ControlledExperimentSpec | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.evaluation, CandidateEvaluationRequest):
            raise TypeError("evaluation finalization request must be typed")
        if not isinstance(self.replay, CandidateReplayExecutionResult):
            raise TypeError("evaluation finalization replay result must be typed")
        if self.replay.terminal_result is not None:
            raise ValueError("terminal replay result cannot be finalized again")
        if not isinstance(self.execution, CandidateEvaluationExecutionResult):
            raise TypeError("evaluation finalization execution result must be typed")
        if self.measurement_experiment is not None and not isinstance(
            self.measurement_experiment,
            ControlledExperimentSpec,
        ):
            raise TypeError("measurement_experiment must be typed when present")


@dataclass(frozen=True)
class CandidateEvaluationFinalizationPolicy:
    """Runner-owned policy for measurement and final candidate admission."""

    measurement_mode: MeasurementPolicyMode
    auto_apply_target_types: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.measurement_mode, MeasurementPolicyMode):
            raise TypeError("measurement_mode must be typed")
        normalized_target_types = tuple(
            target_type.strip()
            for target_type in self.auto_apply_target_types
            if isinstance(target_type, str) and target_type.strip()
        )
        if len(normalized_target_types) != len(self.auto_apply_target_types):
            raise ValueError("auto_apply_target_types must be non-empty strings")
        object.__setattr__(
            self,
            "auto_apply_target_types",
            normalized_target_types,
        )


@dataclass(frozen=True)
class CandidateEvaluationFinalizationRuntime:
    """Injected materialization and historical feedback seams."""

    materialize_measurement: MeasurementMaterializer
    typed_gate_failure: TypedGateFailureMapper
    feedback_builder: CandidateFeedbackBuilder

    def __post_init__(self) -> None:
        for field_name in (
            "materialize_measurement",
            "typed_gate_failure",
            "feedback_builder",
        ):
            if not callable(getattr(self, field_name)):
                raise TypeError(f"{field_name} must be callable")


def finalize_candidate_evaluation(
    request: CandidateEvaluationFinalizationRequest,
    policy: CandidateEvaluationFinalizationPolicy,
    runtime: CandidateEvaluationFinalizationRuntime,
) -> CandidateEvaluationResult:
    """Finalize measurement, gates, lifecycle, and iteration outcome."""

    evaluation = request.evaluation
    replay = request.replay
    execution = request.execution
    gate_results = list(execution.gate_results)

    if is_verified_apply_policy(evaluation.apply_policy):
        target_type = evaluation.target.identity.target_type
        gate_results.append(
            GateResult(
                gate_name="auto_apply_target_type",
                passed=target_type in policy.auto_apply_target_types,
                reason=(
                    "target type is allowlisted for auto apply"
                    if target_type in policy.auto_apply_target_types
                    else "target type is not allowlisted for auto apply"
                ),
                details={
                    "target_type": target_type,
                    "auto_apply_target_types": list(
                        policy.auto_apply_target_types
                    ),
                },
            )
        )

    measurement_summary: MeasurementSummary | None = None
    candidate_prerequisite_blocked = any(
        causal_admission_prerequisite_blocker(
            gate_name=gate.gate_name,
            passed=gate.passed,
            details=gate.details,
        )
        for gate in gate_results
    )
    if (
        request.measurement_experiment is not None
        and not candidate_prerequisite_blocked
    ):
        try:
            materialized_summary = runtime.materialize_measurement(
                experiment=request.measurement_experiment,
                materialization_run_id=evaluation.run_id,
                candidate=evaluation.candidate,
                dataset=evaluation.dataset,
                replay_result=replay.replay_result,
                replay_dataset=replay.replay_dataset,
                baseline_summary=execution.baseline_summary,
                candidate_summary=execution.candidate_summary,
                candidate_count=evaluation.candidate_count,
                authoritative_candidate_count=1,
                target_selection_report=evaluation.target_selection_report,
            )
            if not isinstance(materialized_summary, MeasurementSummary):
                raise TypeError("materialized measurement summary must be typed")
            measurement_summary = materialized_summary
            if policy.measurement_mode is MeasurementPolicyMode.REQUIRED:
                gate_results.append(
                    measurement_promotion_gate(measurement_summary)
                )
        except (OSError, TypeError, ValueError) as exc:
            if policy.measurement_mode is MeasurementPolicyMode.REQUIRED:
                gate_results.append(
                    GateResult(
                        gate_name="trusted_improvement_measurement",
                        passed=False,
                        reason="controlled measurement could not be finalized",
                        details={
                            "failure_class": "measurement",
                            "code": "measurement_materialization_failed",
                            "type": type(exc).__name__,
                            "reason": str(exc),
                        },
                    )
                )

    gate_results = [
        runtime.typed_gate_failure(gate) for gate in gate_results
    ]
    failed_gates = [gate for gate in gate_results if not gate.passed]
    proposal_blocked = any(
        isinstance(gate.details, Mapping)
        and gate.details.get("failure_class") in {"infrastructure", "budget"}
        for gate in failed_gates
    )
    status = (
        "accepted"
        if (
            (
                evaluation.source_disposition.requires_fresh_evaluation
                and execution.fresh_evaluation_completed
                and not failed_gates
            )
            or (
                not evaluation.source_disposition.requires_fresh_evaluation
                and not is_verified_apply_policy(evaluation.apply_policy)
                and not proposal_blocked
            )
            or (
                is_verified_apply_policy(evaluation.apply_policy)
                and not failed_gates
            )
        )
        else "rejected"
    )

    tracker = evaluation.attempt_tracker
    attempt_key = evaluation.attempt_key
    if (
        tracker is not None
        and attempt_key is not None
        and not tracker.terminal(attempt_key)
    ):
        infrastructure_failure = any(
            not gate.passed
            and isinstance(gate.details, Mapping)
            and gate.details.get("failure_class") == "infrastructure"
            for gate in gate_results
        )
        tracker.emit(
            attempt_key,
            (
                CandidateAttemptStage.SELECTED
                if status == "accepted"
                else (
                    CandidateAttemptStage.BLOCKED
                    if infrastructure_failure
                    else CandidateAttemptStage.REJECTED
                )
            ),
            reason_code=(
                "candidate_selected"
                if status == "accepted"
                else (
                    "candidate_evaluation_blocked"
                    if infrastructure_failure
                    else "candidate_evaluation_rejected"
                )
            ),
        )

    report_item = iteration_report_item(
        iteration_number=evaluation.iteration_number,
        candidate_number=evaluation.candidate_number,
        candidate_count=evaluation.candidate_count,
        candidate=evaluation.candidate,
        status=status,
        baseline_summary=execution.baseline_summary,
        candidate_summary=execution.candidate_summary,
        held_out_summary=execution.held_out_summary,
        failed_gates=failed_gates,
        regression_evidence=execution.regression_evidence,
        challenge_report=execution.challenge_report,
    )
    feedback = runtime.feedback_builder(
        candidate=evaluation.candidate,
        baseline_summary=execution.baseline_summary,
        candidate_summary=execution.candidate_summary,
        held_out_summary=execution.held_out_summary,
        failed_gates=failed_gates,
    )
    state = iteration_state(
        candidate=evaluation.candidate,
        baseline_summary=execution.baseline_summary,
        candidate_summary=execution.candidate_summary,
        held_out_summary=execution.held_out_summary,
        replay_result=replay.replay_result,
        replay_dataset=replay.replay_dataset,
        gate_results=gate_results,
        feedback=feedback,
        status=status,
        regression_evidence=execution.regression_evidence,
        challenge_report=execution.challenge_report,
    )
    if measurement_summary is not None:
        state["measurement_summary"] = measurement_summary
        report_item["measurement"] = measurement_summary.to_dict()
    return CandidateEvaluationResult.from_tuple(
        (state, report_item, feedback)
    )
