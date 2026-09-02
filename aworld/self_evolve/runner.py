from __future__ import annotations

from decimal import Decimal
from typing import Callable, Any
from pathlib import Path
from typing import Iterable, Mapping

from aworld.config.conf import ModelConfig, SelfEvolveJudgeConfig
from aworld.runners.batch import DeterministicTaskBatchExecutor
from aworld.self_evolve.credit_assignment import (
    TargetSelectionDecision,
    TargetSelectionReport,
)
from aworld.self_evolve.datasets import (
    SelfEvolveDataset,
    SelfEvolveEvalSourceConfig,
    build_dataset_from_source,
)
from aworld.self_evolve.skill_evolution_contract import (
    SkillEvolutionContract,
)
from aworld.self_evolve.evaluation import (
    EvaluationBackend,
)
from aworld.self_evolve.controllers.retention import (
    ArtifactRetentionController,
    _artifact_retention_report as _owned_artifact_retention_report,
    _finalize_run_report as _owned_finalize_run_report,
    _retention_controller as _owned_retention_controller,
)
from aworld.self_evolve.lifecycle import cleanup_self_evolve_artifacts
from aworld.self_evolve.measurement import (
    MeasurementPolicyMode,
)
from aworld.self_evolve.ingestion import (
    IngestionRegistry,
)
from aworld.self_evolve.controllers.run_generation_helpers import (
    _VERIFICATION_CONTRACT_VERSION,
    _verification_contract_fingerprint as _generation_verification_contract_fingerprint,
)
from aworld.self_evolve.cli_ingestion import (
    _load_or_build_campaign_dataset as _cli_load_or_build_campaign_dataset,
    prepare_ingestion_from_cli_request,
    promote_ingestion_from_cli_request,
)
from aworld.self_evolve.cli_orchestration import (
    CliOrchestrationRuntime,
    execute_cli_optimization,
    _auto_group_trajectory_log_dataset,
    _default_cli_skill_candidate,
    _infer_target_from_trace_packs,
    _target_from_ref,
)
from aworld.self_evolve.apply_runtime_support import (
    default_post_apply_evaluator,
)
from aworld.self_evolve.target_selection_support import (
    explicit_target_selection_report,
)
from aworld.self_evolve.controllers.run_iteration_helpers import (
    _candidate_gate_results as _candidate_gate_results,
    _iteration_validation_feedback as _iteration_validation_feedback,
)
from aworld.self_evolve.run_history import (
    _load_prior_scheduler_state as _load_prior_scheduler_state,
)
from aworld.self_evolve.screening_observation_history import (
    _control_qualification_identity as _measurement_control_identity,
    _screening_control_preflight as _screening_control_preflight,
)
from aworld.self_evolve.feedback_diagnostics import (
    _typed_gate_feedback_metrics as _typed_gate_feedback_metrics,
)
from aworld.self_evolve.controllers.run_execution import (
    CandidateEvaluationRequest,
    CandidateEvaluationResult,
    ExplicitTargetRunRequest,
)
from aworld.self_evolve.controllers.run_configuration import (
    RunnerBudgetConfiguration,
    RunnerConstructionRequest,
    RunnerConstructionResult,
    RunnerMeasurementConfiguration,
    RunnerPolicyConfiguration,
    RunnerReplayConfiguration,
    RunnerRuntimeDependencies,
    build_runner_construction,
)
from aworld.self_evolve.controllers.run_replay_adaptation import (
    ReplayAdaptationExecution,
)
from aworld.self_evolve.controllers.run_repair_conformance import (
    RepairConformancePopulationRuntime,
    RepairConformancePreflightRuntime,
)
from aworld.self_evolve.controllers.run_capability_validation import (
    CapabilityValidationRuntime,
)
from aworld.self_evolve.controllers.run_candidate_execution import (
    CandidateIterationExecution,
)
from aworld.self_evolve.controllers.run_challenge_execution import (
    ChallengeExecution,
)
from aworld.self_evolve.controllers.run_regression_execution import (
    RegressionExecution,
)
from aworld.self_evolve.controllers.run_apply_transaction import (
    ApplyTransactionExecution,
)
from aworld.self_evolve.controllers.run_verified_only_apply import (
    VerifiedOnlyApplyExecution,
)
from aworld.self_evolve.controllers.run_resources import (
    CandidateAttemptTracker as _CandidateAttemptTracker,
    RunBudgetContext as _RunBudgetContext,
    RunFailureCleanup as _RunFailureCleanup,
)
from aworld.self_evolve.budget import CandidateAttemptKey
from aworld.self_evolve.controllers.measurement_execution import (
    PairedReplayExecutionRuntime,
)
from aworld.self_evolve.controllers.screening_execution import (
    _replay_evaluator_admission_gate as _replay_evaluator_admission_gate,
    _with_typed_gate_failure_event as _with_typed_gate_failure_event,
    execute_screen_candidate_population,
)
from aworld.self_evolve.overlay import (
    create_candidate_skill_overlay as create_candidate_skill_overlay,
)
from aworld.self_evolve.repair_conformance import (
    evaluate_candidate_source_conformance as evaluate_candidate_source_conformance,
    evaluate_compiled_probe_conformance as evaluate_compiled_probe_conformance,
)
from aworld.self_evolve.replay_capability import (
    frozen_replay_fixture_shape_fingerprints as frozen_replay_fixture_shape_fingerprints,
)
from aworld.self_evolve.challenger import (
    DEFAULT_CHALLENGE_CASES,
    ChallengerBackend,
)
from aworld.self_evolve.concurrency import (
    SelfEvolveConcurrencyPolicy,
)
from aworld.self_evolve.optimizers.base import (
    CandidateOptimizer,
    CandidateSourceDisposition,
)
from aworld.self_evolve.provenance import (
    InferredNewSkillPolicy,
    TargetProvenance,
)
from aworld.self_evolve.replay import (
    AWorldCliCandidateReplayBackend,
    CandidateReplayBackend,
    preflight_frozen_replay_capability as preflight_frozen_replay_capability,
    replay_capability_fixture_leaf_values as replay_capability_fixture_leaf_values,
    replay_capability_fixture_response_leaf_values as replay_capability_fixture_response_leaf_values,
)
from aworld.self_evolve.regression import (
    ResolvedRegressionSuite,
)
from aworld.self_evolve.replay_adaptation import (
    ReplayAdaptationCompiler,
    ReplayCapabilityRequirement,
)
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.run_defaults import (
    DEFAULT_CANDIDATE_GENERATION_TOKENS_PER_UNIT,
)
from aworld.self_evolve.targets import (
    SelfEvolveTarget,
)
from aworld.self_evolve.trace_pack import TracePack
from aworld.self_evolve.types import (
    CandidateVariant,
    EvaluationSummary,
    GateResult,
    SelfEvolveRun,
)


from aworld.self_evolve.controllers.run_lifecycle_execution import (
    RunLifecycleExecution,
    RunLifecycleResult,
)
from aworld.self_evolve.controllers.run_phase_context import (
    RunCompatibilityOverrides,
    RunLifecyclePublishedState,
    RunLifecycleServices,
)


_MAX_PROGRESS_REPAIR_EXTENSION_ITERATIONS = 6
_RUNNER_COMPAT_BASE_METHODS: dict[str, Callable[..., Any]] = {}
_default_post_apply_evaluator = default_post_apply_evaluator
_explicit_target_selection_report = explicit_target_selection_report
_RUNNER_COMPAT_METHOD_NAMES = (
    "_screen_candidate_population",
    "_execute_screen_candidate_population",
    "_replay_adaptation_execution",
    "_repair_conformance_preflight_runtime",
    "_repair_conformance_preflight_override",
    "_repair_conformance_population_runtime",
    "_capability_validation_runtime",
    "_validate_candidate_repair_conformance_population",
    "_preflight_candidate_repair_conformance",
    "_load_measurement_resume_request",
    "_plan_candidate_measurement",
    "_materialize_candidate_measurement",
    "_measurement_search_projection_execution",
    "_attach_measurement_search_performance",
    "_evaluate_iteration_candidate",
    "_candidate_iteration_execution",
    "_execute_iteration_candidate",
    "_validate_candidate_capabilities",
    "_prepare_replay_adaptation",
    "_baseline_reuse_provenance",
    "_compile_authoritative_measurement_plan",
    "_paired_replay_runtime",
    "_replay_selected_candidate",
    "_challenge_execution",
    "_regression_execution",
    "_evaluate_independent_regression",
    "_prepare_challenge_suites",
    "_auto_apply_execution",
    "_apply_auto_verified",
    "_verified_only_apply_execution",
    "_apply_verified_only",
)

__all__ = [
    "SelfEvolveRunner",
    "SelfEvolveRunnerResult",
    "optimize_explicit_target",
    "optimize_from_cli_request",
    "prepare_ingestion_from_cli_request",
    "promote_ingestion_from_cli_request",
]


def _runner_method_override(
    runner: object,
    method_name: str,
) -> Callable[..., Any] | None:
    base_method = _RUNNER_COMPAT_BASE_METHODS.get(method_name)
    if base_method is None:
        return None
    callback = getattr(runner, method_name)
    implementation = getattr(callback, "__func__", callback)
    return None if implementation is base_method else callback


def _verification_contract_fingerprint(**kwargs: object) -> str:
    """Compatibility seam for verification contract version upgrades."""

    return _generation_verification_contract_fingerprint(
        **kwargs,
        verification_contract_version=_VERIFICATION_CONTRACT_VERSION,
    )


SelfEvolveRunnerResult = RunLifecycleResult

class SelfEvolveRunner:
    def __init__(
        self,
        *,
        store: FilesystemSelfEvolveStore,
        optimizer: CandidateOptimizer,
        post_apply_evaluator: Callable[[CandidateVariant], Any] | None = None,
        evaluation_backend: EvaluationBackend | None = None,
        regression_backend: EvaluationBackend | None = None,
        regression_suites: tuple[ResolvedRegressionSuite, ...] = (),
        challenger_backend: ChallengerBackend | None = None,
        challenger_enabled: bool = True,
        challenger_max_cases: int = DEFAULT_CHALLENGE_CASES,
        min_score_delta: float = 0.0,
        pending_duplicate: bool = False,
        max_iterations: int = 1,
        min_eval_cases: int = 30,
        judge_repetitions: int = 3,
        max_run_tokens: int | None = None,
        total_run_token_budget: int | None = None,
        per_attempt_replay_token_limit: int | None = None,
        max_run_cost_usd: float | Decimal | None = None,
        max_run_wall_seconds: float | Decimal | None = None,
        candidate_generation_tokens_per_unit: int | None = (
            DEFAULT_CANDIDATE_GENERATION_TOKENS_PER_UNIT
        ),
        candidate_generation_output_tokens_per_unit: int = 16_000,
        candidate_generation_model_name: str = "gpt-4o",
        candidate_generation_cost_usd_per_unit: float | Decimal | None = Decimal(
            "0.05"
        ),
        candidate_generation_wall_seconds_per_unit: float | Decimal | None = Decimal(
            "120"
        ),
        candidate_screening_tokens_per_unit: int | None = 4_096,
        candidate_screening_cost_usd_per_unit: float | Decimal | None = Decimal("0.05"),
        candidate_screening_wall_seconds_per_unit: float | Decimal | None = (
            Decimal("210")
        ),
        replay_tokens_per_unit: int | None = 4_096,
        replay_cost_usd_per_unit: float | Decimal | None = Decimal("0.05"),
        replay_wall_seconds_per_unit: float | Decimal | None = Decimal("600"),
        evaluation_tokens_per_unit: int | None = 2_048,
        evaluation_cost_usd_per_unit: float | Decimal | None = Decimal("0.02"),
        evaluation_wall_seconds_per_unit: float | Decimal | None = Decimal("60"),
        deprecated_config_mappings: Iterable[str] | Mapping[str, str] | None = None,
        auto_apply_target_types: tuple[str, ...] = ("skill",),
        allow_generated_target_mutation: bool = False,
        allow_external_target_mutation: bool = False,
        inferred_new_skill_policy: InferredNewSkillPolicy
        | str = InferredNewSkillPolicy.AUTO_VERIFIED,
        replay_enabled: bool = False,
        candidate_replay_backend: CandidateReplayBackend | None = None,
        regression_replay_backend: CandidateReplayBackend | None = None,
        replay_timeout_seconds: int = 600,
        replay_total_timeout_seconds: int | None = None,
        replay_resume_dir: str | Path | None = None,
        measurement_resume_run_id: str | None = None,
        replay_max_steps: int | None = None,
        replay_candidate_limit: int = 2,
        candidate_screening_max_cases: int = 3,
        max_generated_candidates: int = 6,
        max_full_evaluation_candidates: int = 3,
        max_score_tiebreak_candidates: int = 1,
        baseline_replay_repetitions: int = 1,
        candidate_replay_repetitions: int = 1,
        replay_repetitions_explicit: bool = False,
        replay_stability_margin: float = 0.0,
        measurement_mode: MeasurementPolicyMode | str = MeasurementPolicyMode.OFF,
        measurement_primary_metric: str = "task_success",
        measurement_minimum_effect: float = 0.0,
        measurement_confidence_level: float = 0.95,
        measurement_min_independent_cases: int = 2,
        measurement_bootstrap_samples: int = 2_000,
        measurement_zero_yield_patience: int = 2,
        measurement_invalid_control_patience: int = 2,
        measurement_maximum_interval_width: float | None = None,
        replay_agent: str | None = None,
        runtime_registry_refresher: Callable[[CandidateVariant], Any] | None = None,
        runtime_skill_activator: Callable[[CandidateVariant], Any] | None = None,
        runtime_registry_compensator: Callable[[CandidateVariant, object | None], Any]
        | None = None,
        runtime_skill_compensator: Callable[[CandidateVariant, object | None], Any]
        | None = None,
        progress_callback: Callable[[str, str], Any] | None = None,
        skip_duplicate_rejected_candidate_gate: bool = False,
        replay_adaptation_compiler: ReplayAdaptationCompiler | None = None,
        concurrency_policy: SelfEvolveConcurrencyPolicy | None = None,
        task_batch_executor: DeterministicTaskBatchExecutor | None = None,
        ingestion_model_call_count: int = 0,
        skill_evolution_contract: SkillEvolutionContract | None = None,
    ) -> None:
        construction = build_runner_construction(
            RunnerConstructionRequest(
                runtime=RunnerRuntimeDependencies(
                    store=store,
                    optimizer=optimizer,
                    post_apply_evaluator=post_apply_evaluator,
                    evaluation_backend=evaluation_backend,
                    regression_backend=regression_backend,
                    regression_suites=regression_suites,
                    challenger_backend=challenger_backend,
                    candidate_replay_backend=candidate_replay_backend,
                    regression_replay_backend=regression_replay_backend,
                    runtime_registry_refresher=runtime_registry_refresher,
                    runtime_skill_activator=runtime_skill_activator,
                    runtime_registry_compensator=runtime_registry_compensator,
                    runtime_skill_compensator=runtime_skill_compensator,
                    progress_callback=progress_callback,
                    replay_adaptation_compiler=replay_adaptation_compiler,
                    concurrency_policy=concurrency_policy,
                    task_batch_executor=task_batch_executor,
                    skill_evolution_contract=skill_evolution_contract,
                    runner_type_name=type(self).__name__,
                ),
                budget=RunnerBudgetConfiguration(
                    max_run_tokens=max_run_tokens,
                    total_run_token_budget=total_run_token_budget,
                    per_attempt_replay_token_limit=per_attempt_replay_token_limit,
                    max_run_cost_usd=max_run_cost_usd,
                    max_run_wall_seconds=max_run_wall_seconds,
                    candidate_generation_tokens_per_unit=(
                        candidate_generation_tokens_per_unit
                    ),
                    candidate_generation_cost_usd_per_unit=(
                        candidate_generation_cost_usd_per_unit
                    ),
                    candidate_generation_wall_seconds_per_unit=(
                        candidate_generation_wall_seconds_per_unit
                    ),
                    candidate_screening_tokens_per_unit=(
                        candidate_screening_tokens_per_unit
                    ),
                    candidate_screening_cost_usd_per_unit=(
                        candidate_screening_cost_usd_per_unit
                    ),
                    candidate_screening_wall_seconds_per_unit=(
                        candidate_screening_wall_seconds_per_unit
                    ),
                    replay_tokens_per_unit=replay_tokens_per_unit,
                    replay_cost_usd_per_unit=replay_cost_usd_per_unit,
                    replay_wall_seconds_per_unit=replay_wall_seconds_per_unit,
                    evaluation_tokens_per_unit=evaluation_tokens_per_unit,
                    evaluation_cost_usd_per_unit=evaluation_cost_usd_per_unit,
                    evaluation_wall_seconds_per_unit=(evaluation_wall_seconds_per_unit),
                    deprecated_config_mappings=deprecated_config_mappings,
                ),
                replay=RunnerReplayConfiguration(
                    replay_enabled=replay_enabled,
                    replay_timeout_seconds=replay_timeout_seconds,
                    replay_total_timeout_seconds=replay_total_timeout_seconds,
                    replay_resume_dir=replay_resume_dir,
                    replay_max_steps=replay_max_steps,
                    replay_candidate_limit=replay_candidate_limit,
                    candidate_screening_max_cases=candidate_screening_max_cases,
                    max_generated_candidates=max_generated_candidates,
                    max_full_evaluation_candidates=max_full_evaluation_candidates,
                    max_score_tiebreak_candidates=max_score_tiebreak_candidates,
                    baseline_replay_repetitions=baseline_replay_repetitions,
                    candidate_replay_repetitions=candidate_replay_repetitions,
                    replay_repetitions_explicit=replay_repetitions_explicit,
                    replay_stability_margin=replay_stability_margin,
                    replay_agent=replay_agent,
                ),
                measurement=RunnerMeasurementConfiguration(
                    mode=measurement_mode,
                    primary_metric=measurement_primary_metric,
                    minimum_effect=measurement_minimum_effect,
                    confidence_level=measurement_confidence_level,
                    minimum_independent_cases=measurement_min_independent_cases,
                    bootstrap_samples=measurement_bootstrap_samples,
                    zero_yield_patience=measurement_zero_yield_patience,
                    invalid_control_patience=measurement_invalid_control_patience,
                    maximum_interval_width=measurement_maximum_interval_width,
                    resume_run_id=measurement_resume_run_id,
                ),
                policy=RunnerPolicyConfiguration(
                    challenger_enabled=challenger_enabled,
                    challenger_max_cases=challenger_max_cases,
                    min_score_delta=min_score_delta,
                    pending_duplicate=pending_duplicate,
                    max_iterations=max_iterations,
                    min_eval_cases=min_eval_cases,
                    judge_repetitions=judge_repetitions,
                    candidate_generation_output_tokens_per_unit=(
                        candidate_generation_output_tokens_per_unit
                    ),
                    candidate_generation_model_name=(candidate_generation_model_name),
                    auto_apply_target_types=auto_apply_target_types,
                    allow_generated_target_mutation=(allow_generated_target_mutation),
                    allow_external_target_mutation=(allow_external_target_mutation),
                    inferred_new_skill_policy=inferred_new_skill_policy,
                    skip_duplicate_rejected_candidate_gate=(
                        skip_duplicate_rejected_candidate_gate
                    ),
                    ingestion_model_call_count=ingestion_model_call_count,
                ),
            )
        )
        self._install_construction(construction)

    def _install_construction(
        self,
        construction: RunnerConstructionResult,
    ) -> None:
        self._construction = construction
        self._lifecycle_published_state = RunLifecyclePublishedState()
        runtime = construction.runtime
        budget = construction.budget
        policy = construction.policy
        replay = construction.replay
        measurement = construction.measurement
        controllers = construction.controllers
        mutable = construction.mutable

        self.store = runtime.store
        self.optimizer = runtime.optimizer
        self.post_apply_evaluator = runtime.post_apply_evaluator
        self.evaluation_backend = runtime.evaluation_backend
        self.regression_backend = runtime.regression_backend
        self.regression_suites = runtime.regression_suites
        self.challenger_backend = runtime.challenger_backend
        self.candidate_replay_backend = runtime.candidate_replay_backend
        self.regression_replay_backend = runtime.regression_replay_backend
        self.skill_evolution_contract = runtime.skill_evolution_contract
        self.runtime_registry_refresher = runtime.runtime_registry_refresher
        self.runtime_skill_activator = runtime.runtime_skill_activator
        self.runtime_registry_compensator = runtime.runtime_registry_compensator
        self.runtime_skill_compensator = runtime.runtime_skill_compensator
        self.progress_callback = runtime.progress_callback
        self.replay_adaptation_compiler = runtime.replay_adaptation_compiler
        self.concurrency_policy = runtime.concurrency_policy
        self.task_batch_executor = runtime.task_batch_executor

        self.max_run_tokens = budget.max_run_tokens
        self.total_run_token_budget = budget.total_run_token_budget
        self.per_attempt_replay_token_limit = budget.per_attempt_replay_token_limit
        self.max_run_cost_usd = budget.max_run_cost_usd
        self.max_run_wall_seconds = budget.max_run_wall_seconds
        self.deprecated_config_mappings = budget.deprecated_config_mappings
        self.candidate_generation_tokens_per_unit = (
            budget.candidate_generation_tokens_per_unit
        )
        self.candidate_screening_tokens_per_unit = (
            budget.candidate_screening_tokens_per_unit
        )
        self.replay_tokens_per_unit = budget.replay_tokens_per_unit
        self.evaluation_tokens_per_unit = budget.evaluation_tokens_per_unit
        self._budget_cold_start_by_stage = budget.cold_start_by_stage

        self.challenger_enabled = policy.challenger_enabled
        self.challenger_max_cases = policy.challenger_max_cases
        self.min_score_delta = policy.min_score_delta
        self.pending_duplicate = policy.pending_duplicate
        self.max_iterations = policy.max_iterations
        self.min_eval_cases = policy.min_eval_cases
        self.judge_repetitions = policy.judge_repetitions
        self.ingestion_model_call_count = policy.ingestion_model_call_count
        self.candidate_generation_output_tokens_per_unit = (
            policy.candidate_generation_output_tokens_per_unit
        )
        self.candidate_generation_model_name = policy.candidate_generation_model_name
        self.auto_apply_target_types = policy.auto_apply_target_types
        self.allow_generated_target_mutation = policy.allow_generated_target_mutation
        self.allow_external_target_mutation = policy.allow_external_target_mutation
        self.inferred_new_skill_policy = policy.inferred_new_skill_policy
        self.skip_duplicate_rejected_candidate_gate = (
            policy.skip_duplicate_rejected_candidate_gate
        )

        self.replay_enabled = replay.replay_enabled
        self.replay_timeout_seconds = replay.replay_timeout_seconds
        self.replay_total_timeout_seconds = replay.replay_total_timeout_seconds
        self.replay_resume_dir = replay.replay_resume_dir
        self.replay_max_steps = replay.replay_max_steps
        self.replay_candidate_limit = replay.replay_candidate_limit
        self.candidate_screening_max_cases = replay.candidate_screening_max_cases
        self.max_generated_candidates = replay.max_generated_candidates
        self.max_full_evaluation_candidates = replay.max_full_evaluation_candidates
        self.max_score_tiebreak_candidates = replay.max_score_tiebreak_candidates
        self.baseline_replay_repetitions = replay.baseline_replay_repetitions
        self.candidate_replay_repetitions = replay.candidate_replay_repetitions
        self.replay_repetitions_explicit = replay.replay_repetitions_explicit
        self.replay_stability_margin = replay.replay_stability_margin
        self.replay_agent = replay.replay_agent

        self.measurement_mode = measurement.mode
        self.measurement_primary_metric = measurement.primary_metric
        self.measurement_minimum_effect = measurement.minimum_effect
        self.measurement_confidence_level = measurement.confidence_level
        self.measurement_min_independent_cases = measurement.minimum_independent_cases
        self.measurement_bootstrap_samples = measurement.bootstrap_samples
        self.measurement_early_stop_policy = measurement.early_stop_policy
        self.measurement_resume_run_id = measurement.resume_run_id
        self._measurement_experiments = measurement.experiments
        self._screening_measurement_experiments = measurement.screening_experiments
        self._measurement_summaries = measurement.summaries

        self._generation_controller = controllers.generation
        self._screening_controller = controllers.screening
        self._measurement_controller = controllers.measurement
        self._measurement_planning_controller = controllers.measurement_planning
        self._authoritative_measurement_controller = (
            controllers.authoritative_measurement
        )
        self._paired_replay_execution_controller = controllers.paired_replay_execution

        self._active_target_intent = mutable.active_target_intent
        self.execution_telemetry = mutable.execution_telemetry
        self._replay_adaptation_state = mutable.replay_adaptation
        # Compatibility aliases retained for integrations that inspect cache state.
        self._replay_adaptation_cache = self._replay_adaptation_state.adaptation_cache
        self._replay_dataset_preflight_cache = (
            self._replay_adaptation_state.dataset_preflight_cache
        )
        self._run_environment_fingerprints = (
            self._replay_adaptation_state.environment_fingerprints
        )
        self._candidate_screening_case_observations = (
            mutable.candidate_screening_case_observations
        )
        self._candidate_screening_control_observations = (
            mutable.candidate_screening_control_observations
        )
        self._candidate_screening_observation_dataset_fingerprint = (
            mutable.candidate_screening_observation_dataset_fingerprint
        )
        self._candidate_screening_loaded_run_ids = (
            mutable.candidate_screening_loaded_run_ids
        )
        self._candidate_screening_run_invalid_control_case_ids = (
            mutable.candidate_screening_run_invalid_control_case_ids
        )
        self._current_run_authoritative_case_observations = (
            mutable.current_run_authoritative_case_observations
        )

    def _lifecycle_execution(self) -> RunLifecycleExecution:
        callbacks = {
            name: callback
            for name in _RUNNER_COMPAT_METHOD_NAMES
            if (callback := _runner_method_override(self, name)) is not None
        }
        return RunLifecycleExecution(
            self._construction,
            published_state=self._lifecycle_published_state,
            services=RunLifecycleServices(
                artifact_retention_report=_artifact_retention_report,
                finalize_run_report=_finalize_run_report,
                load_prior_scheduler_state=_load_prior_scheduler_state,
                screening_control_preflight=_screening_control_preflight,
                control_qualification_identity=_measurement_control_identity,
                preflight_frozen_replay_capability=(
                    preflight_frozen_replay_capability
                ),
                evaluate_candidate_source_conformance=(
                    evaluate_candidate_source_conformance
                ),
                create_candidate_skill_overlay=create_candidate_skill_overlay,
                evaluate_compiled_probe_conformance=(
                    evaluate_compiled_probe_conformance
                ),
                frozen_replay_fixture_shape_fingerprints=(
                    frozen_replay_fixture_shape_fingerprints
                ),
                replay_capability_fixture_leaf_values=(
                    replay_capability_fixture_leaf_values
                ),
                replay_capability_fixture_response_leaf_values=(
                    replay_capability_fixture_response_leaf_values
                ),
                verification_contract_fingerprint=(
                    _verification_contract_fingerprint
                ),
                replay_evaluator_admission_gate=(
                    _replay_evaluator_admission_gate
                ),
            ),
            compatibility_overrides=RunCompatibilityOverrides(**callbacks),
            execution_telemetry=self.execution_telemetry,
            active_target_intent=self._active_target_intent,
            screening_observation_dataset_fingerprint=(
                self._candidate_screening_observation_dataset_fingerprint
            ),
        )

    def _sync_lifecycle_state(self, execution: RunLifecycleExecution) -> None:
        state = execution.context.state
        self.execution_telemetry = state.execution_telemetry
        self._active_target_intent = state.active_target_intent
        self._candidate_screening_observation_dataset_fingerprint = (
            state.screening_observation_dataset_fingerprint
        )
        if state.run_budget_ledger is not None:
            self.run_budget_ledger = state.run_budget_ledger

    @property
    def run_budget_ledger(self) -> Any:
        ledger = self._lifecycle_published_state.run_budget_ledger
        if ledger is None:
            raise AttributeError("run_budget_ledger")
        return ledger

    @run_budget_ledger.setter
    def run_budget_ledger(self, value: Any) -> None:
        self._lifecycle_published_state.run_budget_ledger = value

    @property
    def execution_telemetry(self) -> Any:
        telemetry = self._lifecycle_published_state.execution_telemetry
        if telemetry is None:
            raise AttributeError("execution_telemetry")
        return telemetry

    @execution_telemetry.setter
    def execution_telemetry(self, value: Any) -> None:
        self._lifecycle_published_state.execution_telemetry = value

    async def run_explicit_target(
        self,
        *,
        run_id: str,
        target: SelfEvolveTarget,
        dataset: SelfEvolveDataset,
        trace_packs: tuple[TracePack, ...],
        apply_policy: str = "proposal",
        target_selection_report: TargetSelectionReport | None = None,
        target_provenance: TargetProvenance | None = None,
        target_selection_decision: TargetSelectionDecision | None = None,
        campaign_prior_run_ids: tuple[str, ...] | None = None,
        campaign_scheduler_checkpoint_run_ids: tuple[str, ...] | None = None,
        campaign_id: str | None = None,
        campaign_cycle: int | None = None,
    ) -> SelfEvolveRunnerResult:
        failure_cleanup = _RunFailureCleanup()
        request = ExplicitTargetRunRequest(
            run_id=run_id,
            target=target,
            dataset=dataset,
            trace_packs=trace_packs,
            apply_policy=apply_policy,
            target_selection_report=target_selection_report,
            target_provenance=target_provenance,
            target_selection_decision=target_selection_decision,
            campaign_prior_run_ids=campaign_prior_run_ids,
            campaign_scheduler_checkpoint_run_ids=(
                campaign_scheduler_checkpoint_run_ids
            ),
            campaign_id=campaign_id,
            campaign_cycle=campaign_cycle,
        )
        try:
            return await self._run_explicit_target(
                request=request,
                failure_cleanup=failure_cleanup,
            )
        except BaseException:
            failure_cleanup.cleanup()
            raise
        finally:
            self._replay_adaptation_state.cleanup_run(run_id)
            self._candidate_screening_run_invalid_control_case_ids.pop(run_id, None)

    async def _run_explicit_target(
        self,
        *,
        request: ExplicitTargetRunRequest,
        failure_cleanup: _RunFailureCleanup,
    ) -> SelfEvolveRunnerResult:
        execution = self._lifecycle_execution()
        try:
            return await execution.execute(
                request=request,
                failure_cleanup=failure_cleanup,
            )
        finally:
            self._sync_lifecycle_state(execution)

    async def _screen_candidate_population(self, **kwargs: Any) -> Any:
        return await self._lifecycle_execution().phases.operations.screen_candidate_population(**kwargs)

    _execute_screen_candidate_population = staticmethod(
        execute_screen_candidate_population
    )

    def _replay_adaptation_execution(self, **kwargs: Any) -> ReplayAdaptationExecution:
        return self._lifecycle_execution().phases.screening._replay_adaptation_execution(**kwargs)

    def _repair_conformance_preflight_runtime(
        self,
    ) -> RepairConformancePreflightRuntime:
        return self._lifecycle_execution().phases.screening._repair_conformance_preflight_runtime()

    def _repair_conformance_preflight_override(self) -> Any:
        return self._lifecycle_execution().phases.screening._repair_conformance_preflight_override()

    def _repair_conformance_population_runtime(
        self,
    ) -> RepairConformancePopulationRuntime:
        return self._lifecycle_execution().phases.screening._repair_conformance_population_runtime()

    def _capability_validation_runtime(self) -> CapabilityValidationRuntime:
        return self._lifecycle_execution().phases.screening._capability_validation_runtime()

    async def _validate_candidate_repair_conformance_population(
        self,
        **kwargs: Any,
    ) -> Any:
        return await self._lifecycle_execution().phases.operations.validate_candidate_repair_conformance_population(**kwargs)

    async def _preflight_candidate_repair_conformance(
        self,
        **kwargs: Any,
    ) -> Any:
        return (
            await self._lifecycle_execution().phases.operations.preflight_candidate_repair_conformance(**kwargs)
        )

    def _load_measurement_resume_request(self, **kwargs: Any) -> Any:
        return self._lifecycle_execution().phases.operations.load_measurement_resume_request(**kwargs)

    def _plan_candidate_measurement(self, **kwargs: Any) -> Any:
        return self._lifecycle_execution().phases.operations.plan_candidate_measurement(**kwargs)

    def _materialize_candidate_measurement(self, **kwargs: Any) -> Any:
        return self._lifecycle_execution().phases.operations.materialize_candidate_measurement(**kwargs)

    def _measurement_search_projection_execution(self) -> Any:
        return self._lifecycle_execution().phases.operations.measurement_search_projection_execution()

    def _attach_measurement_search_performance(self, **kwargs: Any) -> Any:
        return self._lifecycle_execution().phases.operations.attach_measurement_search_performance(**kwargs)

    async def _evaluate_iteration_candidate(
        self,
        *,
        run_id: str,
        target: SelfEvolveTarget,
        dataset: SelfEvolveDataset,
        candidate: CandidateVariant,
        apply_policy: str,
        target_provenance: TargetProvenance | None,
        target_provenance_unresolved_reason: str | None = None,
        target_selection_report: TargetSelectionReport | None = None,
        iteration_number: int,
        candidate_number: int,
        candidate_count: int,
        rejected_candidate_ids: set[str],
        accepted_candidate_ids: set[str],
        baseline_replay_dir: str | None = None,
        capability_requirements: tuple[ReplayCapabilityRequirement, ...] = (),
        attempt_key: CandidateAttemptKey | None = None,
        attempt_tracker: _CandidateAttemptTracker | None = None,
        budget_context: _RunBudgetContext | None = None,
        precomputed_gate_results: tuple[GateResult, ...] = (),
        source_disposition: CandidateSourceDisposition = CandidateSourceDisposition(),
        baseline_evaluation_cache: dict[str, EvaluationSummary] | None = None,
        allow_score_tiebreak: bool = True,
    ) -> tuple[dict[str, object], dict[str, object], tuple[EvaluationSummary, ...]]:
        """Compatibility adapter for the historical keyword-only boundary."""

        result = await self._execute_iteration_candidate(
            CandidateEvaluationRequest(
                run_id=run_id,
                target=target,
                dataset=dataset,
                candidate=candidate,
                apply_policy=apply_policy,
                target_provenance=target_provenance,
                target_provenance_unresolved_reason=(
                    target_provenance_unresolved_reason
                ),
                target_selection_report=target_selection_report,
                iteration_number=iteration_number,
                candidate_number=candidate_number,
                candidate_count=candidate_count,
                rejected_candidate_ids=rejected_candidate_ids,
                accepted_candidate_ids=accepted_candidate_ids,
                baseline_replay_dir=baseline_replay_dir,
                capability_requirements=capability_requirements,
                attempt_key=attempt_key,
                attempt_tracker=attempt_tracker,
                budget_context=budget_context,
                precomputed_gate_results=precomputed_gate_results,
                source_disposition=source_disposition,
                baseline_evaluation_cache=baseline_evaluation_cache,
                allow_score_tiebreak=allow_score_tiebreak,
            )
        )
        return result.as_tuple()

    def _candidate_iteration_execution(self) -> CandidateIterationExecution:
        return self._lifecycle_execution().phases.operations.candidate_iteration_execution()

    async def _execute_iteration_candidate(
        self, request: CandidateEvaluationRequest
    ) -> CandidateEvaluationResult:
        return await self._candidate_iteration_execution().execute(request)

    async def _validate_candidate_capabilities(
        self,
        **kwargs: Any,
    ) -> Any:
        return await self._lifecycle_execution().phases.operations.validate_candidate_capabilities(**kwargs)

    def _prepare_replay_adaptation(
        self,
        **kwargs: Any,
    ) -> Any:
        return self._lifecycle_execution().phases.operations.prepare_replay_adaptation(**kwargs)

    def _baseline_reuse_provenance(
        self,
        **kwargs: Any,
    ) -> Any:
        return self._lifecycle_execution().phases.operations.baseline_reuse_provenance(**kwargs)

    def _compile_authoritative_measurement_plan(self, **kwargs: Any) -> Any:
        return self._lifecycle_execution().phases.operations.compile_authoritative_measurement_plan(**kwargs)

    def _paired_replay_runtime(self) -> PairedReplayExecutionRuntime:
        return self._lifecycle_execution().phases.operations.paired_replay_runtime()

    async def _replay_selected_candidate(self, **kwargs: Any) -> Any:
        return await self._lifecycle_execution().phases.operations.replay_selected_candidate(**kwargs)

    def _challenge_execution(self) -> ChallengeExecution:
        return self._lifecycle_execution().phases.operations.challenge_execution()

    def _regression_execution(self) -> RegressionExecution:
        return self._lifecycle_execution().phases.operations.regression_execution()

    async def _evaluate_independent_regression(self, **kwargs: Any) -> Any:
        return await self._lifecycle_execution().phases.operations.evaluate_independent_regression(**kwargs)

    async def _prepare_challenge_suites(self, **kwargs: Any) -> Any:
        return await self._lifecycle_execution().phases.operations.prepare_challenge_suites(**kwargs)

    def _auto_apply_execution(self, **kwargs: Any) -> ApplyTransactionExecution:
        return self._lifecycle_execution().phases.operations.auto_apply_execution(**kwargs)

    async def _apply_auto_verified(self, *args: Any, **kwargs: Any) -> Any:
        return await self._lifecycle_execution().phases.operations.apply_auto_verified(*args, **kwargs)

    def _verified_only_apply_execution(self) -> VerifiedOnlyApplyExecution:
        return self._lifecycle_execution().phases.operations.verified_only_apply_execution()

    async def _apply_verified_only(self, *args: Any, **kwargs: Any) -> Any:
        return await self._lifecycle_execution().phases.operations.apply_verified_only(*args, **kwargs)


_RUNNER_COMPAT_BASE_METHODS.update(
    {name: getattr(SelfEvolveRunner, name) for name in _RUNNER_COMPAT_METHOD_NAMES}
)


async def optimize_explicit_target(
    *,
    workspace_root: str | Path,
    run_id: str,
    target: SelfEvolveTarget,
    current_trajectory: Iterable[Mapping[str, Any]],
    task_id: str,
    optimizer: CandidateOptimizer,
    apply_policy: str = "proposal",
    post_apply_evaluator: Callable[[CandidateVariant], Any] | None = None,
) -> SelfEvolveRunnerResult:
    trajectory = list(current_trajectory)
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id=task_id,
    )
    trace_pack = dataset.cases[0].trace_pack
    if trace_pack is None:
        raise ValueError("current trajectory dataset did not produce a trace pack")

    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(workspace_root),
        optimizer=optimizer,
        post_apply_evaluator=post_apply_evaluator,
    )
    return await runner.run_explicit_target(
        run_id=run_id,
        target=target,
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy=apply_policy,
    )


def optimize_from_cli_request(
    *,
    workspace_root: str | Path,
    agent: str | None = None,
    task: str | None = None,
    target: str | None = None,
    dataset: str | None = None,
    from_session: str | None = None,
    from_trajectory: str | None = None,
    from_trajectory_set: str | None = None,
    include_prior_runs: bool = False,
    batch_config: str | None = None,
    from_run: str | None = None,
    rerun_evaluator: bool = False,
    current_trajectory: Iterable[Mapping[str, Any]] | None = None,
    iterations: int | None = None,
    apply_policy: str = "proposal",
    infer_target: bool = False,
    inferred_new_skill_policy: InferredNewSkillPolicy
    | str = InferredNewSkillPolicy.AUTO_VERIFIED,
    evaluation_backend: EvaluationBackend | None = None,
    regression_backend: EvaluationBackend | None = None,
    regression_benchmarks: Iterable[str] = (),
    challenger_backend: ChallengerBackend | None = None,
    challenger_enabled: bool = True,
    challenger_max_cases: int = DEFAULT_CHALLENGE_CASES,
    post_apply_evaluator: Callable[[CandidateVariant], Any] | None = None,
    min_eval_cases: int = 30,
    judge_repetitions: int = 3,
    judge_timeout_seconds: float | None = 300.0,
    max_run_tokens: int | None = None,
    total_run_token_budget: int | None = None,
    per_attempt_replay_token_limit: int | None = None,
    max_run_cost_usd: float | Decimal | None = None,
    max_run_wall_seconds: float | Decimal | None = None,
    candidate_generation_tokens_per_unit: int | None = None,
    candidate_generation_cost_usd_per_unit: float | Decimal | None = None,
    candidate_generation_wall_seconds_per_unit: float | Decimal | None = None,
    candidate_screening_tokens_per_unit: int | None = None,
    candidate_screening_cost_usd_per_unit: float | Decimal | None = None,
    candidate_screening_wall_seconds_per_unit: float | Decimal | None = None,
    replay_tokens_per_unit: int | None = None,
    replay_cost_usd_per_unit: float | Decimal | None = None,
    replay_wall_seconds_per_unit: float | Decimal | None = None,
    evaluation_tokens_per_unit: int | None = None,
    evaluation_cost_usd_per_unit: float | Decimal | None = None,
    evaluation_wall_seconds_per_unit: float | Decimal | None = None,
    deprecated_config_mappings: Iterable[str] | Mapping[str, str] | None = None,
    min_score_delta: float = 0.0,
    auto_apply_target_types: tuple[str, ...] = ("skill",),
    allow_generated_target_mutation: bool = False,
    allow_external_target_mutation: bool = False,
    judge_config: SelfEvolveJudgeConfig | Mapping[str, Any] | None = None,
    mutation_model_config: ModelConfig | None = None,
    replay_enabled: bool = False,
    candidate_replay_backend: CandidateReplayBackend | None = None,
    regression_replay_backend: CandidateReplayBackend | None = None,
    replay_timeout_seconds: int = 600,
    replay_total_timeout_seconds: int | None = None,
    replay_max_steps: int | None = None,
    replay_candidate_limit: int = 2,
    candidate_screening_max_cases: int = 3,
    max_generated_candidates: int = 6,
    max_full_evaluation_candidates: int = 3,
    max_score_tiebreak_candidates: int = 1,
    baseline_replay_repetitions: int = 1,
    candidate_replay_repetitions: int = 1,
    replay_repetitions_explicit: bool = False,
    replay_stability_margin: float = 0.0,
    measurement_mode: MeasurementPolicyMode | str | None = None,
    measurement_primary_metric: str = "task_success",
    measurement_minimum_effect: float = 0.0,
    measurement_confidence_level: float = 0.95,
    measurement_min_independent_cases: int = 2,
    measurement_bootstrap_samples: int = 2_000,
    measurement_zero_yield_patience: int = 2,
    measurement_invalid_control_patience: int = 2,
    measurement_maximum_interval_width: float | None = None,
    replay_adaptation_compiler: ReplayAdaptationCompiler | None = None,
    runtime_registry_refresher: Callable[[CandidateVariant], Any] | None = None,
    runtime_skill_activator: Callable[[CandidateVariant], Any] | None = None,
    runtime_registry_compensator: Callable[[CandidateVariant, object | None], Any]
    | None = None,
    runtime_skill_compensator: Callable[[CandidateVariant, object | None], Any]
    | None = None,
    progress_callback: Callable[[str, str], Any] | None = None,
    concurrency_policy: SelfEvolveConcurrencyPolicy | None = None,
    campaign_id: str | None = None,
    campaign_cycle: int | None = None,
    campaign_prior_run_ids: Iterable[str] | None = None,
    campaign_scheduler_checkpoint_run_ids: Iterable[str] | None = None,
    campaign_expected_target: Mapping[str, Any] | None = None,
    campaign_measurement_pending_run_id: str | None = None,
    campaign_measurement_pending_candidate_id: str | None = None,
    from_source: str | None = None,
    source_ingestor: str | None = None,
    source_manifest: str | None = None,
    semantic_evidence_approval: str | None = None,
    semantic_qualification_report: str | None = None,
    ingestion_model_config: ModelConfig | None = None,
    ingestion_only: bool = False,
    frozen_ingestion_id: str | None = None,
    ingestion_registry: IngestionRegistry | None = None,
    skill_evolution_contract: (
        Mapping[str, object] | SkillEvolutionContract | None
    ) = None,
) -> Mapping[str, Any]:
    request = locals()
    return execute_cli_optimization(
        **request,
        runtime=CliOrchestrationRuntime(
            runner_type=SelfEvolveRunner,
            run_budget_context_type=_RunBudgetContext,
            load_or_build_campaign_dataset=_cli_load_or_build_campaign_dataset,
            default_cli_skill_candidate=_default_cli_skill_candidate,
            auto_group_trajectory_log_dataset=_auto_group_trajectory_log_dataset,
            infer_target_from_trace_packs=_infer_target_from_trace_packs,
            target_from_ref=_target_from_ref,
            replay_backend_type=AWorldCliCandidateReplayBackend,
        ),
    )


def _retention_controller(
    store: FilesystemSelfEvolveStore,
) -> ArtifactRetentionController:
    """Build retention with Runner's historical cleanup patch seam."""

    return _owned_retention_controller(
        store,
        cleanup=cleanup_self_evolve_artifacts,
    )


def _artifact_retention_report(
    store: FilesystemSelfEvolveStore,
    run_id: str,
    *,
    previous: Mapping[str, object] | None = None,
) -> dict[str, object]:
    return _owned_artifact_retention_report(
        store,
        run_id,
        previous=previous,
        cleanup=cleanup_self_evolve_artifacts,
    )


def _finalize_run_report(
    store: FilesystemSelfEvolveStore,
    run_id: str,
    *,
    report: dict[str, Any],
    completed_run: SelfEvolveRun,
    previous_artifact_retention: Mapping[str, object] | None = None,
) -> Path:
    return _owned_finalize_run_report(
        store,
        run_id,
        report=report,
        completed_run=completed_run,
        previous_artifact_retention=previous_artifact_retention,
        cleanup=cleanup_self_evolve_artifacts,
    )
