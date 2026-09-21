"""Typed construction boundary for the self-evolve Runner facade.

The public ``SelfEvolveRunner`` constructor deliberately remains stable.  This
module owns normalization, validation, controller construction, and mutable
run-service initialization so the facade does not need to know how those pieces
are assembled.  Nothing in this module imports the Runner.
"""

from __future__ import annotations

import math
import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from aworld.runners.batch import DeterministicTaskBatchExecutor
from aworld.self_evolve.budget import BudgetStage, BudgetUsage
from aworld.self_evolve.challenger import (
    MAX_CHALLENGE_CASES,
    ChallengerBackend,
    DeterministicInvariantChallenger,
)
from aworld.self_evolve.concurrency import (
    SelfEvolveConcurrencyPolicy,
    SelfEvolveExecutionTelemetry,
)
from aworld.self_evolve.controllers.generation import CandidateGenerationController
from aworld.self_evolve.controllers.measurement import (
    CandidateMeasurementController,
    MeasurementPlanningConfig,
    MeasurementPlanningController,
    MeasurementPlanningIdentities,
    measurement_component_identity,
)
from aworld.self_evolve.controllers.measurement_authority import (
    AuthoritativeMeasurementConfig,
    AuthoritativeMeasurementController,
)
from aworld.self_evolve.controllers.measurement_execution import (
    PairedReplayExecutionConfig,
    PairedReplayExecutionController,
)
from aworld.self_evolve.controllers.run_budget_support import configured_budget_usage
from aworld.self_evolve.controllers.run_replay_adaptation import (
    ReplayAdaptationState,
)
from aworld.self_evolve.controllers.screening import CandidateScreeningController
from aworld.self_evolve.evaluation import EvaluationBackend
from aworld.self_evolve.measurement import (
    ControlledExperimentSpec,
    MeasurementEarlyStopPolicy,
    MeasurementPolicyMode,
    MeasurementSummary,
    stable_measurement_fingerprint,
)
from aworld.self_evolve.optimizers.base import CandidateOptimizer
from aworld.self_evolve.provenance import (
    InferredNewSkillPolicy,
    TargetMutationIntent,
)
from aworld.self_evolve.regression import ResolvedRegressionSuite
from aworld.self_evolve.replay import CandidateReplayBackend
from aworld.self_evolve.replay_adaptation import ReplayAdaptationCompiler
from aworld.self_evolve.skill_evolution_contract import SkillEvolutionContract
from aworld.self_evolve.run_defaults import (
    DEFAULT_CANDIDATE_GENERATION_TOKENS_PER_UNIT,
)
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.types import CandidateVariant


@dataclass(frozen=True)
class RunnerRuntimeDependencies:
    """External services and effect seams supplied to one Runner instance."""

    store: FilesystemSelfEvolveStore
    optimizer: CandidateOptimizer
    post_apply_evaluator: Callable[[CandidateVariant], Any] | None
    evaluation_backend: EvaluationBackend | None
    regression_backend: EvaluationBackend | None
    regression_suites: tuple[ResolvedRegressionSuite, ...]
    challenger_backend: ChallengerBackend | None
    candidate_replay_backend: CandidateReplayBackend | None
    regression_replay_backend: CandidateReplayBackend | None
    runtime_registry_refresher: Callable[[CandidateVariant], Any] | None
    runtime_skill_activator: Callable[[CandidateVariant], Any] | None
    progress_callback: Callable[[str, str], Any] | None
    replay_adaptation_compiler: ReplayAdaptationCompiler | None
    concurrency_policy: SelfEvolveConcurrencyPolicy | None
    task_batch_executor: DeterministicTaskBatchExecutor | None
    skill_evolution_contract: SkillEvolutionContract | None
    runner_type_name: str
    runtime_registry_compensator: Callable[
        [CandidateVariant, object | None], Any
    ] | None = None
    runtime_skill_compensator: Callable[
        [CandidateVariant, object | None], Any
    ] | None = None


@dataclass(frozen=True)
class RunnerBudgetConfiguration:
    """Legacy-compatible run ceilings and per-stage cold-start estimates."""

    max_run_tokens: int | None
    total_run_token_budget: int | None
    per_attempt_replay_token_limit: int | None
    max_run_cost_usd: float | Decimal | None
    max_run_wall_seconds: float | Decimal | None
    candidate_generation_tokens_per_unit: int | None
    candidate_generation_cost_usd_per_unit: float | Decimal | None
    candidate_generation_wall_seconds_per_unit: float | Decimal | None
    candidate_screening_tokens_per_unit: int | None
    candidate_screening_cost_usd_per_unit: float | Decimal | None
    candidate_screening_wall_seconds_per_unit: float | Decimal | None
    replay_tokens_per_unit: int | None
    replay_cost_usd_per_unit: float | Decimal | None
    replay_wall_seconds_per_unit: float | Decimal | None
    evaluation_tokens_per_unit: int | None
    evaluation_cost_usd_per_unit: float | Decimal | None
    evaluation_wall_seconds_per_unit: float | Decimal | None
    deprecated_config_mappings: Iterable[str] | Mapping[str, str] | None


@dataclass(frozen=True)
class RunnerReplayConfiguration:
    """Replay, population, and candidate scheduling policy."""

    replay_enabled: bool
    replay_timeout_seconds: int
    replay_total_timeout_seconds: int | None
    replay_resume_dir: str | Path | None
    replay_max_steps: int | None
    replay_candidate_limit: int
    candidate_screening_max_cases: int
    max_generated_candidates: int
    max_full_evaluation_candidates: int
    max_score_tiebreak_candidates: int
    baseline_replay_repetitions: int
    candidate_replay_repetitions: int
    replay_repetitions_explicit: bool
    replay_stability_margin: float
    replay_agent: str | None


@dataclass(frozen=True)
class RunnerMeasurementConfiguration:
    """Measurement authority and resume configuration."""

    mode: MeasurementPolicyMode | str
    primary_metric: str
    minimum_effect: float
    confidence_level: float
    minimum_independent_cases: int
    bootstrap_samples: int
    zero_yield_patience: int
    invalid_control_patience: int
    maximum_interval_width: float | None
    resume_run_id: str | None


@dataclass(frozen=True)
class RunnerPolicyConfiguration:
    """General evolution, application, and generation policy."""

    challenger_enabled: bool
    challenger_max_cases: int
    min_score_delta: float
    pending_duplicate: bool
    max_iterations: int
    min_eval_cases: int
    judge_repetitions: int
    candidate_generation_output_tokens_per_unit: int
    candidate_generation_model_name: str
    auto_apply_target_types: tuple[str, ...]
    allow_generated_target_mutation: bool
    allow_external_target_mutation: bool
    inferred_new_skill_policy: InferredNewSkillPolicy | str
    skip_duplicate_rejected_candidate_gate: bool
    ingestion_model_call_count: int


@dataclass(frozen=True)
class RunnerConstructionRequest:
    """Complete typed input consumed by the construction service."""

    runtime: RunnerRuntimeDependencies
    budget: RunnerBudgetConfiguration
    replay: RunnerReplayConfiguration
    measurement: RunnerMeasurementConfiguration
    policy: RunnerPolicyConfiguration


@dataclass(frozen=True)
class ConstructedRunnerRuntime:
    store: FilesystemSelfEvolveStore
    optimizer: CandidateOptimizer
    post_apply_evaluator: Callable[[CandidateVariant], Any] | None
    evaluation_backend: EvaluationBackend | None
    regression_backend: EvaluationBackend | None
    regression_suites: tuple[ResolvedRegressionSuite, ...]
    challenger_backend: ChallengerBackend | None
    candidate_replay_backend: CandidateReplayBackend | None
    regression_replay_backend: CandidateReplayBackend | None
    skill_evolution_contract: SkillEvolutionContract | None
    runtime_registry_refresher: Callable[[CandidateVariant], Any] | None
    runtime_skill_activator: Callable[[CandidateVariant], Any] | None
    progress_callback: Callable[[str, str], Any] | None
    replay_adaptation_compiler: ReplayAdaptationCompiler
    concurrency_policy: SelfEvolveConcurrencyPolicy
    task_batch_executor: DeterministicTaskBatchExecutor
    runtime_registry_compensator: Callable[
        [CandidateVariant, object | None], Any
    ] | None = None
    runtime_skill_compensator: Callable[
        [CandidateVariant, object | None], Any
    ] | None = None


@dataclass(frozen=True)
class ConstructedRunnerBudget:
    max_run_tokens: int | None
    total_run_token_budget: int | None
    per_attempt_replay_token_limit: int | None
    max_run_cost_usd: Decimal | None
    max_run_wall_seconds: Decimal | None
    deprecated_config_mappings: Mapping[str, str] | tuple[str, ...]
    candidate_generation_tokens_per_unit: int
    candidate_screening_tokens_per_unit: int
    replay_tokens_per_unit: int
    evaluation_tokens_per_unit: int
    cold_start_by_stage: Mapping[BudgetStage, BudgetUsage | None]


@dataclass(frozen=True)
class ConstructedRunnerPolicy:
    challenger_enabled: bool
    challenger_max_cases: int
    min_score_delta: float
    pending_duplicate: bool
    max_iterations: int
    min_eval_cases: int
    judge_repetitions: int
    ingestion_model_call_count: int
    candidate_generation_output_tokens_per_unit: int
    candidate_generation_model_name: str
    auto_apply_target_types: tuple[str, ...]
    allow_generated_target_mutation: bool
    allow_external_target_mutation: bool
    inferred_new_skill_policy: InferredNewSkillPolicy
    skip_duplicate_rejected_candidate_gate: bool


@dataclass(frozen=True)
class ConstructedRunnerReplay:
    replay_enabled: bool
    replay_timeout_seconds: int
    replay_total_timeout_seconds: int | None
    replay_resume_dir: str | None
    replay_max_steps: int | None
    replay_candidate_limit: int
    candidate_screening_max_cases: int
    max_generated_candidates: int
    max_full_evaluation_candidates: int
    max_score_tiebreak_candidates: int
    baseline_replay_repetitions: int
    candidate_replay_repetitions: int
    replay_repetitions_explicit: bool
    replay_stability_margin: float
    replay_agent: str | None


@dataclass(frozen=True)
class ConstructedRunnerMeasurement:
    mode: MeasurementPolicyMode
    primary_metric: str
    minimum_effect: float
    confidence_level: float
    minimum_independent_cases: int
    bootstrap_samples: int
    early_stop_policy: MeasurementEarlyStopPolicy
    resume_run_id: str | None
    experiments: dict[tuple[str, str], ControlledExperimentSpec]
    screening_experiments: dict[tuple[str, str, str], ControlledExperimentSpec]
    summaries: dict[tuple[str, str], MeasurementSummary]


@dataclass(frozen=True)
class ConstructedRunnerControllers:
    generation: CandidateGenerationController
    screening: CandidateScreeningController
    measurement: CandidateMeasurementController
    measurement_planning: MeasurementPlanningController
    authoritative_measurement: AuthoritativeMeasurementController
    paired_replay_execution: PairedReplayExecutionController


@dataclass(frozen=True)
class ConstructedRunnerMutableState:
    active_target_intent: TargetMutationIntent | None
    execution_telemetry: SelfEvolveExecutionTelemetry
    replay_adaptation: ReplayAdaptationState
    candidate_screening_case_observations: dict[str, dict[str, float | int]]
    candidate_screening_control_observations: dict[str, dict[str, object]]
    candidate_screening_observation_dataset_fingerprint: str | None
    candidate_screening_loaded_run_ids: set[str]
    candidate_screening_run_invalid_control_case_ids: dict[str, set[str]]
    current_run_authoritative_case_observations: dict[str, dict[str, int]]


@dataclass(frozen=True)
class RunnerConstructionResult:
    """Cohesive typed bundles projected by the compatibility facade."""

    runtime: ConstructedRunnerRuntime
    budget: ConstructedRunnerBudget
    policy: ConstructedRunnerPolicy
    replay: ConstructedRunnerReplay
    measurement: ConstructedRunnerMeasurement
    controllers: ConstructedRunnerControllers
    mutable: ConstructedRunnerMutableState


def build_runner_construction(
    request: RunnerConstructionRequest,
) -> RunnerConstructionResult:
    """Validate and assemble one Runner without receiving the facade itself."""

    runtime = request.runtime
    budget = request.budget
    replay = request.replay
    measurement = request.measurement
    policy = request.policy

    if (
        isinstance(policy.challenger_max_cases, bool)
        or not 0 < policy.challenger_max_cases <= MAX_CHALLENGE_CASES
    ):
        raise ValueError(
            f"challenger_max_cases must be between 1 and {MAX_CHALLENGE_CASES}"
        )
    if (
        isinstance(policy.ingestion_model_call_count, bool)
        or not isinstance(policy.ingestion_model_call_count, int)
        or policy.ingestion_model_call_count < 0
    ):
        raise ValueError("ingestion_model_call_count must be a non-negative integer")
    if (
        isinstance(policy.candidate_generation_output_tokens_per_unit, bool)
        or policy.candidate_generation_output_tokens_per_unit <= 0
    ):
        raise ValueError("candidate_generation_output_tokens_per_unit must be positive")
    if not isinstance(policy.candidate_generation_model_name, str) or not (
        policy.candidate_generation_model_name.strip()
    ):
        raise ValueError("candidate_generation_model_name must be non-empty")

    total_run_token_budget = (
        budget.max_run_tokens
        if budget.total_run_token_budget is None and budget.max_run_tokens is not None
        else budget.total_run_token_budget
    )
    per_attempt_replay_token_limit = (
        budget.max_run_tokens
        if budget.per_attempt_replay_token_limit is None
        and budget.max_run_tokens is not None
        else budget.per_attempt_replay_token_limit
    )
    legacy_total_budget_mapping = (
        budget.total_run_token_budget is None and budget.max_run_tokens is not None
    )
    legacy_per_attempt_budget_mapping = (
        budget.per_attempt_replay_token_limit is None
        and budget.max_run_tokens is not None
    )
    max_run_cost_usd = (
        Decimal(str(budget.max_run_cost_usd))
        if budget.max_run_cost_usd is not None
        else None
    )
    max_run_wall_seconds = (
        Decimal(str(budget.max_run_wall_seconds))
        if budget.max_run_wall_seconds is not None
        else None
    )
    deprecated_config_mappings: Mapping[str, str] | tuple[str, ...] = (
        dict(budget.deprecated_config_mappings)
        if isinstance(budget.deprecated_config_mappings, Mapping)
        else tuple(budget.deprecated_config_mappings or ())
    )
    legacy_budget_mappings = {
        name: target
        for enabled, name, target in (
            (
                legacy_total_budget_mapping,
                "max_run_tokens_to_total_run_token_budget",
                "total_run_token_budget",
            ),
            (
                legacy_per_attempt_budget_mapping,
                "max_run_tokens_to_per_attempt_replay_token_limit",
                "per_attempt_replay_token_limit",
            ),
        )
        if enabled
    }
    if legacy_budget_mappings:
        if isinstance(deprecated_config_mappings, Mapping):
            deprecated_config_mappings = {
                **dict(deprecated_config_mappings),
                **legacy_budget_mappings,
            }
        else:
            deprecated_config_mappings = tuple(
                dict.fromkeys((*deprecated_config_mappings, *legacy_budget_mappings))
            )

    generation_tokens = (
        DEFAULT_CANDIDATE_GENERATION_TOKENS_PER_UNIT
        if budget.candidate_generation_tokens_per_unit is None
        else budget.candidate_generation_tokens_per_unit
    )
    generation_cost = (
        Decimal("0.05")
        if budget.candidate_generation_cost_usd_per_unit is None
        else budget.candidate_generation_cost_usd_per_unit
    )
    generation_wall = (
        Decimal("120")
        if budget.candidate_generation_wall_seconds_per_unit is None
        else budget.candidate_generation_wall_seconds_per_unit
    )
    screening_tokens = (
        4_096
        if budget.candidate_screening_tokens_per_unit is None
        else budget.candidate_screening_tokens_per_unit
    )
    screening_cost = (
        Decimal("0.05")
        if budget.candidate_screening_cost_usd_per_unit is None
        else budget.candidate_screening_cost_usd_per_unit
    )
    screening_wall = (
        Decimal("210")
        if budget.candidate_screening_wall_seconds_per_unit is None
        else budget.candidate_screening_wall_seconds_per_unit
    )
    replay_tokens = (
        4_096
        if budget.replay_tokens_per_unit is None
        else budget.replay_tokens_per_unit
    )
    replay_cost = (
        Decimal("0.05")
        if budget.replay_cost_usd_per_unit is None
        else budget.replay_cost_usd_per_unit
    )
    replay_wall = (
        Decimal("600")
        if budget.replay_wall_seconds_per_unit is None
        else budget.replay_wall_seconds_per_unit
    )
    evaluation_tokens = (
        2_048
        if budget.evaluation_tokens_per_unit is None
        else budget.evaluation_tokens_per_unit
    )
    evaluation_cost = (
        Decimal("0.02")
        if budget.evaluation_cost_usd_per_unit is None
        else budget.evaluation_cost_usd_per_unit
    )
    evaluation_wall = (
        Decimal("60")
        if budget.evaluation_wall_seconds_per_unit is None
        else budget.evaluation_wall_seconds_per_unit
    )
    cold_start_by_stage = {
        stage: configured_budget_usage(
            tokens=tokens,
            cost_usd=cost,
            wall_seconds=wall,
            token_ceiling=total_run_token_budget,
            cost_ceiling=max_run_cost_usd,
            wall_ceiling=max_run_wall_seconds,
        )
        for stage, tokens, cost, wall in (
            (
                BudgetStage.CANDIDATE_GENERATION,
                generation_tokens,
                generation_cost,
                generation_wall,
            ),
            (
                BudgetStage.CHALLENGER,
                generation_tokens,
                generation_cost,
                generation_wall,
            ),
            (BudgetStage.CONFORMANCE, 0, Decimal("0"), Decimal("30")),
            (BudgetStage.SCREENING, screening_tokens, screening_cost, screening_wall),
            (BudgetStage.PAIRED_REPLAY, replay_tokens, replay_cost, replay_wall),
            (BudgetStage.REGRESSION_REPLAY, replay_tokens, replay_cost, replay_wall),
            (
                BudgetStage.EVALUATION,
                evaluation_tokens,
                evaluation_cost,
                evaluation_wall,
            ),
            (BudgetStage.JUDGE, evaluation_tokens, evaluation_cost, evaluation_wall),
        )
    }

    if (
        replay.replay_total_timeout_seconds is not None
        and replay.replay_total_timeout_seconds <= 0
    ):
        raise ValueError("replay_total_timeout_seconds must be positive")
    replay_resume_dir = (
        str(Path(replay.replay_resume_dir))
        if replay.replay_resume_dir is not None
        else None
    )
    measurement_resume_run_id = (
        str(measurement.resume_run_id).strip()
        if measurement.resume_run_id is not None
        else None
    )
    if measurement_resume_run_id is not None:
        if not measurement_resume_run_id:
            raise ValueError("measurement_resume_run_id must be non-empty")
        if replay_resume_dir is None:
            raise ValueError(
                "measurement authority resume requires its replay directory"
            )
    for name, value in (
        ("candidate_screening_max_cases", replay.candidate_screening_max_cases),
        ("max_generated_candidates", replay.max_generated_candidates),
        ("max_full_evaluation_candidates", replay.max_full_evaluation_candidates),
    ):
        if value <= 0:
            raise ValueError(f"{name} must be positive")
    if replay.max_score_tiebreak_candidates < 0:
        raise ValueError("max_score_tiebreak_candidates must be non-negative")

    measurement_mode = MeasurementPolicyMode(measurement.mode)
    measurement_primary_metric = measurement.primary_metric.strip()
    if not measurement_primary_metric:
        raise ValueError("measurement_primary_metric must be non-empty")
    measurement_minimum_effect = float(measurement.minimum_effect)
    if not math.isfinite(measurement_minimum_effect):
        raise ValueError("measurement_minimum_effect must be finite")
    measurement_confidence_level = float(measurement.confidence_level)
    if not 0 < measurement_confidence_level < 1:
        raise ValueError("measurement_confidence_level must be between 0 and 1")
    if measurement.minimum_independent_cases <= 0:
        raise ValueError("measurement_min_independent_cases must be positive")
    if not 200 <= measurement.bootstrap_samples <= 100_000:
        raise ValueError("measurement_bootstrap_samples must be between 200 and 100000")
    measurement_early_stop_policy = MeasurementEarlyStopPolicy(
        zero_yield_patience=measurement.zero_yield_patience,
        invalid_control_patience=measurement.invalid_control_patience,
        maximum_interval_width=measurement.maximum_interval_width,
    )

    regression_backend = runtime.regression_backend or runtime.evaluation_backend
    challenger_backend = (
        runtime.challenger_backend or DeterministicInvariantChallenger()
        if policy.challenger_enabled
        else None
    )
    regression_replay_backend = (
        runtime.regression_replay_backend
        if runtime.regression_replay_backend is not None
        else runtime.candidate_replay_backend
    )
    generation_model_name = policy.candidate_generation_model_name.strip()
    measurement_summaries: dict[tuple[str, str], MeasurementSummary] = {}
    measurement_experiments: dict[tuple[str, str], ControlledExperimentSpec] = {}
    screening_measurement_experiments: dict[
        tuple[str, str, str], ControlledExperimentSpec
    ] = {}

    measurement_planning_controller = MeasurementPlanningController(
        store=runtime.store,
        config=MeasurementPlanningConfig(
            mode=measurement_mode,
            identities=MeasurementPlanningIdentities(
                task_model=stable_measurement_fingerprint(
                    {
                        "replay_agent": replay.replay_agent,
                        "execution_backend": measurement_component_identity(
                            runtime.candidate_replay_backend
                        ),
                    }
                ),
                generator=stable_measurement_fingerprint(
                    measurement_component_identity(runtime.optimizer)
                ),
                scheduler=stable_measurement_fingerprint(
                    {
                        "kind": "StageAwareCandidateScheduler",
                        "max_generated_candidates": replay.max_generated_candidates,
                        "max_authoritative_candidates": replay.max_full_evaluation_candidates,
                        "replay_candidate_limit": replay.replay_candidate_limit,
                    }
                ),
                evaluator=stable_measurement_fingerprint(
                    {
                        "evaluation": measurement_component_identity(
                            runtime.evaluation_backend
                        ),
                        "regression": measurement_component_identity(
                            regression_backend
                        ),
                        "judge_repetitions": policy.judge_repetitions,
                    }
                ),
                runtime=stable_measurement_fingerprint(
                    {
                        "python": list(sys.version_info[:3]),
                        "platform": sys.platform,
                        "runner": runtime.runner_type_name,
                    }
                ),
            ),
            resume_run_id=measurement_resume_run_id,
            replay_resume_dir=replay_resume_dir,
            replay_enabled=replay.replay_enabled,
            replay_backend_available=runtime.candidate_replay_backend is not None,
            baseline_replay_repetitions=replay.baseline_replay_repetitions,
            candidate_replay_repetitions=replay.candidate_replay_repetitions,
            replay_repetitions_explicit=replay.replay_repetitions_explicit,
            judge_repetitions=policy.judge_repetitions,
            evaluation_backend_available=runtime.evaluation_backend is not None,
            minimum_independent_cases=measurement.minimum_independent_cases,
            primary_metric=measurement_primary_metric,
            minimum_effect=measurement_minimum_effect,
            confidence_level=measurement_confidence_level,
            bootstrap_samples=measurement.bootstrap_samples,
            early_stop_policy=measurement_early_stop_policy,
            total_run_token_budget=total_run_token_budget,
            per_attempt_replay_token_limit=per_attempt_replay_token_limit,
            max_run_cost_usd=max_run_cost_usd,
            max_run_wall_seconds=max_run_wall_seconds,
            replay_timeout_seconds=replay.replay_timeout_seconds,
        ),
    )

    replay_adaptation_compiler = (
        runtime.replay_adaptation_compiler or ReplayAdaptationCompiler()
    )
    concurrency_policy = runtime.concurrency_policy or SelfEvolveConcurrencyPolicy()
    task_batch_executor = (
        runtime.task_batch_executor or DeterministicTaskBatchExecutor()
    )
    generation_controller = CandidateGenerationController(
        output_tokens_per_candidate=(
            policy.candidate_generation_output_tokens_per_unit
        ),
        model_name=generation_model_name,
    )
    screening_controller = CandidateScreeningController()
    measurement_controller = CandidateMeasurementController(
        store=runtime.store,
        primary_metric=measurement_primary_metric,
        summaries=measurement_summaries,
    )
    authoritative_measurement_controller = AuthoritativeMeasurementController(
        store=runtime.store,
        config=AuthoritativeMeasurementConfig(
            mode=measurement_mode,
            resume_run_id=measurement_resume_run_id,
            campaign_wall_deadline_seconds=(
                float(replay.replay_total_timeout_seconds)
                if replay.replay_total_timeout_seconds is not None
                else None
            ),
        ),
    )
    paired_replay_execution_controller = PairedReplayExecutionController(
        store=runtime.store,
        config=PairedReplayExecutionConfig(
            replay_enabled=replay.replay_enabled,
            replay_backend=runtime.candidate_replay_backend,
            replay_agent=replay.replay_agent,
            baseline_repetitions=replay.baseline_replay_repetitions,
            candidate_repetitions=replay.candidate_replay_repetitions,
            repetitions_explicit=replay.replay_repetitions_explicit,
            minimum_independent_cases=measurement.minimum_independent_cases,
            timeout_seconds=replay.replay_timeout_seconds,
            total_timeout_seconds=replay.replay_total_timeout_seconds,
            max_steps=replay.replay_max_steps,
            max_tokens=per_attempt_replay_token_limit,
            resume_replay_dir=replay_resume_dir,
            invalid_control_patience=(
                measurement_early_stop_policy.invalid_control_patience
            ),
            measurement_mode=measurement_mode,
        ),
    )
    return RunnerConstructionResult(
        runtime=ConstructedRunnerRuntime(
            store=runtime.store,
            optimizer=runtime.optimizer,
            post_apply_evaluator=runtime.post_apply_evaluator,
            evaluation_backend=runtime.evaluation_backend,
            regression_backend=regression_backend,
            regression_suites=tuple(runtime.regression_suites),
            challenger_backend=challenger_backend,
            candidate_replay_backend=runtime.candidate_replay_backend,
            regression_replay_backend=regression_replay_backend,
            skill_evolution_contract=runtime.skill_evolution_contract,
            runtime_registry_refresher=runtime.runtime_registry_refresher,
            runtime_skill_activator=runtime.runtime_skill_activator,
            runtime_registry_compensator=runtime.runtime_registry_compensator,
            runtime_skill_compensator=runtime.runtime_skill_compensator,
            progress_callback=runtime.progress_callback,
            replay_adaptation_compiler=replay_adaptation_compiler,
            concurrency_policy=concurrency_policy,
            task_batch_executor=task_batch_executor,
        ),
        budget=ConstructedRunnerBudget(
            max_run_tokens=budget.max_run_tokens,
            total_run_token_budget=total_run_token_budget,
            per_attempt_replay_token_limit=per_attempt_replay_token_limit,
            max_run_cost_usd=max_run_cost_usd,
            max_run_wall_seconds=max_run_wall_seconds,
            deprecated_config_mappings=deprecated_config_mappings,
            candidate_generation_tokens_per_unit=generation_tokens,
            candidate_screening_tokens_per_unit=screening_tokens,
            replay_tokens_per_unit=replay_tokens,
            evaluation_tokens_per_unit=evaluation_tokens,
            cold_start_by_stage=cold_start_by_stage,
        ),
        policy=ConstructedRunnerPolicy(
            challenger_enabled=policy.challenger_enabled,
            challenger_max_cases=policy.challenger_max_cases,
            min_score_delta=policy.min_score_delta,
            pending_duplicate=policy.pending_duplicate,
            max_iterations=policy.max_iterations,
            min_eval_cases=policy.min_eval_cases,
            judge_repetitions=policy.judge_repetitions,
            ingestion_model_call_count=policy.ingestion_model_call_count,
            candidate_generation_output_tokens_per_unit=(
                policy.candidate_generation_output_tokens_per_unit
            ),
            candidate_generation_model_name=generation_model_name,
            auto_apply_target_types=tuple(policy.auto_apply_target_types),
            allow_generated_target_mutation=(policy.allow_generated_target_mutation),
            allow_external_target_mutation=(policy.allow_external_target_mutation),
            inferred_new_skill_policy=InferredNewSkillPolicy(
                policy.inferred_new_skill_policy
            ),
            skip_duplicate_rejected_candidate_gate=(
                policy.skip_duplicate_rejected_candidate_gate
            ),
        ),
        replay=ConstructedRunnerReplay(
            replay_enabled=replay.replay_enabled,
            replay_timeout_seconds=replay.replay_timeout_seconds,
            replay_total_timeout_seconds=replay.replay_total_timeout_seconds,
            replay_resume_dir=replay_resume_dir,
            replay_max_steps=replay.replay_max_steps,
            replay_candidate_limit=replay.replay_candidate_limit,
            candidate_screening_max_cases=replay.candidate_screening_max_cases,
            max_generated_candidates=replay.max_generated_candidates,
            max_full_evaluation_candidates=(replay.max_full_evaluation_candidates),
            max_score_tiebreak_candidates=(replay.max_score_tiebreak_candidates),
            baseline_replay_repetitions=replay.baseline_replay_repetitions,
            candidate_replay_repetitions=replay.candidate_replay_repetitions,
            replay_repetitions_explicit=replay.replay_repetitions_explicit,
            replay_stability_margin=replay.replay_stability_margin,
            replay_agent=replay.replay_agent,
        ),
        measurement=ConstructedRunnerMeasurement(
            mode=measurement_mode,
            primary_metric=measurement_primary_metric,
            minimum_effect=measurement_minimum_effect,
            confidence_level=measurement_confidence_level,
            minimum_independent_cases=measurement.minimum_independent_cases,
            bootstrap_samples=measurement.bootstrap_samples,
            early_stop_policy=measurement_early_stop_policy,
            resume_run_id=measurement_resume_run_id,
            experiments=measurement_experiments,
            screening_experiments=screening_measurement_experiments,
            summaries=measurement_summaries,
        ),
        controllers=ConstructedRunnerControllers(
            generation=generation_controller,
            screening=screening_controller,
            measurement=measurement_controller,
            measurement_planning=measurement_planning_controller,
            authoritative_measurement=authoritative_measurement_controller,
            paired_replay_execution=paired_replay_execution_controller,
        ),
        mutable=ConstructedRunnerMutableState(
            active_target_intent=None,
            execution_telemetry=SelfEvolveExecutionTelemetry(),
            replay_adaptation=ReplayAdaptationState(),
            candidate_screening_case_observations={},
            candidate_screening_control_observations={},
            candidate_screening_observation_dataset_fingerprint=None,
            candidate_screening_loaded_run_ids=set(),
            candidate_screening_run_invalid_control_case_ids={},
            current_run_authoritative_case_observations={},
        ),
    )


__all__ = [
    "ConstructedRunnerBudget",
    "ConstructedRunnerControllers",
    "ConstructedRunnerMeasurement",
    "ConstructedRunnerMutableState",
    "ConstructedRunnerPolicy",
    "ConstructedRunnerReplay",
    "ConstructedRunnerRuntime",
    "RunnerBudgetConfiguration",
    "RunnerConstructionRequest",
    "RunnerConstructionResult",
    "RunnerMeasurementConfiguration",
    "RunnerPolicyConfiguration",
    "RunnerReplayConfiguration",
    "RunnerRuntimeDependencies",
    "build_runner_construction",
    "configured_budget_usage",
]
