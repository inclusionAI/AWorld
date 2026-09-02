"""Candidate evaluation, replay, challenge, and regression phase factory."""

from __future__ import annotations

from aworld.self_evolve.controllers.run_phase_context import RunPhaseContext
from aworld.self_evolve.controllers.run_phase_adapters import (
    BaselineReuseProvenanceAdapter,
    LegacyChallengeExecutionOverride,
    MeasurementPlanCompilationAdapter,
    MeasurementResumeAdapter,
    PrepareReplayAdaptationAdapter,
)

from typing import Callable
from pathlib import Path
from typing import Mapping

from aworld.core.tool.replay_policy import EvidencePolicyProfileV2
from aworld.self_evolve.datasets import (
    SelfEvolveDataset,
)
from aworld.self_evolve.evaluation import (
    AWorldTrajectoryEvaluatorBackend,
    evaluate_baseline_and_candidate,
    evaluate_variant_task,
)
from aworld.self_evolve.measurement import (
    ControlledExperimentSpec,
)
from aworld.self_evolve.measurement_control import (
    MeasurementPlanV2,
)
from aworld.self_evolve.controllers.run_iteration_helpers import (
    _candidate_gate_results,
    _gate_is_replay_execution_infrastructure_failure,
    _iteration_validation_feedback,
)
from aworld.self_evolve.feedback_diagnostics import (
    _typed_gate_feedback_metrics as _typed_gate_feedback_metrics,
)
from aworld.self_evolve.replay_cache import _reusable_baseline_case_count
from aworld.self_evolve.controllers.run_execution import (
    CandidateEvaluationRequest,
    CandidateEvaluationResult,
)
from aworld.self_evolve.controllers.run_replay_adaptation import (
    BaselineReuseProvenanceRequest,
    BaselineReuseProvenanceRuntime,
    ReplayAdaptationRequest,
    baseline_reuse_provenance,
    prepare_replay_adaptation,
)
from aworld.self_evolve.controllers.run_capability_validation import (
    CapabilityValidationPolicy,
    CapabilityValidationRequest,
    validate_candidate_capabilities,
)
from aworld.self_evolve.controllers.run_candidate_execution import (
    CandidateIterationExecution,
    CandidateIterationExecutionPolicy,
    CandidateIterationExecutionRuntime,
)
from aworld.self_evolve.controllers.run_challenge_execution import (
    ChallengeExecution,
    ChallengeExecutionPolicy,
    ChallengeExecutionRequest,
    ChallengeExecutionRuntime,
)
from aworld.self_evolve.controllers.run_regression_execution import (
    RegressionExecution,
    RegressionExecutionPolicy,
    RegressionExecutionRequest,
    RegressionExecutionRuntime,
    RegressionReplayExecution,
    RegressionReplayRequest,
    RegressionReplayResult,
)
from aworld.self_evolve.controllers.run_resources import (
    RunBudgetContext as _RunBudgetContext,
)
from aworld.self_evolve.controllers.measurement import (
    measurement_component_identity as _measurement_component_identity,
)
from aworld.self_evolve.controllers.measurement_authority import (
    AuthoritativeMeasurementRequest,
    AuthoritativeMeasurementRuntime,
)
from aworld.self_evolve.controllers.measurement_execution import (
    PairedReplayExecutionRequest,
    PairedReplayExecutionRuntime,
)
from aworld.self_evolve.controllers.screening_execution import (
    _with_typed_gate_failure_event,
)
from aworld.self_evolve.challenger import (
    ChallengeReport,
)
from aworld.self_evolve.optimizers.base import (
    CandidateSourceDisposition,
)
from aworld.self_evolve.replay import (
    CandidateReplayBackend,
    CandidateReplayResult,
)
from aworld.self_evolve.regression import (
    RegressionEvidence,
)
from aworld.self_evolve.replay_adaptation import (
    IsolationDecision,
    ReplayAdaptationBundle,
    ReplayCapabilityRequirement,
)
from aworld.self_evolve.targets import (
    SelfEvolveTarget,
)
from aworld.self_evolve.types import (
    CandidateVariant,
    GateResult,
)


_DEFAULT_CANDIDATE_CONTENT_MAX_CHARS = 500_000


class CandidatePhaseFactory:
    def __init__(self, context: RunPhaseContext) -> None:
        self.context = context

    def _candidate_iteration_execution(self) -> CandidateIterationExecution:
        evaluation_backend = self.context.construction.runtime.evaluation_backend
        return CandidateIterationExecution(
            CandidateIterationExecutionPolicy(
                workspace_root=self.context.construction.runtime.store.workspace_root,
                max_candidate_chars=_DEFAULT_CANDIDATE_CONTENT_MAX_CHARS,
                allow_generated_target_mutation=self.context.construction.policy.allow_generated_target_mutation,
                allow_external_target_mutation=self.context.construction.policy.allow_external_target_mutation,
                target_intent=self.context.state.active_target_intent,
                inferred_new_skill_policy=self.context.construction.policy.inferred_new_skill_policy,
                skip_duplicate_rejected_candidate_gate=(
                    self.context.construction.policy.skip_duplicate_rejected_candidate_gate
                ),
                measurement_mode=self.context.construction.measurement.mode,
                replay_enabled=self.context.construction.replay.replay_enabled,
                replay_backend=self.context.construction.runtime.candidate_replay_backend,
                replay_repetitions_explicit=self.context.construction.replay.replay_repetitions_explicit,
                measurement_min_independent_cases=(
                    self.context.construction.measurement.minimum_independent_cases
                ),
                baseline_replay_repetitions=self.context.construction.replay.baseline_replay_repetitions,
                candidate_replay_repetitions=self.context.construction.replay.candidate_replay_repetitions,
                judge_repetitions=self.context.construction.policy.judge_repetitions,
                replay_candidate_limit=self.context.construction.replay.replay_candidate_limit,
                per_attempt_replay_token_limit=self.context.construction.budget.per_attempt_replay_token_limit,
                replay_tokens_per_unit=self.context.construction.budget.replay_tokens_per_unit,
                evaluation_backend=evaluation_backend,
                regression_suite_case_counts=tuple(
                    len(suite.dataset.cases) for suite in self.context.construction.runtime.regression_suites
                ),
                challenger_enabled=self.context.construction.policy.challenger_enabled,
                challenger_max_cases=self.context.construction.policy.challenger_max_cases,
                max_iterations=self.context.construction.policy.max_iterations,
                min_score_delta=self.context.construction.policy.min_score_delta,
                replay_stability_margin=self.context.construction.replay.replay_stability_margin,
                min_eval_cases=self.context.construction.policy.min_eval_cases,
                require_resource_evidence=(
                    isinstance(evaluation_backend, AWorldTrajectoryEvaluatorBackend)
                    or getattr(
                        evaluation_backend,
                        "resource_accounting_required",
                        False,
                    )
                    is True
                ),
                auto_apply_target_types=tuple(self.context.construction.policy.auto_apply_target_types),
            ),
            CandidateIterationExecutionRuntime(
                measurement_planner=self.context.construction.controllers.measurement_planning,
                measurement_experiments=self.context.construction.measurement.experiments,
                environment_fingerprints=self.context.construction.mutable.replay_adaptation.environment_fingerprints,
                capability_policy=CapabilityValidationPolicy(
                    replay_enabled=self.context.construction.replay.replay_enabled
                ),
                capability_runtime=self.context.require_operations().capability_validation_runtime(),
                paired_replay_controller=self.context.construction.controllers.paired_replay_execution,
                paired_replay_runtime=self.context.require_operations().paired_replay_runtime(),
                regression=(
                    self.context.overrides.get("_evaluate_independent_regression")
                    or self.context.require_operations().regression_execution()
                ),
                measurement_controller=self.context.construction.controllers.measurement,
                task_batch_executor=self.context.construction.runtime.task_batch_executor,
                max_concurrency=self.context.construction.runtime.concurrency_policy.effective_limit(
                    "evaluation", item_count=2
                ),
                execution_telemetry=self.context.state.execution_telemetry,
                progress_callback=self.context.construction.runtime.progress_callback,
                gate_evaluator=_candidate_gate_results,
                reusable_baseline_case_count=_reusable_baseline_case_count,
                typed_gate_failure=_with_typed_gate_failure_event,
                feedback_builder=_iteration_validation_feedback,
                replay_evaluator_admission_gate=(
                    self.context.services.replay_evaluator_admission_gate
                ),
                evaluate_pair=evaluate_baseline_and_candidate,
                evaluate_variant=evaluate_variant_task,
                gate_is_replay_infrastructure_failure=(
                    _gate_is_replay_execution_infrastructure_failure
                ),
                capability_override=self.context.overrides.get(
                    "_validate_candidate_capabilities"
                ),
                paired_replay_override=self.context.overrides.get(
                    "_replay_selected_candidate"
                ),
            ),
        )

    async def _execute_iteration_candidate(
        self,
        request: CandidateEvaluationRequest,
    ) -> CandidateEvaluationResult:
        return await self.context.require_operations().candidate_iteration_execution().execute(request)

    async def _validate_candidate_capabilities(
        self,
        *,
        run_id: str,
        target: SelfEvolveTarget,
        dataset: SelfEvolveDataset,
        candidate: CandidateVariant,
        requirements: tuple[ReplayCapabilityRequirement, ...],
    ) -> list[GateResult]:
        result = await validate_candidate_capabilities(
            CapabilityValidationRequest(
                run_id=run_id,
                target=target,
                dataset=dataset,
                candidate=candidate,
                requirements=requirements,
            ),
            CapabilityValidationPolicy(replay_enabled=self.context.construction.replay.replay_enabled),
            self.context.require_operations().capability_validation_runtime(),
        )
        return result.as_list()

    def _prepare_replay_adaptation(
        self,
        *,
        run_id: str,
        dataset: SelfEvolveDataset,
        capability_skill_root: str | Path | None = None,
        candidate_package_fingerprint: str | None = None,
        emit_progress: bool = True,
    ) -> tuple[ReplayAdaptationBundle | None, GateResult]:
        execution = self.context.require_operations().replay_adaptation_execution(
            preserve_instance_override=False
        )
        return prepare_replay_adaptation(
            ReplayAdaptationRequest(
                run_id=run_id,
                dataset=dataset,
                capability_skill_root=capability_skill_root,
                candidate_package_fingerprint=candidate_package_fingerprint,
                emit_progress=emit_progress,
            ),
            execution.runtime,
            execution.state,
        ).as_tuple()

    def _baseline_reuse_provenance(
        self,
        *,
        run_id: str,
        target: SelfEvolveTarget,
        dataset: SelfEvolveDataset,
        replay_adaptation: ReplayAdaptationBundle | None = None,
        timeout_seconds: float | None = None,
        max_steps: int | None = None,
        max_tool_calls: int | None = None,
    ) -> dict[str, str | None]:
        return baseline_reuse_provenance(
            BaselineReuseProvenanceRequest(
                run_id=run_id,
                target=target,
                dataset=dataset,
                replay_adaptation=replay_adaptation,
                timeout_seconds=timeout_seconds,
                max_steps=max_steps,
                max_tool_calls=max_tool_calls,
            ),
            BaselineReuseProvenanceRuntime(
                replay_adaptation=self.context.require_operations().replay_adaptation_execution()
            ),
        ).provenance

    def _compile_authoritative_measurement_plan(
        self,
        *,
        run_id: str,
        dataset: SelfEvolveDataset,
        candidate: CandidateVariant,
        replay_adaptation: ReplayAdaptationBundle,
        replay_backend: CandidateReplayBackend,
        member_timeout_seconds: float,
        artifact_namespace: str | None = None,
        target_adapter: SelfEvolveTarget | None = None,
        experiment: ControlledExperimentSpec | None = None,
        measurement_stage: str = "authoritative",
    ) -> (
        tuple[
            MeasurementPlanV2,
            IsolationDecision,
            EvidencePolicyProfileV2,
        ]
        | None
    ):
        result = self.context.construction.controllers.authoritative_measurement.compile(
            AuthoritativeMeasurementRequest(
                run_id=run_id,
                dataset=dataset,
                candidate=candidate,
                replay_adaptation=replay_adaptation,
                replay_backend_identity=(
                    _measurement_component_identity(replay_backend)
                ),
                member_timeout_seconds=member_timeout_seconds,
                artifact_namespace=artifact_namespace,
                target_adapter_identity=(
                    _measurement_component_identity(target_adapter)
                    if target_adapter is not None
                    else None
                ),
                experiment=experiment,
                measurement_stage=measurement_stage,
            ),
            AuthoritativeMeasurementRuntime(
                experiments=self.context.construction.measurement.experiments,
                load_resume_request=self.context.require_operations().load_measurement_resume_request,
                progress_callback=self.context.construction.runtime.progress_callback,
            ),
        )
        return result.execution_bundle

    def _paired_replay_runtime(self) -> PairedReplayExecutionRuntime:
        adaptation = self.context.require_operations().replay_adaptation_execution()
        resume = MeasurementResumeAdapter(self.context.construction.controllers.measurement_planning)
        return PairedReplayExecutionRuntime(
            progress_callback=self.context.construction.runtime.progress_callback,
            execution_telemetry=self.context.state.execution_telemetry,
            screening_case_observations=self.context.construction.mutable.candidate_screening_case_observations,
            screening_control_observations=(
                self.context.construction.mutable.candidate_screening_control_observations
            ),
            measurement_experiments=self.context.construction.measurement.experiments,
            prepare_replay_adaptation=PrepareReplayAdaptationAdapter(adaptation),
            baseline_reuse_provenance=BaselineReuseProvenanceAdapter(adaptation),
            compile_measurement_plan=MeasurementPlanCompilationAdapter(
                self.context.construction.controllers.authoritative_measurement,
                self.context.construction.measurement.experiments,
                resume,
                self.context.construction.runtime.progress_callback,
            ),
            load_measurement_resume_request=resume,
        )

    async def _replay_selected_candidate(
        self,
        *,
        run_id: str,
        target: SelfEvolveTarget,
        dataset: SelfEvolveDataset,
        selected_candidate: CandidateVariant,
        apply_policy: str,
        baseline_replay_dir: str | None = None,
        baseline_repetitions: int | None = None,
        candidate_repetitions: int | None = None,
        progress_stage: str = "candidate_replay",
        timeout_seconds: int | None = None,
        max_steps: int | None = None,
        max_tool_calls: int | None = None,
        lifecycle_callback: Callable[[str, Mapping[str, object]], None] | None = None,
        source_disposition: CandidateSourceDisposition = CandidateSourceDisposition(),
        artifact_namespace: str | None = None,
        replay_backend: CandidateReplayBackend | None = None,
        measurement_experiment: ControlledExperimentSpec | None = None,
        measurement_stage: str = "authoritative",
    ) -> tuple[
        CandidateReplayResult | None, SelfEvolveDataset | None, GateResult | None
    ]:
        result = await self.context.construction.controllers.paired_replay_execution.execute(
            PairedReplayExecutionRequest(
                run_id=run_id,
                target=target,
                dataset=dataset,
                candidate=selected_candidate,
                apply_policy=apply_policy,
                baseline_replay_dir=baseline_replay_dir,
                baseline_repetitions=baseline_repetitions,
                candidate_repetitions=candidate_repetitions,
                progress_stage=progress_stage,
                timeout_seconds=timeout_seconds,
                max_steps=max_steps,
                max_tool_calls=max_tool_calls,
                lifecycle_callback=lifecycle_callback,
                source_disposition=source_disposition,
                artifact_namespace=artifact_namespace,
                replay_backend=replay_backend,
                measurement_experiment=measurement_experiment,
                measurement_stage=measurement_stage,
            ),
            self.context.require_operations().paired_replay_runtime(),
        )
        return result.as_tuple()

    def _challenge_execution(self) -> ChallengeExecution:
        callback = self.context.overrides.get("_prepare_challenge_suites")
        return ChallengeExecution(
            ChallengeExecutionPolicy(
                enabled=self.context.construction.policy.challenger_enabled,
                max_cases=self.context.construction.policy.challenger_max_cases,
                regression_suites=tuple(self.context.construction.runtime.regression_suites),
            ),
            ChallengeExecutionRuntime(
                store=self.context.construction.runtime.store,
                backend=self.context.construction.runtime.challenger_backend,
                progress_callback=self.context.construction.runtime.progress_callback,
            ),
            override=(
                LegacyChallengeExecutionOverride(callback)
                if callback is not None
                else None
            ),
        )

    def _regression_execution(self) -> RegressionExecution:
        controller = self.context.construction.controllers.paired_replay_execution
        paired_runtime = self.context.require_operations().paired_replay_runtime()
        replay_backend = self.context.construction.runtime.regression_replay_backend

        async def execute_replay(
            request: RegressionReplayRequest,
        ) -> RegressionReplayResult:
            result = await controller.execute(
                PairedReplayExecutionRequest(
                    run_id=request.run_id,
                    target=request.target,
                    dataset=request.dataset,
                    candidate=request.candidate,
                    apply_policy=request.apply_policy,
                    progress_stage="regression_replay",
                    artifact_namespace=f"regression/{request.suite_id}",
                    lifecycle_callback=request.lifecycle_callback,
                    replay_backend=replay_backend,
                ),
                paired_runtime,
            )
            return RegressionReplayResult(result.replay_dataset, result.gate)

        return RegressionExecution(
            RegressionExecutionPolicy(
                replay_enabled=self.context.construction.replay.replay_enabled,
                baseline_replay_repetitions=self.context.construction.replay.baseline_replay_repetitions,
                candidate_replay_repetitions=self.context.construction.replay.candidate_replay_repetitions,
                regression_suites=tuple(self.context.construction.runtime.regression_suites),
            ),
            RegressionExecutionRuntime(
                store=self.context.construction.runtime.store,
                challenge=self.context.require_operations().challenge_execution(),
                regression_backend=self.context.construction.runtime.regression_backend,
                regression_replay_backend=self.context.construction.runtime.regression_replay_backend,
                selection_backend=self.context.construction.runtime.evaluation_backend,
                replay=RegressionReplayExecution(execute_replay),
                task_batch_executor=self.context.construction.runtime.task_batch_executor,
                max_concurrency=self.context.construction.runtime.concurrency_policy.effective_limit(
                    "evaluation", item_count=2
                ),
                execution_telemetry=self.context.state.execution_telemetry,
                progress_callback=self.context.construction.runtime.progress_callback,
            ),
        )

    async def _evaluate_independent_regression(
        self,
        *,
        run_id: str,
        target: SelfEvolveTarget,
        selection_dataset: SelfEvolveDataset,
        candidate: CandidateVariant,
        apply_policy: str,
        budget_context: _RunBudgetContext | None,
    ) -> tuple[RegressionEvidence | None, ChallengeReport | None, GateResult]:
        result = await self.context.require_operations().regression_execution().execute(
            RegressionExecutionRequest(
                run_id=run_id,
                target=target,
                selection_dataset=selection_dataset,
                candidate=candidate,
                apply_policy=apply_policy,
                budget_context=budget_context,
            )
        )
        return result.as_tuple()

    async def _prepare_challenge_suites(
        self,
        *,
        run_id: str,
        target: SelfEvolveTarget,
        candidate: CandidateVariant,
        budget_context: _RunBudgetContext | None,
    ) -> tuple[ChallengeReport | None, GateResult]:
        result = await self.context.require_operations().challenge_execution().execute(
            ChallengeExecutionRequest(
                run_id=run_id,
                target=target,
                candidate=candidate,
                budget_context=budget_context,
            )
        )
        return result.as_tuple()
