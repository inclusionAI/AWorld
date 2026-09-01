"""Typed execution controller for candidate-generation iterations."""

from __future__ import annotations
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Protocol
from aworld.self_evolve.budget import (
    BudgetDecision,
    BudgetStage,
    CandidateAttemptKey,
    CandidateAttemptStage,
    ScheduledCandidateSlot,
    ScheduledSlotRole,
    SchedulerDecision,
    SchedulerState,
    StageAwareCandidateScheduler,
)
from aworld.self_evolve.campaign_policy import (
    is_verified_apply_policy as _is_verified_apply_policy,
)
from aworld.self_evolve.candidate_package import (
    candidate_package_fingerprint,
    candidate_semantic_package_fingerprint,
)
from aworld.self_evolve.concurrency import SelfEvolveExecutionTelemetry
from aworld.self_evolve.controllers.generation import CandidateGenerationController
from aworld.self_evolve.controllers.run_execution import (
    duplicate_accepted_candidate_gate as _duplicate_accepted_candidate_gate,
    duplicate_rejected_candidate_gate as _duplicate_rejected_candidate_gate,
    iteration_report_item as _iteration_report_item,
    iteration_state as _iteration_state,
)
from aworld.self_evolve.controllers.run_generation_helpers import (
    _MAX_CONSECUTIVE_DUPLICATE_POPULATION_STALLS,
    _MAX_CONSECUTIVE_MATERIALIZATION_STALLS,
    _MAX_CONSECUTIVE_POLICY_FILTER_STALLS,
    _candidate_attempt_placeholder,
    _candidate_generation_actual_usage,
    _candidate_materialization_failure_events,
    _candidate_materialization_failures,
    _candidate_materialization_stall_signature,
    _candidate_policy_filter_event,
    _candidate_policy_filter_signature,
    _canonicalize_verified_prerequisite_files,
    _is_semantic_lesson_duplicate,
    _known_duplicate_candidate_count,
    _lineage_semantic_lesson_fingerprints,
    _optimizer_stored_candidate_admission_reason,
    _rank_candidate_population,
    _retryable_candidate_generation_failure,
    _scheduler_state_with_mutation_families,
    _semantic_lesson_duplicate_count,
    _semantic_lesson_duplicate_feedback,
    _typed_repair_frontiers,
    _with_versioned_semantic_lineage,
)
from aworld.self_evolve.controllers.run_state import ExplicitRunStateAccumulator
from aworld.self_evolve.controllers.screening_execution import (
    _budget_usage_for_attempt_event,
    _emit_progress,
    _non_negative_int,
)
from aworld.self_evolve.controllers.screening_helpers import (
    _candidate_screening_dataset,
)
from aworld.self_evolve.datasets import SelfEvolveDataset
from aworld.self_evolve.evolution_context import compile_evolution_context
from aworld.self_evolve.failure_events import FailureOwner, FailureScope
from aworld.self_evolve.feedback_diagnostics import (
    _feedback_has_candidate_repair_conformance,
    _merge_validation_feedback,
    _next_progress_repair_extension_family,
)
from aworld.self_evolve.handbook import load_handbook_slice_for_target
from aworld.self_evolve.lessons import LessonRecord, extract_lesson_records
from aworld.self_evolve.optimizers.base import (
    CandidateGenerationOutcomeKind,
    CandidateOptimizer,
    CandidateSourceDisposition,
    CandidateSourceKind,
    OptimizerRequest,
    OptimizerResult,
)
from aworld.self_evolve.replay_adaptation import ReplayPreflightReport
from aworld.self_evolve.sanitization import public_diagnostic_projection, sanitize_text
from aworld.self_evolve.skill_evolution_contract import SkillEvolutionContract
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.targets import SelfEvolveTarget
from aworld.self_evolve.trace_pack import TracePack
from aworld.self_evolve.types import CandidateVariant, EvaluationSummary, GateResult


class GenerationBudgetContext(Protocol):
    def can_fit_workflow(
        self, items: tuple[tuple[BudgetStage, str, int], ...]
    ) -> bool: ...
    def reserve(
        self,
        stage: BudgetStage,
        key: str,
        *,
        units: int = 1,
        backend_proven_zero: bool | None = None,
        request_derived_tokens: int | None = None,
    ) -> BudgetDecision: ...
    def debit(
        self,
        decision: BudgetDecision,
        *,
        tokens: int | None = None,
        wall_seconds: object | None = None,
        actual_source: str,
        **kwargs: object,
    ) -> None: ...


class GenerationAttemptTracker(Protocol):
    def start(
        self,
        *,
        iteration: int,
        slot: int,
        candidate_id: str,
        usage: object | None = None,
    ) -> CandidateAttemptKey: ...
    def emit(
        self,
        key: CandidateAttemptKey,
        stage: CandidateAttemptStage,
        *,
        reason_code: str | None = None,
        **kwargs: object,
    ) -> object: ...
    def terminal(self, key: CandidateAttemptKey) -> bool: ...


class GenerationExecutionDisposition(str, Enum):
    PROCEED = "proceed"
    NEXT_ITERATION = "next_iteration"
    STOP = "stop"


@dataclass(frozen=True)
class GenerationExecutionPolicy:
    max_iterations: int
    max_generated_candidates: int
    max_full_evaluation_candidates: int
    replay_candidate_limit: int
    replay_enabled: bool
    candidate_screening_max_cases: int

    def __post_init__(self) -> None:
        for field_name in (
            "max_iterations",
            "max_generated_candidates",
            "max_full_evaluation_candidates",
            "replay_candidate_limit",
            "candidate_screening_max_cases",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")


@dataclass
class GenerationExecutionState:
    scheduler_state: SchedulerState
    validation_feedback: tuple[EvaluationSummary, ...] = ()
    fresh_evaluation_required: bool = False
    latest_handbook_slice: Mapping[str, object] | None = None
    all_candidates: list[CandidateVariant] = field(default_factory=list)
    candidate_source_dispositions: dict[str, CandidateSourceDisposition] = field(
        default_factory=dict
    )
    optimizer_diagnostics: list[dict[str, object]] = field(default_factory=list)
    optimizer_lineage_paths: list[str] = field(default_factory=list)
    optimizer_lineage_paths_by_candidate: dict[str, str] = field(default_factory=dict)
    scheduler_decisions: list[dict[str, object]] = field(default_factory=list)
    iteration_reports: list[dict[str, object]] = field(default_factory=list)
    iteration_states: list[dict[str, object]] = field(default_factory=list)
    gate_results: list[GateResult] = field(default_factory=list)
    canonical_candidate_id_by_package: dict[str, str] = field(default_factory=dict)
    package_fingerprint_by_candidate_id: dict[str, str] = field(default_factory=dict)
    current_run_candidate_id_by_package: dict[str, str] = field(default_factory=dict)
    current_run_package_fingerprint_by_candidate_id: dict[str, str] = field(
        default_factory=dict
    )
    current_run_candidate_id_by_semantic_package: dict[str, str] = field(
        default_factory=dict
    )
    attempt_key_by_candidate_id: dict[str, CandidateAttemptKey] = field(
        default_factory=dict
    )


@dataclass(frozen=True)
class GenerationExecutionRuntime:
    store: FilesystemSelfEvolveStore
    optimizer: CandidateOptimizer
    generation_controller: CandidateGenerationController
    execution_telemetry: SelfEvolveExecutionTelemetry
    scheduler: StageAwareCandidateScheduler
    budget_context: GenerationBudgetContext
    attempt_tracker: GenerationAttemptTracker
    repair_workflow_budget_items: Callable[
        ..., tuple[tuple[BudgetStage, str, int], ...]
    ]
    progress_callback: Callable[[str, str], Any] | None = None
    skill_evolution_contract: SkillEvolutionContract | None = None
    candidate_replay_backend: object | None = None
    verification_contract_fingerprint: Callable[..., str] | None = None


@dataclass(frozen=True)
class GenerationExecutionRequest:
    iteration_index: int
    iteration_budget: int
    run_id: str
    target: SelfEvolveTarget
    dataset: SelfEvolveDataset
    trace_packs: tuple[TracePack, ...]
    apply_policy: str
    repair_reserved_slot_count: int
    verification_settings: Mapping[str, object]
    prior_feedback: tuple[EvaluationSummary, ...]
    generation_lesson_records: tuple[LessonRecord, ...]
    replay_preflight: ReplayPreflightReport
    target_package_inventory: tuple[str, ...]
    target_package_sources: Mapping[str, Mapping[str, object]]
    rejected_candidate_ids: set[str]
    accepted_candidate_ids: set[str]
    current_run_attempted_candidate_ids: set[str]
    rejected_semantic_lesson_fingerprints: set[object]
    run_state: ExplicitRunStateAccumulator
    policy: GenerationExecutionPolicy
    state: GenerationExecutionState

    def __post_init__(self) -> None:
        if self.iteration_index < 0:
            raise ValueError("iteration_index must be non-negative")
        if self.iteration_budget < 1:
            raise ValueError("iteration_budget must be positive")
        if not self.run_id:
            raise ValueError("run_id must be non-empty")


@dataclass(frozen=True)
class GenerationExecutionResult:
    disposition: GenerationExecutionDisposition
    candidate_population: tuple[CandidateVariant, ...]
    prerequisite_fidelity_gates: Mapping[str, GateResult]
    optimizer_result: OptimizerResult | None
    state: GenerationExecutionState


async def execute_generation_iteration(
    request: GenerationExecutionRequest, runtime: GenerationExecutionRuntime
) -> GenerationExecutionResult:
    iteration_index = request.iteration_index
    iteration_budget = request.iteration_budget
    run_id = request.run_id
    target = request.target
    dataset = request.dataset
    trace_packs = request.trace_packs
    apply_policy = request.apply_policy
    repair_reserved_slot_count = request.repair_reserved_slot_count
    verification_settings = request.verification_settings
    prior_feedback = request.prior_feedback
    generation_lesson_records = request.generation_lesson_records
    replay_preflight = request.replay_preflight
    target_package_inventory = request.target_package_inventory
    target_package_sources = request.target_package_sources
    rejected_candidate_ids = request.rejected_candidate_ids
    accepted_candidate_ids = request.accepted_candidate_ids
    current_run_attempted_candidate_ids = request.current_run_attempted_candidate_ids
    rejected_semantic_lesson_fingerprints = (
        request.rejected_semantic_lesson_fingerprints
    )
    run_state = request.run_state
    policy = request.policy
    state = request.state
    scheduler_state = state.scheduler_state
    validation_feedback = state.validation_feedback
    fresh_evaluation_required = state.fresh_evaluation_required
    latest_handbook_slice = state.latest_handbook_slice
    all_candidates = state.all_candidates
    candidate_source_dispositions = state.candidate_source_dispositions
    optimizer_diagnostics = state.optimizer_diagnostics
    optimizer_lineage_paths = state.optimizer_lineage_paths
    optimizer_lineage_paths_by_candidate = state.optimizer_lineage_paths_by_candidate
    scheduler_decisions = state.scheduler_decisions
    iteration_reports = state.iteration_reports
    iteration_states = state.iteration_states
    gate_results = state.gate_results
    canonical_candidate_id_by_package = state.canonical_candidate_id_by_package
    package_fingerprint_by_candidate_id = state.package_fingerprint_by_candidate_id
    current_run_candidate_id_by_package = state.current_run_candidate_id_by_package
    current_run_package_fingerprint_by_candidate_id = (
        state.current_run_package_fingerprint_by_candidate_id
    )
    current_run_candidate_id_by_semantic_package = (
        state.current_run_candidate_id_by_semantic_package
    )
    attempt_key_by_candidate_id = state.attempt_key_by_candidate_id
    scheduler = runtime.scheduler
    budget_context = runtime.budget_context
    attempt_tracker = runtime.attempt_tracker
    repair_workflow_budget_items = runtime.repair_workflow_budget_items
    candidate_population: tuple[CandidateVariant, ...] = ()
    prerequisite_fidelity_gates: dict[str, GateResult] = {}
    optimizer_result: OptimizerResult | None = None

    def _result(
        disposition: GenerationExecutionDisposition,
    ) -> GenerationExecutionResult:
        state.scheduler_state = scheduler_state
        state.validation_feedback = validation_feedback
        state.fresh_evaluation_required = fresh_evaluation_required
        state.latest_handbook_slice = latest_handbook_slice
        return GenerationExecutionResult(
            disposition,
            tuple(candidate_population),
            dict(prerequisite_fidelity_gates),
            optimizer_result,
            state,
        )

    if run_state.baseline_preflight_blocked:
        return _result(GenerationExecutionDisposition.STOP)
    if iteration_index >= policy.max_iterations:
        repair_family = _next_progress_repair_extension_family(
            validation_feedback,
            consumed_families=run_state.generation.progress_repair_families,
        )
        if repair_family is None:
            return _result(GenerationExecutionDisposition.STOP)
        run_state.generation.progress_repair_families.add(repair_family)
    cumulative_feedback = (*prior_feedback, *validation_feedback)
    repair_frontiers = _typed_repair_frontiers(cumulative_feedback)
    focused_available = budget_context.can_fit_workflow(
        repair_workflow_budget_items(iteration=iteration_index + 1, candidate_count=1)
    )
    diverse_available = budget_context.can_fit_workflow(
        repair_workflow_budget_items(iteration=iteration_index + 1, candidate_count=2)
    )
    scheduler_state_before_decision = scheduler_state
    scheduler_decision = scheduler.schedule(
        state=scheduler_state,
        frontiers=repair_frontiers,
        focused_budget_available=focused_available,
        diverse_budget_available=diverse_available,
        untyped_feedback_present=bool(cumulative_feedback) and (not repair_frontiers),
    )
    stored_admission_reason = (
        _optimizer_stored_candidate_admission_reason(runtime.optimizer)
        if iteration_index == 0
        else None
    )
    if stored_admission_reason is not None:
        scheduler_decision = SchedulerDecision(
            reason_code=stored_admission_reason,
            slots=(
                ScheduledCandidateSlot(
                    slot=0, role=ScheduledSlotRole.BOUNDED_EXPLORATION
                ),
            ),
            stop=False,
            state=scheduler_state,
        )
    scheduler_state = scheduler_decision.state
    requested_generation_slot_count = len(scheduler_decision.slots)
    scheduled_slots = scheduler_decision.slots
    if (
        len(scheduled_slots) > 1
        and scheduled_slots[0].role is ScheduledSlotRole.FOCUSED_REPAIR
        and (
            scheduled_slots[0].semantic_key
            not in scheduler_state_before_decision.frontier_progress
        )
        and _feedback_has_candidate_repair_conformance(cumulative_feedback)
    ):
        scheduled_slots = scheduled_slots[:1]
        run_state.generation.serialized_new_contract_repair_count += 1
        scheduler_decision = replace(
            scheduler_decision,
            reason_code="focused_repair_serialized_new_contract",
            slots=tuple(scheduled_slots),
        )
    if _is_verified_apply_policy(apply_policy) and scheduled_slots:
        effective_generation_limit = policy.max_generated_candidates
        if not repair_frontiers:
            effective_generation_limit = max(
                1, policy.max_generated_candidates - repair_reserved_slot_count
            )
        remaining_generation_slots = max(
            0,
            effective_generation_limit
            - run_state.generation.generated_candidate_slot_count,
        )
        remaining_authoritative_slots = max(
            0,
            policy.max_full_evaluation_candidates
            - run_state.authoritative_candidate_count,
        )
        if remaining_generation_slots == 0:
            run_state.generation.exhaust_generation_limit(
                max_generated_candidates=policy.max_generated_candidates
            )
            _emit_progress(
                runtime.progress_callback,
                "candidate_generation",
                "Stopped candidate generation: "
                + (
                    "downstream repair capacity remains reserved "
                    if run_state.generation.repair_capacity_reserved
                    else "generated candidate slot limit reached "
                )
                + f"({run_state.generation.generated_candidate_slot_count}/{policy.max_generated_candidates}); repair horizon {iteration_index + 1}/{iteration_budget} was not a required iteration count",
            )
            scheduled_slots = ()
        elif remaining_authoritative_slots == 0:
            run_state.generation.verification_frontier_exhausted = True
            scheduled_slots = ()
        else:
            screening_can_rank = bool(
                policy.replay_enabled
                and runtime.candidate_replay_backend is not None
                and (
                    _candidate_screening_dataset(
                        dataset,
                        capability_requirements=replay_preflight.requirements,
                        max_cases=policy.candidate_screening_max_cases,
                    )
                    is not None
                )
            )
            useful_oversubscription = 1 if screening_can_rank else 0
            admitted_slot_count = min(
                len(scheduled_slots),
                remaining_generation_slots,
                remaining_authoritative_slots + useful_oversubscription,
            )
            scheduled_slots = scheduled_slots[:admitted_slot_count]
    scheduler_decision = replace(scheduler_decision, slots=tuple(scheduled_slots))
    scheduler_decisions.append(
        {
            "iteration": iteration_index + 1,
            **scheduler_decision.to_dict(),
            "requested_slot_count": requested_generation_slot_count,
            "admitted_slot_count": len(scheduled_slots),
        }
    )
    if scheduler_decision.stop or not scheduler_decision.slots:
        return _result(GenerationExecutionDisposition.STOP)
    generation_slot_count = len(scheduler_decision.slots)
    if stored_admission_reason is not None:
        _emit_progress(
            runtime.progress_callback,
            "measurement_resume",
            "Restoring the immutable measurement-pending candidate; mutation generation and candidate slot charging are skipped",
        )
    else:
        first_generation_attempt_slot = run_state.generation.begin_generation_slots(
            generation_slot_count
        )
        last_generation_attempt_slot = (
            run_state.generation.candidate_generation_attempt_slot_count
        )
        _emit_progress(
            runtime.progress_callback,
            "candidate_generation",
            f"Generating candidate batch {iteration_index + 1}; candidate attempt slots {first_generation_attempt_slot}-{last_generation_attempt_slot}; effective candidate slots {run_state.generation.generated_candidate_slot_count}/{policy.max_generated_candidates}; repair horizon {iteration_index + 1}/{iteration_budget}"
            if _is_verified_apply_policy(apply_policy)
            else f"Generating candidate iteration {iteration_index + 1}/{iteration_budget}",
        )
    iteration_lesson_records = generation_lesson_records
    if validation_feedback:
        iteration_lesson_records = extract_lesson_records(
            (*prior_feedback, *validation_feedback),
            target_scope={
                "target_type": target.identity.target_type,
                "target_id": target.identity.target_id,
            },
            trace_packs=trace_packs,
        )
    handbook_signals = [lesson.lesson_type for lesson in iteration_lesson_records]
    for summary in validation_feedback:
        failed_gate_names = summary.metrics.get("failed_gates", ())
        if isinstance(failed_gate_names, str):
            handbook_signals.append(failed_gate_names)
        elif isinstance(failed_gate_names, (list, tuple)):
            handbook_signals.extend(
                (item for item in failed_gate_names if isinstance(item, str))
            )
    try:
        handbook_slice = load_handbook_slice_for_target(
            runtime.store.workspace_root,
            target_path=target.identity.path,
            behavior_signals=tuple(handbook_signals),
        )
    except Exception as exc:
        gate_results.append(
            GateResult(
                gate_name="handbook_locator_integrity",
                passed=False,
                reason="self-evolve handbook index could not be refreshed",
                details={
                    "failure_class": "infrastructure",
                    "failure_owner": FailureOwner.FRAMEWORK.value,
                    "failure_scope": FailureScope.SHARED_RUN.value,
                    "repairable": False,
                    "code": "handbook_index_failed",
                    "type": type(exc).__name__,
                    "reason": sanitize_text(str(exc), max_chars=240),
                },
            )
        )
        return _result(GenerationExecutionDisposition.STOP)
    if handbook_slice is not None:
        handbook_payload = handbook_slice.to_prompt_dict()
        latest_handbook_slice = handbook_payload
        runtime.store.write_handbook_slice(
            run_id, iteration_index + 1, handbook_payload
        )
        if not handbook_slice.mutation_allowed:
            gate_results.append(
                GateResult(
                    gate_name="handbook_locator_integrity",
                    passed=False,
                    reason="self-evolve handbook froze unresolved target locators",
                    details={
                        "failure_class": "infrastructure",
                        "failure_owner": FailureOwner.FRAMEWORK.value,
                        "failure_scope": FailureScope.SHARED_RUN.value,
                        "repairable": False,
                        "code": "handbook_locator_frozen",
                        "snapshot_fingerprint": handbook_slice.snapshot_fingerprint,
                        "frozen_locator_ids": list(handbook_slice.frozen_locator_ids),
                    },
                )
            )
            return _result(GenerationExecutionDisposition.STOP)
    else:
        handbook_payload = None
    optimizer_request = OptimizerRequest.from_dataset(
        target=target.identity,
        current_content=target.load_current_content(),
        target_fingerprint=target.fingerprint_current_content(),
        trace_packs=trace_packs,
        validation_feedback=validation_feedback,
        prior_feedback=prior_feedback,
        lesson_records=iteration_lesson_records,
        dataset=dataset,
        max_candidates=generation_slot_count,
        replay_requirements=replay_preflight.requirements,
        target_package_inventory=target_package_inventory,
        target_package_sources=target_package_sources,
        handbook_slice=handbook_payload,
        consumed_mutation_families=tuple(
            sorted(
                {
                    family
                    for slot in scheduler_decision.slots
                    if slot.semantic_key is not None
                    for family in scheduler_state.frontier_mutation_families.get(
                        slot.semantic_key, ()
                    )
                }
            )
        ),
        active_repair_frontier_keys=tuple(
            (slot.semantic_key for slot in scheduler_decision.slots)
        ),
        skill_evolution_contract=runtime.skill_evolution_contract.prompt_projection()
        if runtime.skill_evolution_contract is not None
        else None,
    )
    optimizer_request = replace(
        optimizer_request,
        evolution_context=compile_evolution_context(optimizer_request),
    )
    request_derived_generation_tokens = (
        runtime.generation_controller.request_derived_tokens(
            runtime.optimizer, optimizer_request
        )
        if stored_admission_reason is None
        else None
    )
    generation_budget = budget_context.reserve(
        BudgetStage.CANDIDATE_GENERATION,
        f"iteration-{iteration_index + 1}-generation",
        units=generation_slot_count,
        backend_proven_zero=True if stored_admission_reason is not None else None,
        request_derived_tokens=request_derived_generation_tokens,
    )
    if not generation_budget.allowed:
        for slot in scheduler_decision.slots:
            placeholder = _candidate_attempt_placeholder(iteration_index, slot.slot)
            key = attempt_tracker.start(
                iteration=iteration_index, slot=slot.slot, candidate_id=placeholder
            )
            attempt_tracker.emit(
                key,
                CandidateAttemptStage.NOT_RUN,
                reason_code="generation_budget_denied",
            )
        return _result(GenerationExecutionDisposition.STOP)
    try:
        optimizer_result = await runtime.optimizer.propose(optimizer_request)
    except Exception as exc:
        budget_context.debit(
            generation_budget,
            actual_source="reserved_fallback_candidate_generation_exception",
        )
        for slot in scheduler_decision.slots:
            placeholder = _candidate_attempt_placeholder(iteration_index, slot.slot)
            key = attempt_tracker.start(
                iteration=iteration_index, slot=slot.slot, candidate_id=placeholder
            )
            attempt_tracker.emit(
                key,
                CandidateAttemptStage.BLOCKED,
                reason_code="candidate_generation_infrastructure_failed",
            )
        optimizer_diagnostics.append(
            {
                "iteration": iteration_index + 1,
                "candidate_ids": [],
                "diagnostics": {
                    "candidate_generation_failure": {
                        "code": "candidate_generation_infrastructure_error",
                        "error_type": type(exc).__name__,
                        "stage": "optimizer",
                    }
                },
            }
        )
        run_state.infrastructure_blocked = True
        return _result(GenerationExecutionDisposition.STOP)
    if optimizer_result.source_disposition.kind is CandidateSourceKind.GENERATED:
        optimizer_result = replace(
            optimizer_result,
            candidates=tuple(
                (
                    replace(
                        candidate,
                        target_fingerprint=optimizer_request.target_fingerprint,
                    )
                    for candidate in optimizer_result.candidates
                )
            ),
        )
    lineage_kwargs: dict[str, object] = {}
    if runtime.verification_contract_fingerprint is not None:
        lineage_kwargs["verification_contract_fingerprint"] = (
            runtime.verification_contract_fingerprint
        )
    optimizer_result = _with_versioned_semantic_lineage(
        optimizer_result,
        target_fingerprint=optimizer_request.target_fingerprint,
        replay_preflight_fingerprint=replay_preflight.fingerprint,
        apply_policy=apply_policy,
        verification_settings=verification_settings,
        **lineage_kwargs,
    )
    generation_outcomes = tuple(optimizer_result.generation_outcomes)
    if stored_admission_reason is None:
        run_state.generation.raw_generation_attempt_count += (
            sum(
                (
                    outcome.kind
                    is not CandidateGenerationOutcomeKind.INFRASTRUCTURE_FAILED
                    for outcome in generation_outcomes
                )
            )
            if generation_outcomes
            else len(optimizer_result.candidates)
        )
    population_execution = optimizer_result.diagnostics.get(
        "candidate_population_execution"
    )
    if isinstance(population_execution, Mapping):
        runtime.execution_telemetry.record("candidate_generation", population_execution)
    generation_tokens, generation_wall, generation_source = (
        _candidate_generation_actual_usage(population_execution)
    )
    budget_context.debit(
        generation_budget,
        tokens=generation_tokens,
        wall_seconds=generation_wall,
        actual_source=generation_source,
    )
    source_disposition = optimizer_result.source_disposition
    bypass_historical_deduplication = source_disposition.bypass_historical_deduplication
    fresh_evaluation_required = (
        fresh_evaluation_required or source_disposition.requires_fresh_evaluation
    )
    filtered_known_duplicates = (
        0
        if bypass_historical_deduplication
        else _known_duplicate_candidate_count(
            optimizer_result.candidates,
            rejected_candidate_ids=rejected_candidate_ids,
            accepted_candidate_ids=accepted_candidate_ids,
        )
    )
    current_lineage_fingerprints = _lineage_semantic_lesson_fingerprints(
        optimizer_result.lineage
    )
    filtered_semantic_lesson_duplicates = (
        0
        if bypass_historical_deduplication
        else _semantic_lesson_duplicate_count(
            optimizer_result.candidates,
            lineage_fingerprints=current_lineage_fingerprints,
            rejected_semantic_lesson_fingerprints=rejected_semantic_lesson_fingerprints,
        )
    )
    candidate_protocol_overflow_count = max(
        0, len(optimizer_result.candidates) - generation_slot_count
    )
    iteration_optimizer_diagnostics = {
        **dict(optimizer_result.diagnostics),
        "candidate_generation_outcomes": [
            outcome.to_dict() for outcome in generation_outcomes
        ],
        "filtered_known_duplicate_candidates": filtered_known_duplicates,
        "filtered_semantic_lesson_duplicate_candidates": filtered_semantic_lesson_duplicates,
    }
    generated = (
        () if candidate_protocol_overflow_count else tuple(optimizer_result.candidates)
    )
    prerequisite_fidelity_gates: dict[str, GateResult] = {}
    canonicalized_prerequisite_file_count = 0
    canonicalized_generated: list[CandidateVariant] = []
    for candidate in generated:
        canonical_candidate, fidelity_gate, canonicalized_count = (
            _canonicalize_verified_prerequisite_files(candidate, cumulative_feedback)
        )
        canonicalized_generated.append(canonical_candidate)
        canonicalized_prerequisite_file_count += canonicalized_count
        if fidelity_gate is not None:
            prerequisite_fidelity_gates[canonical_candidate.candidate_id] = (
                fidelity_gate
            )
    generated = tuple(canonicalized_generated)
    if canonicalized_prerequisite_file_count:
        iteration_optimizer_diagnostics[
            "canonicalized_verified_prerequisite_file_count"
        ] = canonicalized_prerequisite_file_count
    if candidate_protocol_overflow_count:
        iteration_optimizer_diagnostics["candidate_protocol_overflow_count"] = (
            candidate_protocol_overflow_count
        )
        iteration_optimizer_diagnostics["candidate_protocol_error"] = {
            "code": "candidate_population_exceeds_scheduled_slots",
            "scheduled_slot_count": generation_slot_count,
            "returned_candidate_count": len(optimizer_result.candidates),
        }
    optimizer_diagnostics.append(
        {
            "iteration": iteration_index + 1,
            "candidate_ids": [
                candidate.candidate_id for candidate in optimizer_result.candidates
            ],
            "diagnostics": public_diagnostic_projection(
                iteration_optimizer_diagnostics
            ),
        }
    )
    scheduler_state = _scheduler_state_with_mutation_families(
        scheduler_state,
        decision=scheduler_decision,
        optimizer_diagnostics=optimizer_result.diagnostics,
    )
    unique_generated: list[CandidateVariant] = []
    unique_candidate_ids: set[str] = set()
    generation_duplicate_feedback: list[EvaluationSummary] = []
    generation_usage = _budget_usage_for_attempt_event(
        generation_budget, tokens=generation_tokens, wall_seconds=generation_wall
    )
    invalid_slots_remaining = _non_negative_int(
        optimizer_result.diagnostics.get("candidate_protocol_invalid_count")
    )
    if candidate_protocol_overflow_count:
        invalid_slots_remaining = generation_slot_count
    outcomes_by_slot = {
        outcome.candidate_index: outcome
        for outcome in generation_outcomes
        if outcome.candidate_index < generation_slot_count
    }
    candidates_by_id = {candidate.candidate_id: candidate for candidate in generated}
    legacy_candidates = iter(
        (
            candidate
            for candidate in generated
            if candidate.candidate_id
            not in {
                outcome.candidate_id
                for outcome in generation_outcomes
                if outcome.candidate_id is not None
            }
        )
    )
    for slot_index in range(generation_slot_count):
        generation_outcome = outcomes_by_slot.get(slot_index)
        generated_candidate = (
            candidates_by_id.get(generation_outcome.candidate_id)
            if generation_outcome is not None
            and generation_outcome.kind is CandidateGenerationOutcomeKind.ADMITTED
            and (generation_outcome.candidate_id is not None)
            else None
        )
        if generated_candidate is None and generation_outcome is None:
            generated_candidate = next(legacy_candidates, None)
        if generated_candidate is None:
            placeholder = (
                generation_outcome.candidate_id
                if generation_outcome is not None
                and generation_outcome.candidate_id is not None
                else _candidate_attempt_placeholder(iteration_index, slot_index)
            )
            key = attempt_tracker.start(
                iteration=iteration_index,
                slot=slot_index,
                candidate_id=placeholder,
                usage=generation_usage if slot_index == 0 else None,
            )
            if (
                generation_outcome is not None
                and generation_outcome.kind
                is CandidateGenerationOutcomeKind.POLICY_FILTERED
            ):
                attempt_tracker.emit(key, CandidateAttemptStage.POLICY_FILTERED)
                attempt_tracker.emit(
                    key,
                    CandidateAttemptStage.NOT_RUN,
                    reason_code="candidate_policy_filtered",
                )
                continue
            if candidate_protocol_overflow_count:
                reason_code = "candidate_population_exceeds_scheduled_slots"
            elif generation_outcome is not None:
                reason_code = generation_outcome.kind.value
            elif invalid_slots_remaining:
                invalid_slots_remaining -= 1
                reason_code = "candidate_protocol_invalid"
            elif isinstance(
                optimizer_result.diagnostics.get("candidate_generation_failure"),
                Mapping,
            ):
                reason_code = "candidate_generation_infrastructure_failed"
            else:
                reason_code = "candidate_generation_no_output"
            attempt_tracker.emit(
                key,
                CandidateAttemptStage.BLOCKED
                if reason_code.endswith("infrastructure_failed")
                else CandidateAttemptStage.NOT_RUN,
                reason_code=reason_code,
            )
            continue
        package_fingerprint = candidate_package_fingerprint(generated_candidate)
        semantic_package_fingerprint = candidate_semantic_package_fingerprint(
            generated_candidate
        )
        canonical_id = (
            current_run_candidate_id_by_package.get(package_fingerprint)
            if bypass_historical_deduplication
            else canonical_candidate_id_by_package.get(package_fingerprint)
        )
        semantic_duplicate_id = current_run_candidate_id_by_semantic_package.get(
            semantic_package_fingerprint
        )
        prior_candidate_duplicate = not bypass_historical_deduplication and (
            generated_candidate.candidate_id in rejected_candidate_ids
            or generated_candidate.candidate_id in accepted_candidate_ids
        )
        semantic_lesson_duplicate = (
            not bypass_historical_deduplication
            and _is_semantic_lesson_duplicate(
                generated_candidate.candidate_id,
                lineage_fingerprints=current_lineage_fingerprints,
                rejected_semantic_lesson_fingerprints=rejected_semantic_lesson_fingerprints,
            )
        )
        candidate_id_collision = (
            generated_candidate.candidate_id
            in (
                current_run_package_fingerprint_by_candidate_id
                if bypass_historical_deduplication
                else package_fingerprint_by_candidate_id
            )
            and (
                current_run_package_fingerprint_by_candidate_id
                if bypass_historical_deduplication
                else package_fingerprint_by_candidate_id
            )[generated_candidate.candidate_id]
            != package_fingerprint
        )
        lifecycle_candidate_id = (
            canonical_id
            if canonical_id is not None
            else semantic_duplicate_id
            if semantic_duplicate_id is not None
            else generated_candidate.candidate_id
        )
        key = attempt_tracker.start(
            iteration=iteration_index,
            slot=slot_index,
            candidate_id=lifecycle_candidate_id,
            usage=generation_usage if slot_index == 0 else None,
        )
        if (
            canonical_id is not None
            or semantic_duplicate_id is not None
            or candidate_id_collision
            or prior_candidate_duplicate
            or semantic_lesson_duplicate
        ):
            attempt_tracker.emit(key, CandidateAttemptStage.DUPLICATE_FILTERED)
            attempt_tracker.emit(
                key,
                CandidateAttemptStage.NOT_RUN,
                reason_code="candidate_id_collision"
                if candidate_id_collision
                else "duplicate_prior_candidate"
                if prior_candidate_duplicate
                else "duplicate_semantic_lesson"
                if semantic_lesson_duplicate
                else "duplicate_candidate_semantics"
                if semantic_duplicate_id is not None
                else "duplicate_candidate_package",
            )
            if prior_candidate_duplicate:
                duplicate_gate_name = (
                    "duplicate_accepted_candidate"
                    if generated_candidate.candidate_id in accepted_candidate_ids
                    else "duplicate_rejected_candidate"
                )
                duplicate_feedback = EvaluationSummary(
                    variant_id=generated_candidate.candidate_id,
                    dataset_split="validation",
                    metrics={
                        "failed_gates": [duplicate_gate_name],
                        "candidate_status": "rejected",
                        "failure_class": "candidate",
                        "repairable": True,
                    },
                )
                duplicate_gate = GateResult(
                    gate_name=duplicate_gate_name,
                    passed=False,
                    reason="candidate repeats a prior terminal candidate",
                    details={
                        "candidate_id": generated_candidate.candidate_id,
                        "failure_class": "candidate",
                        "code": "duplicate_prior_candidate",
                    },
                )
                generation_duplicate_feedback.append(duplicate_feedback)
                iteration_states.append(
                    _iteration_state(
                        candidate=generated_candidate,
                        baseline_summary=None,
                        candidate_summary=None,
                        held_out_summary=None,
                        replay_result=None,
                        replay_dataset=None,
                        gate_results=(duplicate_gate,),
                        feedback=(duplicate_feedback,),
                        status="rejected",
                    )
                )
            elif semantic_lesson_duplicate:
                semantic_fingerprint = current_lineage_fingerprints.get(
                    generated_candidate.candidate_id
                )
                if semantic_fingerprint is not None:
                    run_state.generation.semantic_lesson_duplicate_attempt_count += 1
                    generation_duplicate_feedback.append(
                        _semantic_lesson_duplicate_feedback(
                            generated_candidate, fingerprint=semantic_fingerprint
                        )
                    )
            continue
        canonical_candidate_id_by_package[package_fingerprint] = (
            generated_candidate.candidate_id
        )
        package_fingerprint_by_candidate_id[generated_candidate.candidate_id] = (
            package_fingerprint
        )
        current_run_candidate_id_by_package[package_fingerprint] = (
            generated_candidate.candidate_id
        )
        current_run_candidate_id_by_semantic_package[semantic_package_fingerprint] = (
            generated_candidate.candidate_id
        )
        current_run_package_fingerprint_by_candidate_id[
            generated_candidate.candidate_id
        ] = package_fingerprint
        attempt_tracker.emit(key, CandidateAttemptStage.UNIQUE)
        attempt_key_by_candidate_id[generated_candidate.candidate_id] = key
        unique_generated.append(generated_candidate)
        unique_candidate_ids.add(generated_candidate.candidate_id)
        run_state.generation.record_effective_candidate(
            generated_candidate.candidate_id,
            consumes_slot=stored_admission_reason is None,
        )
        all_candidates.append(generated_candidate)
        candidate_source_dispositions[generated_candidate.candidate_id] = (
            source_disposition
        )
        target.preserve_proposal(runtime.store, run_id, generated_candidate)
    for lineage in optimizer_result.lineage:
        if (
            lineage.candidate_id not in unique_candidate_ids
            or lineage.candidate_id in optimizer_lineage_paths_by_candidate
        ):
            continue
        lineage_path = runtime.store.write_optimizer_lineage(run_id, lineage)
        optimizer_lineage_paths.append(str(lineage_path))
        optimizer_lineage_paths_by_candidate[lineage.candidate_id] = str(lineage_path)
    if generation_duplicate_feedback:
        validation_feedback = _merge_validation_feedback(
            validation_feedback, tuple(generation_duplicate_feedback)
        )
        iteration_reports.extend(
            (
                {
                    "iteration": iteration_index + 1,
                    "candidate_id": item.variant_id,
                    "status": "rejected",
                    "failed_gates": list(item.metrics["failed_gates"]),
                }
                for item in generation_duplicate_feedback
            )
        )
    ranked_candidate_population = _rank_candidate_population(
        tuple(
            (
                candidate
                for candidate in unique_generated
                if bypass_historical_deduplication
                or (
                    candidate.candidate_id not in rejected_candidate_ids
                    and candidate.candidate_id not in accepted_candidate_ids
                    and (
                        not _is_semantic_lesson_duplicate(
                            candidate.candidate_id,
                            lineage_fingerprints=current_lineage_fingerprints,
                            rejected_semantic_lesson_fingerprints=rejected_semantic_lesson_fingerprints,
                        )
                    )
                )
            )
        ),
        optimizer_diagnostics=optimizer_result.diagnostics,
        current_content=target.load_current_content(),
    )
    candidate_population = ranked_candidate_population[
        : max(1, policy.replay_candidate_limit)
    ]
    ranked_below_replay_frontier = ranked_candidate_population[
        max(1, policy.replay_candidate_limit) :
    ]
    for deferred_candidate in ranked_below_replay_frontier:
        deferred_key = attempt_key_by_candidate_id.get(deferred_candidate.candidate_id)
        if deferred_key is not None and (not attempt_tracker.terminal(deferred_key)):
            attempt_tracker.emit(
                deferred_key,
                CandidateAttemptStage.NOT_RUN,
                reason_code="ranked_below_replay_frontier",
            )
        iteration_reports.append(
            {
                "iteration": iteration_index + 1,
                "candidate_id": deferred_candidate.candidate_id,
                "status": "not_run",
                "failed_gates": [],
                "lifecycle_stage": "not_run",
                "reason_code": "ranked_below_replay_frontier",
            }
        )
    _emit_progress(
        runtime.progress_callback,
        "population_generation",
        f"Prepared candidate population ({len(candidate_population)} replay candidate(s), {len(optimizer_result.candidates)} generated)",
    )
    if not candidate_population:
        policy_filter_outcomes = tuple(
            (
                outcome
                for outcome in generation_outcomes
                if outcome.kind is CandidateGenerationOutcomeKind.POLICY_FILTERED
            )
        )
        if policy_filter_outcomes:
            policy_events = tuple(
                (
                    _candidate_policy_filter_event(outcome)
                    for outcome in policy_filter_outcomes
                )
            )
            validation_feedback = _merge_validation_feedback(
                validation_feedback,
                (
                    EvaluationSummary(
                        variant_id=f"candidate-policy-filter-{iteration_index + 1}",
                        dataset_split="validation",
                        metrics={
                            "failed_gates": ["candidate_generation_policy"],
                            "candidate_status": "rejected",
                            "failure_class": "candidate",
                            "repairable": True,
                            "candidate_policy_filter_count": len(
                                policy_filter_outcomes
                            ),
                            "candidate_validation_diagnostics": [
                                {
                                    "code": "candidate_generation_policy_filtered",
                                    "stage": "candidate_generation",
                                    "failure_class": "candidate",
                                    "repairable": True,
                                    "policy_id": outcome.policy_id,
                                    "enforcement": outcome.enforcement,
                                    "reason_codes": list(outcome.reason_codes),
                                    "constraint_ids": list(outcome.constraint_ids),
                                    "active_frontier_key": outcome.active_frontier_key,
                                    "affected_case_ids": list(
                                        outcome.affected_case_ids
                                    ),
                                }
                                for outcome in policy_filter_outcomes
                            ],
                            "causal_failure_events": list(policy_events),
                        },
                    ),
                ),
            )
            iteration_reports.append(
                {
                    "iteration": iteration_index + 1,
                    "candidate_id": None,
                    "status": "policy_filtered",
                    "failed_gates": ["candidate_generation_policy"],
                    "filtered_candidate_count": len(policy_filter_outcomes),
                }
            )
            signature = _candidate_policy_filter_signature(policy_filter_outcomes)
            fully_filtered = len(policy_filter_outcomes) == generation_slot_count
            if run_state.generation.record_policy_filter_stall(
                signature=signature,
                outcomes=policy_filter_outcomes,
                fully_filtered=fully_filtered,
                max_consecutive_stalls=_MAX_CONSECUTIVE_POLICY_FILTER_STALLS,
            ):
                return _result(GenerationExecutionDisposition.STOP)
            if fully_filtered:
                return _result(GenerationExecutionDisposition.NEXT_ITERATION)
        generation_failure = optimizer_result.diagnostics.get(
            "candidate_generation_failure"
        )
        if isinstance(generation_failure, Mapping):
            iteration_reports.append(
                {
                    "iteration": iteration_index + 1,
                    "candidate_id": None,
                    "status": "infrastructure_failed",
                    "failed_gates": ["candidate_generation"],
                }
            )
            if run_state.generation.claim_infrastructure_retry(
                retryable=_retryable_candidate_generation_failure(generation_failure),
                max_retries=2,
            ):
                return _result(GenerationExecutionDisposition.NEXT_ITERATION)
            return _result(GenerationExecutionDisposition.STOP)
        protocol_invalid_count = (
            _non_negative_int(
                optimizer_result.diagnostics.get("candidate_protocol_invalid_count")
            )
            + candidate_protocol_overflow_count
        )
        materialization_failures = _candidate_materialization_failures(
            optimizer_result.diagnostics
        )
        materialization_invalid_count = len(materialization_failures)
        if protocol_invalid_count or materialization_invalid_count:
            protocol_outcomes = tuple(
                (
                    outcome
                    for outcome in generation_outcomes
                    if outcome.kind is CandidateGenerationOutcomeKind.PROTOCOL_INVALID
                )
            )
            unattributed_protocol_failures = max(
                0, protocol_invalid_count - len(protocol_outcomes)
            )
            generation_failure_repairable = bool(
                any(
                    (
                        failure.get("repairable") is not False
                        for failure in materialization_failures
                    )
                )
                or any((outcome.repairable for outcome in protocol_outcomes))
                or unattributed_protocol_failures
            )
            causal_failure_events = _candidate_materialization_failure_events(
                materialization_failures
            )
            failed_gate = (
                "candidate_materialization"
                if materialization_invalid_count
                else "candidate_protocol"
            )
            validation_feedback = _merge_validation_feedback(
                validation_feedback,
                (
                    EvaluationSummary(
                        variant_id=f"candidate-generation-{iteration_index + 1}",
                        dataset_split="validation",
                        metrics={
                            "failed_gates": [failed_gate],
                            "candidate_status": "rejected",
                            "failure_class": "candidate",
                            "repairable": generation_failure_repairable,
                            "candidate_protocol_invalid_count": protocol_invalid_count,
                            "candidate_protocol_overflow_count": candidate_protocol_overflow_count,
                            "candidate_materialization_invalid_count": materialization_invalid_count,
                            "candidate_validation_diagnostics": list(
                                materialization_failures
                            ),
                            "causal_failure_events": list(causal_failure_events),
                        },
                    ),
                ),
            )
            iteration_reports.append(
                {
                    "iteration": iteration_index + 1,
                    "candidate_id": None,
                    "status": "materialization_invalid"
                    if materialization_invalid_count
                    else "protocol_invalid",
                    "failed_gates": [failed_gate],
                }
            )
            if not generation_failure_repairable:
                run_state.generation.protocol_frontier_exhausted = True
                return _result(GenerationExecutionDisposition.STOP)
            full_population_failed = materialization_invalid_count >= max(
                1, generation_slot_count
            )
            materialization_signature = (
                _candidate_materialization_stall_signature(materialization_failures)
                if full_population_failed
                else None
            )
            if run_state.generation.record_materialization_stall(
                signature=materialization_signature,
                full_population_failed=full_population_failed,
                max_consecutive_stalls=_MAX_CONSECUTIVE_MATERIALIZATION_STALLS,
            ):
                return _result(GenerationExecutionDisposition.STOP)
            return _result(GenerationExecutionDisposition.NEXT_ITERATION)
        if generation_duplicate_feedback:
            return _result(GenerationExecutionDisposition.NEXT_ITERATION)
        skipped_feedback: list[EvaluationSummary] = []
        skipped_duplicates = [
            candidate
            for candidate in unique_generated
            if candidate.candidate_id in rejected_candidate_ids
            or candidate.candidate_id in accepted_candidate_ids
        ]
        for candidate_index, skipped_candidate in enumerate(
            skipped_duplicates[: max(1, policy.replay_candidate_limit)]
        ):
            skipped_key = attempt_key_by_candidate_id.get(
                skipped_candidate.candidate_id
            )
            if skipped_key is not None:
                attempt_tracker.emit(
                    skipped_key,
                    CandidateAttemptStage.REJECTED,
                    reason_code="duplicate_prior_candidate",
                )
            duplicate_gates: list[GateResult] = []
            accepted_gate = _duplicate_accepted_candidate_gate(
                skipped_candidate,
                accepted_candidate_ids=accepted_candidate_ids,
                apply_policy=apply_policy,
            )
            if accepted_gate is not None:
                duplicate_gates.append(accepted_gate)
            rejected_gate = _duplicate_rejected_candidate_gate(
                skipped_candidate,
                rejected_candidate_ids=rejected_candidate_ids,
                apply_policy=apply_policy,
            )
            if rejected_gate is not None:
                duplicate_gates.append(rejected_gate)
            failed_duplicate_gates = [
                gate for gate in duplicate_gates if not gate.passed
            ]
            duplicate_feedback = EvaluationSummary(
                variant_id=skipped_candidate.candidate_id,
                metrics={
                    "failed_gates": [gate.gate_name for gate in failed_duplicate_gates],
                    "candidate_status": "rejected",
                },
                dataset_split="validation",
            )
            iteration_reports.append(
                _iteration_report_item(
                    iteration_number=iteration_index + 1,
                    candidate_number=candidate_index + 1,
                    candidate_count=len(skipped_duplicates),
                    candidate=skipped_candidate,
                    status="rejected",
                    baseline_summary=None,
                    candidate_summary=None,
                    held_out_summary=None,
                    failed_gates=failed_duplicate_gates,
                )
            )
            iteration_states.append(
                _iteration_state(
                    candidate=skipped_candidate,
                    baseline_summary=None,
                    candidate_summary=None,
                    held_out_summary=None,
                    replay_result=None,
                    replay_dataset=None,
                    gate_results=duplicate_gates,
                    feedback=(duplicate_feedback,),
                    status="rejected",
                )
            )
            skipped_feedback.append(duplicate_feedback)
        if skipped_feedback:
            validation_feedback = _merge_validation_feedback(
                validation_feedback, tuple(skipped_feedback)
            )
            if run_state.generation.record_duplicate_population(
                all_candidates_previously_attempted=all(
                    (
                        candidate.candidate_id in current_run_attempted_candidate_ids
                        for candidate in skipped_duplicates
                    )
                ),
                max_consecutive_stalls=_MAX_CONSECUTIVE_DUPLICATE_POPULATION_STALLS,
            ):
                return _result(GenerationExecutionDisposition.STOP)
            return _result(GenerationExecutionDisposition.NEXT_ITERATION)
        iteration_reports.append(
            {
                "iteration": iteration_index + 1,
                "candidate_id": None,
                "status": "no_candidate",
                "failed_gates": [],
            }
        )
        return _result(GenerationExecutionDisposition.NEXT_ITERATION)
    run_state.generation.reset_candidate_progress_stalls()
    return _result(GenerationExecutionDisposition.PROCEED)
