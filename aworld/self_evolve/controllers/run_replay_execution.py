"""Typed execution controller for one candidate's authoritative replay."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from aworld.self_evolve.budget import CandidateAttemptStage
from aworld.self_evolve.concurrency import SelfEvolveExecutionTelemetry
from aworld.self_evolve.controllers.run_execution import (
    CandidateEvaluationRequest,
    CandidateEvaluationResult,
    CandidateFeedbackBuilder,
    CandidateReplayAdmissionResult,
    terminal_candidate_evaluation_result,
)
from aworld.self_evolve.controllers.screening_execution import (
    _budget_usage_for_attempt_event,
)
from aworld.self_evolve.controllers.run_telemetry import (
    _stage_telemetry_usage_delta,
    _stage_telemetry_usage_snapshot,
    _telemetry_usage_with_observed_wall,
)
from aworld.self_evolve.datasets import SelfEvolveDataset
from aworld.self_evolve.failure_events import (
    FailureOwner,
    FailureScope,
    FailureStage,
    ReplayFailureEvent,
)
from aworld.self_evolve.replay import CandidateReplayResult
from aworld.self_evolve.sanitization import sanitize_text
from aworld.self_evolve.types import GateResult


CandidateReplayExecutor = Callable[
    ...,
    Awaitable[
        tuple[
            CandidateReplayResult | None,
            SelfEvolveDataset | None,
            GateResult | None,
        ]
    ],
]
CandidateReplayGateEvaluator = Callable[..., GateResult | None]
TypedGateFailureMapper = Callable[[GateResult], GateResult]


@dataclass(frozen=True)
class CandidateReplayExecutionRequest:
    """Frozen candidate request plus its successful replay admission."""

    evaluation: CandidateEvaluationRequest
    admission: CandidateReplayAdmissionResult

    def __post_init__(self) -> None:
        if not isinstance(self.evaluation, CandidateEvaluationRequest):
            raise TypeError("replay execution evaluation request must be typed")
        if not isinstance(self.admission, CandidateReplayAdmissionResult):
            raise TypeError("replay execution admission result must be typed")
        if self.admission.terminal_result is not None:
            raise ValueError("terminal replay admission cannot be executed")


@dataclass(frozen=True)
class CandidateReplayExecutionRuntime:
    """Runner-owned execution seams injected into replay orchestration."""

    replay_candidate: CandidateReplayExecutor
    execution_telemetry: SelfEvolveExecutionTelemetry
    replay_confidence_gate: CandidateReplayGateEvaluator
    replay_evaluator_admission_gate: CandidateReplayGateEvaluator
    typed_gate_failure: TypedGateFailureMapper
    feedback_builder: CandidateFeedbackBuilder

    def __post_init__(self) -> None:
        for field_name in (
            "replay_candidate",
            "replay_confidence_gate",
            "replay_evaluator_admission_gate",
            "typed_gate_failure",
            "feedback_builder",
        ):
            if not callable(getattr(self, field_name)):
                raise TypeError(f"{field_name} must be callable")
        if not isinstance(
            self.execution_telemetry,
            SelfEvolveExecutionTelemetry,
        ):
            raise TypeError("execution_telemetry must be typed")


@dataclass(frozen=True)
class CandidateReplayExecutionResult:
    """Settled replay evidence and optional terminal evaluator admission."""

    gate_results: tuple[GateResult, ...]
    replay_result: CandidateReplayResult | None
    replay_dataset: SelfEvolveDataset | None
    replay_started: bool
    terminal_result: CandidateEvaluationResult | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "gate_results", tuple(self.gate_results))
        if not all(isinstance(gate, GateResult) for gate in self.gate_results):
            raise TypeError("replay execution gate_results must be typed")
        if self.replay_result is not None and not isinstance(
            self.replay_result,
            CandidateReplayResult,
        ):
            raise TypeError("replay_result must be typed when present")
        if self.replay_dataset is not None and not isinstance(
            self.replay_dataset,
            SelfEvolveDataset,
        ):
            raise TypeError("replay_dataset must be typed when present")


def _infrastructure_replay_gate(
    *,
    candidate_id: str,
    replay_started: bool,
    error: Exception,
) -> GateResult:
    error_message = sanitize_text(str(error), max_chars=2_000)
    failure_event = ReplayFailureEvent(
        code="candidate_replay_infrastructure_error",
        owner=FailureOwner.INFRASTRUCTURE,
        stage=FailureStage.EVALUATION,
        scope=FailureScope.SHARED_RUN,
        repairable=True,
        category="measurement_execution",
        summary=(
            error_message
            or "candidate replay backend failed without diagnostics"
        ),
        diagnostics={
            "error_type": type(error).__name__,
            "candidate_id": candidate_id,
            "replay_started": replay_started,
        },
    )
    return GateResult(
        gate_name="candidate_replay",
        passed=False,
        reason=(
            "candidate replay backend failed: " + error_message
            if error_message
            else "candidate replay backend failed"
        ),
        details={
            "failure_class": "infrastructure",
            "failure_owner": FailureOwner.INFRASTRUCTURE.value,
            "failure_scope": FailureScope.SHARED_RUN.value,
            "failure_stage": FailureStage.EVALUATION.value,
            "repairable": True,
            "next_action": "retry_infrastructure",
            "resume_safe": True,
            "code": failure_event.code,
            "type": type(error).__name__,
            "error": error_message,
            "failure_event": failure_event.to_dict(),
            "causal_failure_events": [failure_event.to_dict()],
        },
    )


async def execute_candidate_replay(
    request: CandidateReplayExecutionRequest,
    runtime: CandidateReplayExecutionRuntime,
) -> CandidateReplayExecutionResult:
    """Execute admitted replay, settle telemetry, and admit its evidence."""

    evaluation = request.evaluation
    admission = request.admission
    gate_results = list(admission.gate_results)
    replay_budget = admission.replay_budget
    replay_case_count = admission.replay_case_count
    replay_started = False
    replay_result: CandidateReplayResult | None = None
    replay_dataset: SelfEvolveDataset | None = None
    replay_stage_started_at: float | None = None
    replay_telemetry_before = None
    replay_telemetry_after = None

    def replay_lifecycle(
        stage: str,
        payload: Mapping[str, object],
    ) -> None:
        del payload
        nonlocal replay_started
        if stage == "replay_started":
            replay_started = True
        tracker = evaluation.attempt_tracker
        attempt_key = evaluation.attempt_key
        if tracker is None or attempt_key is None:
            return
        if (
            stage == "adaptation_completed"
            and tracker.last_stage(attempt_key)
            is CandidateAttemptStage.LOCAL_GATES
        ):
            tracker.emit(
                attempt_key,
                CandidateAttemptStage.ADAPTATION,
                case_count=replay_case_count,
            )
        elif stage == "replay_started":
            tracker.emit(
                attempt_key,
                CandidateAttemptStage.PAIRED_REPLAY_STARTED,
                case_count=replay_case_count,
                usage=(
                    _budget_usage_for_attempt_event(replay_budget)
                    if replay_budget is not None
                    else None
                ),
            )
        elif stage == "replay_evidence_reused":
            tracker.emit(
                attempt_key,
                CandidateAttemptStage.REPLAY_EVIDENCE_REUSED,
                case_count=replay_case_count,
            )
        elif stage == "replay_completed":
            tracker.emit(
                attempt_key,
                CandidateAttemptStage.PAIRED_REPLAY_COMPLETED,
                case_count=replay_case_count,
            )
        elif stage == "replay_comparable":
            tracker.emit(
                attempt_key,
                CandidateAttemptStage.PAIRED_REPLAY_COMPARABLE,
                case_count=replay_case_count,
            )

    if not admission.capability_blocked:
        replay_stage_started_at = time.monotonic()
        replay_telemetry_before = _stage_telemetry_usage_snapshot(
            runtime.execution_telemetry,
            "replay",
        )
        try:
            replay_result, replay_dataset, replay_gate = (
                await runtime.replay_candidate(
                    run_id=evaluation.run_id,
                    target=evaluation.target,
                    dataset=evaluation.dataset,
                    selected_candidate=evaluation.candidate,
                    apply_policy=evaluation.apply_policy,
                    baseline_replay_dir=evaluation.baseline_replay_dir,
                    lifecycle_callback=replay_lifecycle,
                    source_disposition=evaluation.source_disposition,
                )
            )
        except Exception as exc:
            replay_result = None
            replay_dataset = None
            replay_gate = _infrastructure_replay_gate(
                candidate_id=evaluation.candidate.candidate_id,
                replay_started=replay_started,
                error=exc,
            )
        if replay_gate is not None:
            gate_results.append(runtime.typed_gate_failure(replay_gate))
        replay_telemetry_after = _stage_telemetry_usage_snapshot(
            runtime.execution_telemetry,
            "replay",
        )

    if replay_budget is not None:
        budget_context = evaluation.budget_context
        if budget_context is None:
            raise RuntimeError("replay reservation requires a budget context")
        if replay_started:
            if replay_telemetry_before is None or replay_telemetry_after is None:
                raise RuntimeError("started replay requires telemetry snapshots")
            replay_usage = _stage_telemetry_usage_delta(
                replay_telemetry_before,
                replay_telemetry_after,
            )
            if replay_stage_started_at is not None:
                replay_usage = _telemetry_usage_with_observed_wall(
                    replay_usage,
                    elapsed_seconds=time.monotonic() - replay_stage_started_at,
                )
            budget_context.debit(
                replay_budget,
                usage_observation=replay_usage.observation,
                actual_source=replay_usage.source,
            )
        else:
            budget_context.release(
                replay_budget,
                reason_code=(
                    "capability_gate_blocked"
                    if admission.capability_blocked
                    else "replay_not_started"
                ),
            )

    replay_confidence_gate = runtime.replay_confidence_gate(
        replay_result,
        dataset=evaluation.dataset,
        apply_policy=evaluation.apply_policy,
    )
    if replay_confidence_gate is not None:
        gate_results.append(replay_confidence_gate)

    evaluator_admission_gate = runtime.replay_evaluator_admission_gate(
        replay_result,
        apply_policy=evaluation.apply_policy,
    )
    if evaluator_admission_gate is not None:
        evaluator_admission_gate = runtime.typed_gate_failure(
            evaluator_admission_gate
        )
        gate_results.append(evaluator_admission_gate)
        if not evaluator_admission_gate.passed:
            tracker = evaluation.attempt_tracker
            attempt_key = evaluation.attempt_key
            if (
                tracker is not None
                and attempt_key is not None
                and not tracker.terminal(attempt_key)
            ):
                tracker.emit(
                    attempt_key,
                    CandidateAttemptStage.REJECTED,
                    reason_code="deterministic_replay_evidence_regressed",
                )
            return CandidateReplayExecutionResult(
                gate_results=tuple(gate_results),
                replay_result=replay_result,
                replay_dataset=replay_dataset,
                replay_started=replay_started,
                terminal_result=terminal_candidate_evaluation_result(
                    candidate=evaluation.candidate,
                    iteration_number=evaluation.iteration_number,
                    candidate_number=evaluation.candidate_number,
                    candidate_count=evaluation.candidate_count,
                    gate_results=gate_results,
                    feedback_builder=runtime.feedback_builder,
                    status="rejected",
                    replay_result=replay_result,
                    replay_dataset=replay_dataset,
                ),
            )

    return CandidateReplayExecutionResult(
        gate_results=tuple(gate_results),
        replay_result=replay_result,
        replay_dataset=replay_dataset,
        replay_started=replay_started,
    )
