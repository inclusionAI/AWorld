"""Typed startup and prior-history assembly for explicit-target runs."""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal

from aworld.self_evolve.budget import (
    BudgetCeilings,
    BudgetStage,
    BudgetUsage,
    BudgetUsageCompleteness,
    BudgetUsageObservation,
    CandidateAttemptKey,
    RunBudgetLedger,
    SchedulerState,
    StageAwareCandidateScheduler,
)
from aworld.self_evolve.concurrency import SelfEvolveExecutionTelemetry
from aworld.self_evolve.challenger import ChallengeReport, ChallengerBackend
from aworld.self_evolve.controllers.run_execution import ExplicitTargetRunRequest
from aworld.self_evolve.controllers.run_resources import RunBudgetContext
from aworld.self_evolve.controllers.run_state import ExplicitRunStateAccumulator
from aworld.self_evolve.datasets import SelfEvolveDataset
from aworld.self_evolve.evaluation import EvaluationBackend
from aworld.self_evolve.lessons import LessonRecord
from aworld.self_evolve.measurement import (
    MeasurementEarlyStopPolicy,
    MeasurementSummary,
)
from aworld.self_evolve.optimizers.base import (
    CandidateOptimizer,
    CandidateSourceDisposition,
)
from aworld.self_evolve.regression import RegressionEvidence
from aworld.self_evolve.replay import CandidateReplayBackend, CandidateReplayResult
from aworld.self_evolve.replay_adaptation import (
    ReplayAdaptationCompiler,
    ReplayPreflightReport,
)
from aworld.self_evolve.run_defaults import (
    DEFAULT_INGESTION_MODEL_TOKENS_PER_CALL,
)
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.targets import SelfEvolveTarget
from aworld.self_evolve.trace_pack import TracePack
from aworld.self_evolve.types import CandidateVariant, EvaluationSummary, GateResult


@dataclass(frozen=True)
class RunBootstrapPolicy:
    total_run_token_budget: int | None
    max_run_cost_usd: Decimal | None
    max_run_wall_seconds: Decimal | None
    ingestion_model_call_count: int
    replay_timeout_seconds: int
    replay_candidate_limit: int
    screening_timeout_ceiling_seconds: int


@dataclass(frozen=True)
class RunBootstrapState:
    candidate_screening_case_observations: dict[str, dict[str, float | int]]
    candidate_screening_control_observations: dict[str, dict[str, object]]
    candidate_screening_loaded_run_ids: set[str]
    current_run_authoritative_case_observations: dict[str, dict[str, int]]
    candidate_screening_observation_dataset_fingerprint: str | None


@dataclass(frozen=True)
class RunBootstrapRuntime:
    store: FilesystemSelfEvolveStore
    optimizer: CandidateOptimizer
    challenger_backend: ChallengerBackend | None
    candidate_replay_backend: CandidateReplayBackend | None
    regression_replay_backend: CandidateReplayBackend | None
    evaluation_backend: EvaluationBackend | None
    cold_start_by_stage: Mapping[BudgetStage, BudgetUsage | None]
    screening_observation_scope_fingerprint: Callable[..., str]
    restore_campaign_screening_case_observations: Callable[..., None]
    restore_historical_screening_lifecycle_observations: Callable[..., None]
    screening_control_harness_fingerprint: Callable[[], str]
    screening_control_preflight: Callable[..., Mapping[str, object]]
    backend_proves_zero_budget_usage: Callable[[object | None, BudgetStage], bool]
    load_prior_scheduler_state: Callable[..., SchedulerState]
    candidate_generation_limit: Callable[..., int]
    register_budget_context: Callable[[RunBudgetContext], None]


@dataclass(frozen=True)
class RunBootstrapRequest:
    run: ExplicitTargetRunRequest
    policy: RunBootstrapPolicy
    state: RunBootstrapState
    runtime: RunBootstrapRuntime


@dataclass(frozen=True)
class RunBootstrapResult:
    execution_telemetry: SelfEvolveExecutionTelemetry
    screening_control_preflight: Mapping[str, object]
    budget_context: RunBudgetContext
    scheduler: StageAwareCandidateScheduler
    scheduler_state: SchedulerState
    scheduler_decisions: list[dict[str, object]]
    screening_observation_dataset_fingerprint: str


def bootstrap_explicit_target_run(request: RunBootstrapRequest) -> RunBootstrapResult:
    """Initialize scoped observation, budget, and scheduler state for one run."""

    run = request.run
    policy = request.policy
    state = request.state
    runtime = request.runtime
    screening_dataset_fingerprint = runtime.screening_observation_scope_fingerprint(
        dataset=run.dataset, target=run.target
    )
    same_observation_scope = (
        state.candidate_screening_observation_dataset_fingerprint
        == screening_dataset_fingerprint
    )
    candidate_observations = (
        copy.deepcopy(state.candidate_screening_case_observations)
        if same_observation_scope
        else {}
    )
    control_observations = (
        copy.deepcopy(state.candidate_screening_control_observations)
        if same_observation_scope
        else {}
    )
    loaded_run_ids = (
        set(state.candidate_screening_loaded_run_ids)
        if same_observation_scope
        else set()
    )
    runtime.restore_campaign_screening_case_observations(
        candidate_observations,
        store=runtime.store,
        prior_run_ids=tuple(run.campaign_prior_run_ids or ()),
        loaded_run_ids=loaded_run_ids,
        control_observations=control_observations,
        harness_fingerprint=runtime.screening_control_harness_fingerprint(),
    )
    runtime.restore_historical_screening_lifecycle_observations(
        candidate_observations,
        store=runtime.store,
        target=run.target.identity,
        dataset=run.dataset,
        current_run_id=run.run_id,
        control_observations=control_observations,
        loaded_run_ids=loaded_run_ids,
        harness_fingerprint=runtime.screening_control_harness_fingerprint(),
    )
    screening_control_preflight = runtime.screening_control_preflight(
        run.dataset,
        observations=candidate_observations,
        timeout_ceiling_seconds=min(
            policy.replay_timeout_seconds,
            policy.screening_timeout_ceiling_seconds,
        ),
        harness_fingerprint=runtime.screening_control_harness_fingerprint(),
    )
    budget_context = RunBudgetContext(
        ledger=RunBudgetLedger(
            BudgetCeilings(
                total_tokens=policy.total_run_token_budget,
                total_cost_usd=policy.max_run_cost_usd,
                wall_seconds=policy.max_run_wall_seconds,
            )
        ),
        cold_start_by_stage=runtime.cold_start_by_stage,
        backend_proven_zero_by_stage={
            BudgetStage.CANDIDATE_GENERATION: runtime.backend_proves_zero_budget_usage(
                runtime.optimizer, BudgetStage.CANDIDATE_GENERATION
            ),
            BudgetStage.CHALLENGER: runtime.backend_proves_zero_budget_usage(
                runtime.challenger_backend, BudgetStage.CHALLENGER
            ),
            BudgetStage.SCREENING: runtime.backend_proves_zero_budget_usage(
                runtime.candidate_replay_backend, BudgetStage.SCREENING
            ),
            BudgetStage.PAIRED_REPLAY: runtime.backend_proves_zero_budget_usage(
                runtime.candidate_replay_backend, BudgetStage.PAIRED_REPLAY
            ),
            BudgetStage.REGRESSION_REPLAY: runtime.backend_proves_zero_budget_usage(
                runtime.regression_replay_backend, BudgetStage.REGRESSION_REPLAY
            ),
            BudgetStage.EVALUATION: runtime.backend_proves_zero_budget_usage(
                runtime.evaluation_backend, BudgetStage.EVALUATION
            ),
            BudgetStage.JUDGE: runtime.backend_proves_zero_budget_usage(
                runtime.evaluation_backend, BudgetStage.JUDGE
            ),
        },
    )
    runtime.register_budget_context(budget_context)
    if policy.ingestion_model_call_count:
        ingestion_budget = budget_context.reserve(
            BudgetStage.CANDIDATE_GENERATION,
            "frozen-dataset-ingestion",
            units=policy.ingestion_model_call_count,
            request_derived_tokens=(
                policy.ingestion_model_call_count
                * DEFAULT_INGESTION_MODEL_TOKENS_PER_CALL
            ),
        )
        if not ingestion_budget.allowed:
            raise ValueError("dataset ingestion model usage exceeds the run budget")
        budget_context.debit(
            ingestion_budget,
            usage_observation=BudgetUsageObservation(
                known_lower_bound=BudgetUsage(),
                completeness=BudgetUsageCompleteness.incomplete(),
            ),
            actual_source="reserved_fallback_pre_run_ingestion_model_usage",
        )
    scheduler = StageAwareCandidateScheduler(
        exploration_population=runtime.candidate_generation_limit(
            replay_candidate_limit=policy.replay_candidate_limit
        )
    )
    scheduler_state = runtime.load_prior_scheduler_state(
        runtime.store,
        run.target.identity,
        current_run_id=run.run_id,
        allowed_run_ids=(
            run.campaign_scheduler_checkpoint_run_ids
            if run.campaign_scheduler_checkpoint_run_ids is not None
            else run.campaign_prior_run_ids
        ),
    )
    state.candidate_screening_case_observations.clear()
    state.candidate_screening_case_observations.update(candidate_observations)
    state.candidate_screening_control_observations.clear()
    state.candidate_screening_control_observations.update(control_observations)
    state.candidate_screening_loaded_run_ids.clear()
    state.candidate_screening_loaded_run_ids.update(loaded_run_ids)
    state.current_run_authoritative_case_observations.clear()
    return RunBootstrapResult(
        execution_telemetry=SelfEvolveExecutionTelemetry(),
        screening_control_preflight=screening_control_preflight,
        budget_context=budget_context,
        scheduler=scheduler,
        scheduler_state=scheduler_state,
        scheduler_decisions=[],
        screening_observation_dataset_fingerprint=screening_dataset_fingerprint,
    )


@dataclass(frozen=True)
class RunHistoryPolicy:
    min_score_delta: float
    min_eval_cases: int
    judge_repetitions: int
    candidate_screening_max_cases: int
    max_generated_candidates: int
    max_full_evaluation_candidates: int
    max_score_tiebreak_candidates: int
    replay_enabled: bool
    baseline_replay_repetitions: int
    candidate_replay_repetitions: int
    replay_stability_margin: float
    replay_timeout_seconds: int
    replay_total_timeout_seconds: int | None
    measurement_mode: str
    measurement_primary_metric: str
    measurement_minimum_effect: float
    measurement_confidence_level: float
    measurement_min_independent_cases: int
    measurement_early_stop_policy: MeasurementEarlyStopPolicy


@dataclass(frozen=True)
class RunHistoryRuntime:
    store: FilesystemSelfEvolveStore
    replay_adaptation_compiler: ReplayAdaptationCompiler
    load_prior_rejected_feedback: Callable[..., tuple[EvaluationSummary, ...]]
    extract_lesson_records: Callable[..., tuple[LessonRecord, ...]]
    non_authoritative_candidate_rejection: Callable[[Mapping[str, object]], bool]
    load_prior_candidate_package_index: Callable[
        ..., tuple[dict[str, str], dict[str, str]]
    ]
    load_prior_rejected_semantic_lesson_fingerprints: Callable[..., set[object]]
    replayable_user_task_dataset: Callable[[SelfEvolveDataset], SelfEvolveDataset]
    target_package_inventory: Callable[[SelfEvolveTarget], tuple[str, ...]]
    target_package_sources: Callable[..., dict[str, Mapping[str, object]]]
    is_verified_apply_policy: Callable[[str], bool]


@dataclass(frozen=True)
class RunHistoryRequest:
    run_id: str
    target: SelfEvolveTarget
    dataset: SelfEvolveDataset
    trace_packs: tuple[TracePack, ...]
    apply_policy: str
    campaign_prior_run_ids: tuple[str, ...] | None
    screening_control_preflight: Mapping[str, object]
    policy: RunHistoryPolicy
    runtime: RunHistoryRuntime


@dataclass(frozen=True)
class RestoredRunHistory:
    prior_feedback: tuple[EvaluationSummary, ...]
    generation_lesson_records: tuple[LessonRecord, ...]
    rejected_candidate_ids: set[str]
    accepted_candidate_ids: set[str]
    canonical_candidate_id_by_package: dict[str, str]
    package_fingerprint_by_candidate_id: dict[str, str]
    rejected_semantic_lesson_fingerprints: set[object]
    replay_preflight: ReplayPreflightReport
    target_package_inventory: tuple[str, ...]
    target_package_sources: dict[str, Mapping[str, object]]


@dataclass(frozen=True)
class InitialIterationState:
    selected_candidate: CandidateVariant | None
    validation_feedback: tuple[EvaluationSummary, ...]
    all_candidates: list[CandidateVariant]
    candidate_source_dispositions: dict[str, CandidateSourceDisposition]
    fresh_evaluation_required: bool
    optimizer_diagnostics: list[dict[str, object]]
    optimizer_lineage_paths: list[str]
    optimizer_lineage_paths_by_candidate: dict[str, str]
    iteration_reports: list[dict[str, object]]
    iteration_states: list[dict[str, object]]
    population_screening_reports: list[dict[str, object]]
    baseline_summary: EvaluationSummary | None
    candidate_summary: EvaluationSummary | None
    held_out_summary: EvaluationSummary | None
    regression_evidence: RegressionEvidence | None
    challenge_report: ChallengeReport | None
    measurement_summary: MeasurementSummary | None
    latest_handbook_slice: Mapping[str, object] | None
    replay_result: CandidateReplayResult | None
    replay_dataset: SelfEvolveDataset | None
    gate_results: list[GateResult]
    current_run_attempted_candidate_ids: set[str]
    current_run_candidate_id_by_package: dict[str, str]
    current_run_package_fingerprint_by_candidate_id: dict[str, str]
    current_run_candidate_id_by_semantic_package: dict[str, str]
    attempt_key_by_candidate_id: dict[str, CandidateAttemptKey]
    baseline_preflight_blocked: bool
    infrastructure_blocked: bool
    shared_validation_gate: GateResult | None
    run_state: ExplicitRunStateAccumulator


@dataclass(frozen=True)
class RunHistoryResult:
    restored: RestoredRunHistory
    iteration: InitialIterationState
    verification_settings: dict[str, object]
    repair_reserved_slot_count: int


def bootstrap_run_history(request: RunHistoryRequest) -> RunHistoryResult:
    """Assemble initial iteration state and prior-campaign evidence."""

    runtime = request.runtime
    policy = request.policy
    prior_feedback = runtime.load_prior_rejected_feedback(
        runtime.store,
        request.target.identity,
        current_run_id=request.run_id,
        allowed_run_ids=request.campaign_prior_run_ids,
    )
    generation_lesson_records = runtime.extract_lesson_records(
        prior_feedback,
        target_scope={
            "target_type": request.target.identity.target_type,
            "target_id": request.target.identity.target_id,
        },
        trace_packs=request.trace_packs,
    )
    rejected_candidate_ids = {
        feedback.variant_id
        for feedback in prior_feedback
        if feedback.metrics.get("candidate_status") == "rejected"
        and not runtime.non_authoritative_candidate_rejection(feedback.metrics)
    }
    accepted_candidate_ids = {
        feedback.variant_id
        for feedback in prior_feedback
        if feedback.metrics.get("candidate_status") == "accepted"
        and feedback.metrics.get("publication_completed") is True
    }
    current_run_attempted_candidate_ids: set[str] = set()
    canonical_candidate_id_by_package, package_fingerprint_by_candidate_id = (
        runtime.load_prior_candidate_package_index(
            runtime.store,
            request.target.identity,
            current_run_id=request.run_id,
            candidate_ids=rejected_candidate_ids | accepted_candidate_ids,
            allowed_run_ids=request.campaign_prior_run_ids,
        )
    )
    rejected_semantic_lesson_fingerprints = (
        runtime.load_prior_rejected_semantic_lesson_fingerprints(
            runtime.store,
            request.target.identity,
            current_run_id=request.run_id,
            allowed_run_ids=request.campaign_prior_run_ids,
        )
    )
    replay_preflight = runtime.replay_adaptation_compiler.preflight(
        dataset=runtime.replayable_user_task_dataset(request.dataset),
        workspace_root=runtime.store.workspace_root,
    )
    runtime.store.write_replay_requirements(request.run_id, replay_preflight)
    target_package_inventory = runtime.target_package_inventory(request.target)
    target_package_sources = runtime.target_package_sources(
        request.target, inventory=target_package_inventory
    )
    verification_settings: dict[str, object] = {
        "min_score_delta": policy.min_score_delta,
        "min_eval_cases": policy.min_eval_cases,
        "judge_repetitions": policy.judge_repetitions,
        "candidate_screening_max_cases": policy.candidate_screening_max_cases,
        "max_generated_candidates": policy.max_generated_candidates,
        "max_full_evaluation_candidates": policy.max_full_evaluation_candidates,
        "max_score_tiebreak_candidates": policy.max_score_tiebreak_candidates,
        "replay_enabled": policy.replay_enabled,
        "baseline_replay_repetitions": policy.baseline_replay_repetitions,
        "candidate_replay_repetitions": policy.candidate_replay_repetitions,
        "replay_stability_margin": policy.replay_stability_margin,
        "replay_timeout_seconds": policy.replay_timeout_seconds,
        "replay_total_timeout_seconds": policy.replay_total_timeout_seconds,
        "measurement_mode": policy.measurement_mode,
        "measurement_primary_metric": policy.measurement_primary_metric,
        "measurement_minimum_effect": policy.measurement_minimum_effect,
        "measurement_confidence_level": policy.measurement_confidence_level,
        "measurement_min_independent_cases": policy.measurement_min_independent_cases,
        "measurement_invalid_control_patience": policy.measurement_early_stop_policy.invalid_control_patience,
    }
    gate_results: list[GateResult] = []
    baseline_preflight_blocked = (
        request.screening_control_preflight.get("candidate_generation_allowed") is False
    )
    if baseline_preflight_blocked:
        gate_results.append(
            GateResult(
                gate_name="evolvability_preflight",
                passed=False,
                reason=(
                    "known baseline controls cannot execute reliably; repair the "
                    "shared replay harness before mutating the skill"
                ),
                details=dict(request.screening_control_preflight),
            )
        )
    iteration_reports: list[dict[str, object]] = []
    iteration_states: list[dict[str, object]] = []
    run_state = ExplicitRunStateAccumulator(
        validation_feedback=(),
        iteration_reports=iteration_reports,
        iteration_states=iteration_states,
        current_run_attempted_candidate_ids=current_run_attempted_candidate_ids,
        rejected_candidate_ids=rejected_candidate_ids,
        accepted_candidate_ids=accepted_candidate_ids,
        baseline_preflight_blocked=baseline_preflight_blocked,
        infrastructure_blocked=False,
    )
    restored = RestoredRunHistory(
        prior_feedback=prior_feedback,
        generation_lesson_records=generation_lesson_records,
        rejected_candidate_ids=rejected_candidate_ids,
        accepted_candidate_ids=accepted_candidate_ids,
        canonical_candidate_id_by_package=canonical_candidate_id_by_package,
        package_fingerprint_by_candidate_id=package_fingerprint_by_candidate_id,
        rejected_semantic_lesson_fingerprints=(rejected_semantic_lesson_fingerprints),
        replay_preflight=replay_preflight,
        target_package_inventory=target_package_inventory,
        target_package_sources=target_package_sources,
    )
    iteration = InitialIterationState(
        selected_candidate=None,
        validation_feedback=(),
        all_candidates=[],
        candidate_source_dispositions={},
        fresh_evaluation_required=False,
        optimizer_diagnostics=[],
        optimizer_lineage_paths=[],
        optimizer_lineage_paths_by_candidate={},
        iteration_reports=iteration_reports,
        iteration_states=iteration_states,
        population_screening_reports=[],
        baseline_summary=None,
        candidate_summary=None,
        held_out_summary=None,
        regression_evidence=None,
        challenge_report=None,
        measurement_summary=None,
        latest_handbook_slice=None,
        replay_result=None,
        replay_dataset=None,
        gate_results=gate_results,
        current_run_attempted_candidate_ids=current_run_attempted_candidate_ids,
        current_run_candidate_id_by_package={},
        current_run_package_fingerprint_by_candidate_id={},
        current_run_candidate_id_by_semantic_package={},
        attempt_key_by_candidate_id={},
        baseline_preflight_blocked=baseline_preflight_blocked,
        infrastructure_blocked=False,
        shared_validation_gate=None,
        run_state=run_state,
    )
    return RunHistoryResult(
        restored=restored,
        iteration=iteration,
        verification_settings=verification_settings,
        repair_reserved_slot_count=(
            1
            if runtime.is_verified_apply_policy(request.apply_policy)
            and policy.max_generated_candidates > 1
            else 0
        ),
    )


__all__ = [
    "InitialIterationState",
    "RestoredRunHistory",
    "RunBootstrapPolicy",
    "RunBootstrapRequest",
    "RunBootstrapResult",
    "RunBootstrapRuntime",
    "RunBootstrapState",
    "RunHistoryPolicy",
    "RunHistoryRequest",
    "RunHistoryResult",
    "RunHistoryRuntime",
    "bootstrap_explicit_target_run",
    "bootstrap_run_history",
]
