"""Typed composition of admission, replay, evaluation, and finalization."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aworld.self_evolve.controllers.measurement import (
    CandidateMeasurementController,
    MeasurementPlanningController,
    MeasurementPlanningRequest,
    MeasurementPlanningRuntime,
)
from aworld.self_evolve.controllers.measurement_execution import (
    PairedReplayExecutionController,
    PairedReplayExecutionRequest,
    PairedReplayExecutionRuntime,
)
from aworld.self_evolve.controllers.run_capability_validation import (
    CapabilityValidationPolicy,
    CapabilityValidationRequest,
    CapabilityValidationRuntime,
    validate_candidate_capabilities,
)
from aworld.self_evolve.controllers.run_evaluation_admission import (
    CandidateEvaluationAdmissionPolicy,
    CandidateEvaluationAdmissionRequest,
    CandidateEvaluationAdmissionRuntime,
    plan_candidate_evaluation_admission,
)
from aworld.self_evolve.controllers.run_evaluation_execution import (
    CandidateEvaluationExecutionPolicy,
    CandidateEvaluationExecutionRequest,
    CandidateEvaluationExecutionRuntime,
    execute_candidate_evaluation,
)
from aworld.self_evolve.controllers.run_evaluation_finalization import (
    CandidateEvaluationFinalizationPolicy,
    CandidateEvaluationFinalizationRequest,
    CandidateEvaluationFinalizationRuntime,
    finalize_candidate_evaluation,
)
from aworld.self_evolve.controllers.run_execution import (
    CandidateEvaluationRequest,
    CandidateEvaluationResult,
    CandidateLocalAdmissionPolicy,
    CandidateReplayAdmissionPolicy,
    CandidateReplayAdmissionRuntime,
    execute_candidate_local_admission,
    execute_candidate_replay_admission,
)
from aworld.self_evolve.controllers.run_regression_execution import (
    RegressionExecution,
)
from aworld.self_evolve.controllers.run_replay_execution import (
    CandidateReplayExecutionRequest,
    CandidateReplayExecutionRuntime,
    execute_candidate_replay,
)
from aworld.self_evolve.concurrency import SelfEvolveExecutionTelemetry
from aworld.self_evolve.measurement import (
    ControlledExperimentSpec,
    MeasurementPolicyMode,
)
from aworld.self_evolve.provenance import InferredNewSkillPolicy, TargetMutationIntent
from aworld.self_evolve.replay import CandidateReplayBackend
from aworld.self_evolve.replay_gates import (
    _replay_confidence_gate,
)
from aworld.self_evolve.types import EvaluationSummary, GateResult


@dataclass(frozen=True)
class CandidateIterationExecutionPolicy:
    workspace_root: Path
    max_candidate_chars: int
    allow_generated_target_mutation: bool
    allow_external_target_mutation: bool
    target_intent: TargetMutationIntent | None
    inferred_new_skill_policy: InferredNewSkillPolicy
    skip_duplicate_rejected_candidate_gate: bool
    measurement_mode: MeasurementPolicyMode
    replay_enabled: bool
    replay_backend: CandidateReplayBackend | None
    replay_repetitions_explicit: bool
    measurement_min_independent_cases: int
    baseline_replay_repetitions: int
    candidate_replay_repetitions: int
    judge_repetitions: int
    replay_candidate_limit: int
    per_attempt_replay_token_limit: int | None
    replay_tokens_per_unit: int
    evaluation_backend: object | None
    regression_suite_case_counts: tuple[int, ...]
    challenger_enabled: bool
    challenger_max_cases: int
    max_iterations: int
    min_score_delta: float
    replay_stability_margin: float
    min_eval_cases: int
    require_resource_evidence: bool
    auto_apply_target_types: tuple[str, ...]


@dataclass(frozen=True)
class CandidateIterationExecutionRuntime:
    measurement_planner: MeasurementPlanningController
    measurement_experiments: dict[object, ControlledExperimentSpec]
    environment_fingerprints: Mapping[str, str]
    capability_policy: CapabilityValidationPolicy
    capability_runtime: CapabilityValidationRuntime
    paired_replay_controller: PairedReplayExecutionController
    paired_replay_runtime: PairedReplayExecutionRuntime
    regression: RegressionExecution | Callable[..., Any]
    measurement_controller: CandidateMeasurementController
    task_batch_executor: object
    max_concurrency: int
    execution_telemetry: SelfEvolveExecutionTelemetry
    progress_callback: Callable[[str, str], Any] | None
    gate_evaluator: Callable[..., tuple[GateResult, ...]]
    reusable_baseline_case_count: Callable[..., int]
    typed_gate_failure: Callable[[GateResult], GateResult]
    feedback_builder: Callable[..., EvaluationSummary]
    replay_evaluator_admission_gate: Callable[..., GateResult | None]
    evaluate_pair: Callable[..., Any]
    evaluate_variant: Callable[..., Any]
    gate_is_replay_infrastructure_failure: Callable[[GateResult], bool]
    capability_override: Callable[..., Any] | None = None
    paired_replay_override: Callable[..., Any] | None = None


@dataclass(frozen=True)
class CandidateIterationExecution:
    policy: CandidateIterationExecutionPolicy
    runtime: CandidateIterationExecutionRuntime

    async def execute(
        self,
        request: CandidateEvaluationRequest,
    ) -> CandidateEvaluationResult:
        return await execute_iteration_candidate(request, self.policy, self.runtime)


async def execute_iteration_candidate(
    request: CandidateEvaluationRequest,
    policy: CandidateIterationExecutionPolicy,
    runtime: CandidateIterationExecutionRuntime,
) -> CandidateEvaluationResult:
    local_admission = execute_candidate_local_admission(
        request,
        CandidateLocalAdmissionPolicy(
            workspace_root=policy.workspace_root,
            max_candidate_chars=policy.max_candidate_chars,
            allow_generated_target_mutation=policy.allow_generated_target_mutation,
            allow_external_target_mutation=policy.allow_external_target_mutation,
            target_intent=policy.target_intent,
            inferred_new_skill_policy=policy.inferred_new_skill_policy,
            skip_duplicate_rejected_candidate_gate=(
                policy.skip_duplicate_rejected_candidate_gate
            ),
            gate_evaluator=runtime.gate_evaluator,
        ),
    )
    gate_results = list(local_admission.gate_results)
    if local_admission.terminal_result is not None:
        return local_admission.terminal_result

    measurement_experiment: ControlledExperimentSpec | None = None
    if policy.measurement_mode is not MeasurementPolicyMode.OFF:
        try:
            measurement_experiment = runtime.measurement_planner.plan(
                MeasurementPlanningRequest(
                    run_id=request.run_id,
                    target=request.target,
                    dataset=request.dataset,
                    candidate=request.candidate,
                    candidate_count=request.candidate_count,
                    environment_fingerprint=runtime.environment_fingerprints.get(
                        request.run_id
                    ),
                    target_intent=(
                        policy.target_intent.value
                        if policy.target_intent is not None
                        else None
                    ),
                ),
                MeasurementPlanningRuntime(runtime.measurement_experiments),
            ).experiment
        except (OSError, TypeError, ValueError) as exc:
            if policy.measurement_mode is MeasurementPolicyMode.REQUIRED:
                gate_results.append(
                    GateResult(
                        gate_name="trusted_improvement_measurement",
                        passed=False,
                        reason="controlled experiment contract could not be frozen",
                        details={
                            "failure_class": "measurement",
                            "code": "measurement_contract_invalid",
                            "type": type(exc).__name__,
                            "reason": str(exc),
                        },
                    )
                )

    async def validate_capabilities(**kwargs: Any) -> list[GateResult]:
        if runtime.capability_override is not None:
            return await runtime.capability_override(**kwargs)
        result = await validate_candidate_capabilities(
            CapabilityValidationRequest(**kwargs),
            runtime.capability_policy,
            runtime.capability_runtime,
        )
        return result.as_list()

    replay_admission = await execute_candidate_replay_admission(
        request,
        CandidateReplayAdmissionPolicy(
            replay_enabled=policy.replay_enabled,
            replay_backend=policy.replay_backend,
            repetitions_explicit=policy.replay_repetitions_explicit,
            measurement_min_independent_cases=(
                policy.measurement_min_independent_cases
            ),
            baseline_repetitions=policy.baseline_replay_repetitions,
            candidate_repetitions=policy.candidate_replay_repetitions,
            judge_repetitions=policy.judge_repetitions,
            replay_candidate_limit=policy.replay_candidate_limit,
            per_attempt_replay_token_limit=policy.per_attempt_replay_token_limit,
            replay_tokens_per_unit=policy.replay_tokens_per_unit,
        ),
        CandidateReplayAdmissionRuntime(
            reusable_baseline_case_count=runtime.reusable_baseline_case_count,
            validate_capabilities=validate_capabilities,
            typed_gate_failure=runtime.typed_gate_failure,
            feedback_builder=runtime.feedback_builder,
        ),
        initial_gate_results=gate_results,
    )
    if replay_admission.terminal_result is not None:
        return replay_admission.terminal_result

    async def replay_candidate(**kwargs: Any) -> tuple[object, object, object]:
        if runtime.paired_replay_override is not None:
            return await runtime.paired_replay_override(**kwargs)
        result = await runtime.paired_replay_controller.execute(
            PairedReplayExecutionRequest(
                candidate=kwargs.pop("selected_candidate"),
                **kwargs,
            ),
            runtime.paired_replay_runtime,
        )
        return result.as_tuple()

    replay_execution = await execute_candidate_replay(
        CandidateReplayExecutionRequest(request, replay_admission),
        CandidateReplayExecutionRuntime(
            replay_candidate=replay_candidate,
            execution_telemetry=runtime.execution_telemetry,
            replay_confidence_gate=_replay_confidence_gate,
            replay_evaluator_admission_gate=runtime.replay_evaluator_admission_gate,
            typed_gate_failure=runtime.typed_gate_failure,
            feedback_builder=runtime.feedback_builder,
        ),
    )
    if replay_execution.terminal_result is not None:
        return replay_execution.terminal_result
    evaluation_admission = plan_candidate_evaluation_admission(
        CandidateEvaluationAdmissionRequest(request, replay_execution),
        CandidateEvaluationAdmissionPolicy(
            replay_enabled=policy.replay_enabled,
            evaluation_backend=policy.evaluation_backend,  # type: ignore[arg-type]
            judge_repetitions=policy.judge_repetitions,
            min_eval_cases=policy.min_eval_cases,
            regression_suite_case_counts=policy.regression_suite_case_counts,
            challenger_enabled=policy.challenger_enabled,
            challenger_max_cases=policy.challenger_max_cases,
        ),
        CandidateEvaluationAdmissionRuntime(
            typed_gate_failure=runtime.typed_gate_failure,
            feedback_builder=runtime.feedback_builder,
        ),
    )
    if evaluation_admission.terminal_result is not None:
        return evaluation_admission.terminal_result
    evaluation_execution = await execute_candidate_evaluation(
        CandidateEvaluationExecutionRequest(
            request,
            replay_execution,
            evaluation_admission,
        ),
        CandidateEvaluationExecutionPolicy(
            evaluation_backend=policy.evaluation_backend,  # type: ignore[arg-type]
            max_iterations=policy.max_iterations,
            min_score_delta=policy.min_score_delta,
            replay_stability_margin=policy.replay_stability_margin,
            min_eval_cases=policy.min_eval_cases,
            require_resource_evidence=policy.require_resource_evidence,
        ),
        CandidateEvaluationExecutionRuntime(
            task_batch_executor=runtime.task_batch_executor,
            max_concurrency=runtime.max_concurrency,
            execution_telemetry=runtime.execution_telemetry,
            progress_callback=runtime.progress_callback,
            evaluate_pair=runtime.evaluate_pair,
            evaluate_variant=runtime.evaluate_variant,
            evaluate_independent_regression=runtime.regression,
            gate_is_replay_infrastructure_failure=(
                runtime.gate_is_replay_infrastructure_failure
            ),
        ),
    )

    def materialize_measurement(**kwargs: Any) -> object:
        return runtime.measurement_controller.materialize_candidate(**kwargs)

    return finalize_candidate_evaluation(
        CandidateEvaluationFinalizationRequest(
            request,
            replay_execution,
            evaluation_execution,
            measurement_experiment,
        ),
        CandidateEvaluationFinalizationPolicy(
            measurement_mode=policy.measurement_mode,
            auto_apply_target_types=policy.auto_apply_target_types,
        ),
        CandidateEvaluationFinalizationRuntime(
            materialize_measurement=materialize_measurement,
            typed_gate_failure=runtime.typed_gate_failure,
            feedback_builder=runtime.feedback_builder,
        ),
    )


__all__ = [
    "CandidateIterationExecution",
    "CandidateIterationExecutionPolicy",
    "CandidateIterationExecutionRuntime",
    "execute_iteration_candidate",
]
