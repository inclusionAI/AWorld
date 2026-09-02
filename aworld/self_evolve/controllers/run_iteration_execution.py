"""Typed orchestration of generation, screening, and evaluation iterations."""

from __future__ import annotations
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any
from aworld.self_evolve.budget import CandidateAttemptStage
from aworld.self_evolve.campaign_policy import (
    is_verified_apply_policy as _is_verified_apply_policy,
)
from aworld.self_evolve.candidate_package import (
    CandidateMutationKind,
    classify_candidate_mutation,
)
from aworld.self_evolve.controllers.run_execution import (
    CandidateEvaluationRequest,
    iteration_state as _iteration_state,
)
from aworld.self_evolve.controllers.run_conformance_lifecycle import (
    ConformanceLifecycleRequest,
    ConformanceLifecycleRuntime,
    advance_conformance_lifecycle,
)
from aworld.self_evolve.controllers.run_generation_execution import (
    GenerationExecutionDisposition,
    GenerationExecutionPolicy,
    GenerationExecutionRequest,
    GenerationExecutionRuntime,
    GenerationExecutionState,
    execute_generation_iteration,
)
from aworld.self_evolve.controllers.run_generation_helpers import (
    _optimizer_stored_candidate_admission_reason,
)
from aworld.self_evolve.controllers.run_iteration_helpers import (
    _authoritative_attempt_consumed,
    _candidate_gate_results,
    _candidate_repair_conformance_contracts,
    _candidate_screening_repair_failures,
    _candidate_screening_repair_feedback,
    _candidate_validation_shared_failure_gate,
    _candidate_validation_stopped_by_shared_infrastructure,
    _feedback_failure_reference,
    _infrastructure_prevented_comparable_evaluation,
    _record_authoritative_replay_observations,
)
from aworld.self_evolve.controllers.run_state import ExplicitRunStateAccumulator
from aworld.self_evolve.controllers.run_resources import (
    remaining_measurement_budget as _remaining_measurement_budget,
)
from aworld.self_evolve.controllers.screening import (
    StoredCandidateScreeningBypass,
)
from aworld.self_evolve.controllers.screening_execution import (
    _baseline_replay_artifact_dir,
    _emit_progress,
    _gate_has_typed_shared_measurement_failure,
    _replay_result_has_reusable_baseline,
    _shared_replay_failure_blocks_population,
    _with_typed_gate_failure_event,
    find_reusable_baseline_replay_dir as _find_reusable_baseline_replay_dir,
)
from aworld.self_evolve.datasets import SelfEvolveDataset
from aworld.self_evolve.feedback_diagnostics import (
    _feedback_requires_counterexample_screening,
    _merge_validation_feedback,
    _typed_gate_feedback_metrics,
)
from aworld.self_evolve.measurement import (
    MeasurementEarlyStopPolicy,
    MeasurementPolicyMode,
    MeasurementSummary,
    evaluate_measurement_stopping,
)
from aworld.self_evolve.optimizers.base import (
    CandidateOptimizer,
    CandidateSourceDisposition,
)
from aworld.self_evolve.provenance import (
    InferredNewSkillPolicy,
    TargetMutationIntent,
    TargetProvenance,
)
from aworld.self_evolve.replay import (
    CandidateReplayBackend,
    CandidateReplayEvidenceReuseBackend,
    replay_dataset_fingerprint,
)
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.targets import SelfEvolveTarget
from aworld.self_evolve.trace_pack import TracePack
from aworld.self_evolve.types import CandidateVariant, EvaluationSummary, GateResult

_DEFAULT_CANDIDATE_CONTENT_MAX_CHARS = 500_000
@dataclass(frozen=True)
class IterationExecutionPolicy:
    allow_external_target_mutation: bool
    allow_generated_target_mutation: bool
    inferred_new_skill_policy: InferredNewSkillPolicy
    max_full_evaluation_candidates: int
    max_score_tiebreak_candidates: int
    measurement_early_stop_policy: MeasurementEarlyStopPolicy
    measurement_mode: MeasurementPolicyMode
    replay_enabled: bool
    _active_target_intent: TargetMutationIntent | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "max_full_evaluation_candidates",
            "max_score_tiebreak_candidates",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")


@dataclass(frozen=True)
class IterationExecutionRuntime:
    store: FilesystemSelfEvolveStore
    optimizer: CandidateOptimizer
    candidate_replay_backend: CandidateReplayBackend | None
    progress_callback: Callable[[str, str], Any] | None
    _plan_candidate_measurement: Callable[..., Any]
    _screen_candidate_population: Callable[..., Any]
    _prepare_replay_adaptation: Callable[..., Any]
    _execute_iteration_candidate: Callable[..., Any]
    _baseline_reuse_provenance: Callable[..., Mapping[str, object]]
    _candidate_screening_case_observations: dict[str, object]
    _candidate_screening_control_observations: dict[str, object]
    _current_run_authoritative_case_observations: dict[str, object]
    _measurement_experiments: Mapping[object, Any]
    _measurement_summaries: dict[tuple[str, str], MeasurementSummary]


@dataclass(frozen=True)
class IterationExecutionRequest:
    run_id: str
    target: SelfEvolveTarget
    dataset: SelfEvolveDataset
    trace_packs: tuple[TracePack, ...]
    apply_policy: str
    target_provenance: TargetProvenance | None
    target_provenance_unresolved_reason: str | None
    target_selection_report: object | None
    prior_feedback: tuple[EvaluationSummary, ...]
    generation_lesson_records: tuple[Any, ...]
    rejected_candidate_ids: set[str]
    accepted_candidate_ids: set[str]
    current_run_attempted_candidate_ids: set[str]
    rejected_semantic_lesson_fingerprints: set[object]
    repair_reserved_slot_count: int
    replay_preflight: object
    target_package_inventory: tuple[str, ...]
    target_package_sources: Mapping[str, Mapping[str, object]]
    verification_settings: Mapping[str, object]
    estimated_baseline_repetitions: int
    iteration_budget: int
    run_state: ExplicitRunStateAccumulator
    attempt_tracker: Any
    budget_context: Any
    generation_execution_policy: GenerationExecutionPolicy
    generation_execution_runtime: GenerationExecutionRuntime
    generation_execution_state: GenerationExecutionState
    population_screening_reports: list[dict[str, object]]


@dataclass(frozen=True)
class IterationExecutionResult:
    validation_feedback: tuple[EvaluationSummary, ...]
    fresh_evaluation_required: bool
    latest_handbook_slice: Mapping[str, object] | None
    scheduler_state: object
    shared_validation_gate: GateResult | None




async def execute_iteration_lifecycle(
    request: IterationExecutionRequest,
    runtime: IterationExecutionRuntime,
    policy: IterationExecutionPolicy,
) -> IterationExecutionResult:
    run_id = request.run_id
    target = request.target
    dataset = request.dataset
    trace_packs = request.trace_packs
    apply_policy = request.apply_policy
    target_provenance = request.target_provenance
    target_provenance_unresolved_reason = request.target_provenance_unresolved_reason
    target_selection_report = request.target_selection_report
    prior_feedback = request.prior_feedback
    generation_lesson_records = request.generation_lesson_records
    rejected_candidate_ids = request.rejected_candidate_ids
    accepted_candidate_ids = request.accepted_candidate_ids
    current_run_attempted_candidate_ids = request.current_run_attempted_candidate_ids
    rejected_semantic_lesson_fingerprints = (
        request.rejected_semantic_lesson_fingerprints
    )
    repair_reserved_slot_count = request.repair_reserved_slot_count
    replay_preflight = request.replay_preflight
    target_package_inventory = request.target_package_inventory
    target_package_sources = request.target_package_sources
    verification_settings = request.verification_settings
    estimated_baseline_repetitions = request.estimated_baseline_repetitions
    iteration_budget = request.iteration_budget
    run_state = request.run_state
    attempt_tracker = request.attempt_tracker
    budget_context = request.budget_context
    generation_execution_policy = request.generation_execution_policy
    generation_execution_runtime = request.generation_execution_runtime
    generation_execution_state = request.generation_execution_state
    population_screening_reports = request.population_screening_reports
    candidate_source_dispositions = (
        generation_execution_state.candidate_source_dispositions
    )
    attempt_key_by_candidate_id = generation_execution_state.attempt_key_by_candidate_id
    iteration_reports = generation_execution_state.iteration_reports
    iteration_states = generation_execution_state.iteration_states
    validation_feedback = generation_execution_state.validation_feedback
    fresh_evaluation_required = generation_execution_state.fresh_evaluation_required
    latest_handbook_slice = generation_execution_state.latest_handbook_slice
    scheduler_state = generation_execution_state.scheduler_state
    shared_validation_gate: GateResult | None = None
    for iteration_index in range(iteration_budget):
        generation_execution_state.validation_feedback = validation_feedback
        generation_execution = await execute_generation_iteration(
            GenerationExecutionRequest(
                iteration_index=iteration_index,
                iteration_budget=iteration_budget,
                run_id=run_id,
                target=target,
                dataset=dataset,
                trace_packs=trace_packs,
                apply_policy=apply_policy,
                repair_reserved_slot_count=repair_reserved_slot_count,
                verification_settings=verification_settings,
                prior_feedback=prior_feedback,
                generation_lesson_records=generation_lesson_records,
                replay_preflight=replay_preflight,
                target_package_inventory=target_package_inventory,
                target_package_sources=target_package_sources,
                rejected_candidate_ids=rejected_candidate_ids,
                accepted_candidate_ids=accepted_candidate_ids,
                current_run_attempted_candidate_ids=current_run_attempted_candidate_ids,
                rejected_semantic_lesson_fingerprints=rejected_semantic_lesson_fingerprints,
                run_state=run_state,
                policy=generation_execution_policy,
                state=generation_execution_state,
            ),
            generation_execution_runtime,
        )
        scheduler_state = generation_execution.state.scheduler_state
        validation_feedback = generation_execution.state.validation_feedback
        fresh_evaluation_required = generation_execution.state.fresh_evaluation_required
        latest_handbook_slice = generation_execution.state.latest_handbook_slice
        if generation_execution.disposition is GenerationExecutionDisposition.STOP:
            break
        if (
            generation_execution.disposition
            is GenerationExecutionDisposition.NEXT_ITERATION
        ):
            continue
        candidate_population = generation_execution.candidate_population
        prerequisite_fidelity_gates = dict(
            generation_execution.prerequisite_fidelity_gates
        )
        optimizer_result = generation_execution.optimizer_result
        if optimizer_result is None:
            raise RuntimeError(
                "generation controller proceeded without optimizer result"
            )
        local_gate_results_by_candidate: dict[str, tuple[GateResult, ...]] = {}
        locally_valid_candidates: list[CandidateVariant] = []
        local_gate_feedback: list[EvaluationSummary] = []
        current_content = target.load_current_content()
        for candidate in candidate_population:
            attempt_key = attempt_key_by_candidate_id.get(candidate.candidate_id)
            raw_local_results = _candidate_gate_results(
                candidate,
                current_content=current_content,
                workspace_root=runtime.store.workspace_root,
                max_chars=_DEFAULT_CANDIDATE_CONTENT_MAX_CHARS,
                target_provenance=target_provenance,
                target_provenance_unresolved_reason=target_provenance_unresolved_reason,
                allow_generated_target_mutation=policy.allow_generated_target_mutation,
                allow_external_target_mutation=policy.allow_external_target_mutation,
                target_intent=policy._active_target_intent,
                inferred_new_skill_policy=policy.inferred_new_skill_policy,
                apply_policy=apply_policy,
            )
            prerequisite_fidelity_gate = prerequisite_fidelity_gates.get(
                candidate.candidate_id
            )
            if prerequisite_fidelity_gate is not None:
                raw_local_results = (*raw_local_results, prerequisite_fidelity_gate)
            local_results = tuple(
                (_with_typed_gate_failure_event(gate) for gate in raw_local_results)
            )
            local_gate_results_by_candidate[candidate.candidate_id] = local_results
            if attempt_key is None:
                continue
            attempt_tracker.emit(attempt_key, CandidateAttemptStage.LOCAL_GATES)
            failed_local = tuple(
                (
                    gate
                    for gate in local_results
                    if not gate.passed
                    and (
                        not (
                            apply_policy == "proposal"
                            and gate.gate_name == "trust_provenance"
                        )
                    )
                )
            )
            if not failed_local:
                locally_valid_candidates.append(candidate)
                continue
            local_feedback_metrics = _typed_gate_feedback_metrics(failed_local)
            local_feedback_metrics.update(
                {
                    "failed_gates": [gate.gate_name for gate in failed_local],
                    "candidate_status": "rejected",
                    "failure_class": "candidate",
                    "repairable": True,
                }
            )
            local_feedback = EvaluationSummary(
                variant_id=candidate.candidate_id,
                dataset_split="validation",
                metrics=local_feedback_metrics,
            )
            local_gate_feedback.append(local_feedback)
            iteration_states.append(
                _iteration_state(
                    candidate=candidate,
                    baseline_summary=None,
                    candidate_summary=None,
                    held_out_summary=None,
                    replay_result=None,
                    replay_dataset=None,
                    gate_results=local_results,
                    feedback=(local_feedback,),
                    status="rejected",
                )
            )
            attempt_tracker.emit(
                attempt_key,
                CandidateAttemptStage.REJECTED,
                reason_code="local_gate_rejected",
            )
        if local_gate_feedback:
            validation_feedback = _merge_validation_feedback(
                validation_feedback, tuple(local_gate_feedback)
            )
            rejected_candidate_ids.update(
                (item.variant_id for item in local_gate_feedback)
            )
            iteration_reports.extend(
                (
                    {
                        "iteration": iteration_index + 1,
                        "candidate_id": item.variant_id,
                        "status": "local_gate_rejected",
                        "failed_gates": list(item.metrics.get("failed_gates", [])),
                    }
                    for item in local_gate_feedback
                )
            )
        candidate_population = tuple(locally_valid_candidates)
        screening_candidates = candidate_population
        if not candidate_population:
            continue
        if policy.measurement_mode is not MeasurementPolicyMode.OFF:
            for planned_candidate in candidate_population:
                try:
                    runtime._plan_candidate_measurement(
                        run_id=run_id,
                        target=target,
                        dataset=dataset,
                        candidate=planned_candidate,
                        candidate_count=len(candidate_population),
                    )
                except (OSError, TypeError, ValueError):
                    continue
        repair_conformance_contracts = _candidate_repair_conformance_contracts(
            optimizer_result
        )
        stored_candidate_admission_reason = (
            _optimizer_stored_candidate_admission_reason(runtime.optimizer)
        )
        (
            candidate_population,
            screening_report,
        ) = await runtime._screen_candidate_population(
            run_id=run_id,
            target=target,
            dataset=dataset,
            candidates=candidate_population,
            apply_policy=apply_policy,
            capability_requirements=replay_preflight.requirements,
            repair_conformance_contracts=repair_conformance_contracts,
            attempt_tracker=attempt_tracker,
            attempt_keys=attempt_key_by_candidate_id,
            budget_context=budget_context,
            require_single_candidate_screening=_feedback_requires_counterexample_screening(
                (*prior_feedback, *validation_feedback)
            ),
            stored_candidate_bypass=(
                StoredCandidateScreeningBypass(stored_candidate_admission_reason)
                if stored_candidate_admission_reason
                in {item.value for item in StoredCandidateScreeningBypass}
                else None
            ),
        )
        if screening_report is not None:
            population_screening_reports.append(screening_report)
            raw_superseded = screening_report.get("superseded_candidate_ids")
            if isinstance(raw_superseded, (list, tuple)):
                run_state.generation.release_effective_candidates(
                    {str(item) for item in raw_superseded if str(item)}
                )
        if _candidate_validation_stopped_by_shared_infrastructure(screening_report):
            run_state.infrastructure_blocked = True
            framework_invalidated_ids = {
                candidate.candidate_id for candidate in screening_candidates
            }
            run_state.generation.release_effective_candidates(framework_invalidated_ids)
            if isinstance(screening_report, dict):
                screening_report["framework_invalidated_candidate_ids"] = sorted(
                    framework_invalidated_ids
                )
                screening_report["effective_candidate_slot_count"] = (
                    run_state.generation.generated_candidate_slot_count
                )
                for stage_name in ("conformance", "screening"):
                    stage_report = screening_report.get(stage_name)
                    if (
                        isinstance(stage_report, dict)
                        and stage_report.get("stopped_by_shared_infrastructure") is True
                    ):
                        stage_report["framework_invalidated_candidate_ids"] = sorted(
                            framework_invalidated_ids
                        )
                        stage_report["effective_candidate_slot_count"] = (
                            run_state.generation.generated_candidate_slot_count
                        )
            shared_validation_gate = _candidate_validation_shared_failure_gate(
                screening_report
            )
            for blocked_candidate in screening_candidates:
                blocked_key = attempt_key_by_candidate_id.get(
                    blocked_candidate.candidate_id
                )
                if blocked_key is not None and (
                    not attempt_tracker.terminal(blocked_key)
                ):
                    attempt_tracker.emit(
                        blocked_key,
                        CandidateAttemptStage.BLOCKED,
                        reason_code="candidate_validation_shared_infrastructure_blocked",
                    )
            break
        screening_failures = _candidate_screening_repair_failures(
            screening_candidates, screening_report
        )
        screening_feedback = _candidate_screening_repair_feedback(
            screening_candidates, screening_report
        )
        if screening_feedback:
            validation_feedback = _merge_validation_feedback(
                validation_feedback, screening_feedback
            )
            rejected_candidate_ids.update(
                (item.variant_id for item in screening_feedback)
            )
            current_run_attempted_candidate_ids.update(
                (item.variant_id for item in screening_feedback)
            )
            iteration_reports.extend(
                (
                    {
                        "iteration": iteration_index + 1,
                        "candidate_id": item.variant_id,
                        "status": "screening_rejected",
                        "failed_gates": list(item.metrics.get("failed_gates", [])),
                    }
                    for item in screening_feedback
                )
            )
            for item in screening_feedback:
                screened_key = attempt_key_by_candidate_id.get(item.variant_id)
                if screened_key is not None and (
                    not attempt_tracker.terminal(screened_key)
                ):
                    failure_event_id, semantic_key = _feedback_failure_reference(item)
                    attempt_tracker.emit(
                        screened_key,
                        CandidateAttemptStage.REJECTED,
                        reason_code="candidate_validation_rejected",
                        failure_event_id=failure_event_id,
                        semantic_failure_key=semantic_key,
                    )
            feedback_by_candidate = {
                item.variant_id: item for item in screening_feedback
            }
            for failed_candidate, failed_gate in screening_failures:
                candidate_feedback = feedback_by_candidate.get(
                    failed_candidate.candidate_id
                )
                iteration_states.append(
                    _iteration_state(
                        candidate=failed_candidate,
                        baseline_summary=None,
                        candidate_summary=None,
                        held_out_summary=None,
                        replay_result=None,
                        replay_dataset=None,
                        gate_results=[failed_gate],
                        feedback=(candidate_feedback,)
                        if candidate_feedback is not None
                        else (),
                        status="rejected",
                    )
                )
        conformance_lifecycle = advance_conformance_lifecycle(
            ConformanceLifecycleRequest(
                failures=screening_failures,
                feedback=screening_feedback,
                candidate_population_empty=not candidate_population,
                screening_report=(
                    screening_report
                    if isinstance(screening_report, dict)
                    else None
                ),
                validation_feedback=validation_feedback,
                run_state=run_state,
            ),
            ConformanceLifecycleRuntime(
                progress_callback=runtime.progress_callback,
                emit_progress=_emit_progress,
            ),
        )
        validation_feedback = conformance_lifecycle.validation_feedback
        if screening_feedback and (not candidate_population):
            if conformance_lifecycle.should_stop:
                break
            continue
        accepted_in_iteration = False
        reusable_baseline_replay_dir: str | None = None
        if (
            policy.replay_enabled
            and target.identity.target_type == "skill"
            and (runtime.candidate_replay_backend is not None)
            and (
                not isinstance(
                    runtime.candidate_replay_backend,
                    CandidateReplayEvidenceReuseBackend,
                )
            )
        ):
            replay_adaptation, replay_adaptation_gate = (
                runtime._prepare_replay_adaptation(
                    run_id=run_id, dataset=dataset, emit_progress=False
                )
            )
            if replay_adaptation_gate.passed and replay_adaptation is not None:
                reusable_baseline_replay_dir = _find_reusable_baseline_replay_dir(
                    store=runtime.store,
                    run_id=run_id,
                    target=target.identity,
                    dataset=dataset,
                    baseline_repetitions=estimated_baseline_repetitions,
                    baseline_skill_fingerprint=target.fingerprint_current_content(),
                    dataset_fingerprint=replay_dataset_fingerprint(dataset),
                    adaptation_fingerprint=replay_adaptation.adaptation_fingerprint,
                    workspace_seed_fingerprint=replay_adaptation.workspace_seed_fingerprint,
                )
        for candidate_index, iteration_candidate in enumerate(candidate_population):
            iteration_mutation = classify_candidate_mutation(
                iteration_candidate, current_content=target.load_current_content()
            )
            counts_toward_authoritative = (
                iteration_mutation.kind is not CandidateMutationKind.EVALUATION_SUPPORT
            )
            if (
                _is_verified_apply_policy(apply_policy)
                and counts_toward_authoritative
                and (
                    run_state.authoritative_candidate_count
                    >= policy.max_full_evaluation_candidates
                )
            ):
                run_state.generation.verification_frontier_exhausted = True
                deferred_key = attempt_key_by_candidate_id.get(
                    iteration_candidate.candidate_id
                )
                if deferred_key is not None and (
                    not attempt_tracker.terminal(deferred_key)
                ):
                    attempt_tracker.emit(
                        deferred_key,
                        CandidateAttemptStage.NOT_RUN,
                        reason_code="authoritative_frontier_limit_reached",
                    )
                iteration_reports.append(
                    {
                        "iteration": iteration_index + 1,
                        "candidate_id": iteration_candidate.candidate_id,
                        "status": "not_run",
                        "failed_gates": [],
                        "lifecycle_stage": "not_run",
                        "reason_code": "authoritative_frontier_limit_reached",
                    }
                )
                break
            if counts_toward_authoritative:
                run_state.begin_authoritative_candidate(
                    iteration_candidate.candidate_id, counts_toward_authoritative=True
                )
            evaluation_result = await runtime._execute_iteration_candidate(
                CandidateEvaluationRequest(
                    run_id=run_id,
                    target=target,
                    dataset=dataset,
                    candidate=iteration_candidate,
                    apply_policy=apply_policy,
                    target_provenance=target_provenance,
                    target_provenance_unresolved_reason=target_provenance_unresolved_reason,
                    target_selection_report=target_selection_report,
                    iteration_number=iteration_index + 1,
                    candidate_number=candidate_index + 1,
                    candidate_count=len(candidate_population),
                    rejected_candidate_ids=rejected_candidate_ids,
                    accepted_candidate_ids=accepted_candidate_ids,
                    baseline_replay_dir=reusable_baseline_replay_dir,
                    capability_requirements=replay_preflight.requirements,
                    attempt_key=attempt_key_by_candidate_id.get(
                        iteration_candidate.candidate_id
                    ),
                    attempt_tracker=attempt_tracker,
                    budget_context=budget_context,
                    precomputed_gate_results=local_gate_results_by_candidate.get(
                        iteration_candidate.candidate_id, ()
                    ),
                    source_disposition=candidate_source_dispositions.get(
                        iteration_candidate.candidate_id, CandidateSourceDisposition()
                    ),
                    baseline_evaluation_cache=run_state.baseline_evaluation_cache,
                    allow_score_tiebreak=run_state.score_tiebreak_candidate_count
                    < policy.max_score_tiebreak_candidates,
                )
            )
            report_item = evaluation_result.report_item
            evaluated_attempt_key = attempt_key_by_candidate_id.get(
                iteration_candidate.candidate_id
            )
            if evaluated_attempt_key is not None and attempt_tracker.has_stage(
                evaluated_attempt_key,
                CandidateAttemptStage.PAIRED_REPLAY_STARTED,
                CandidateAttemptStage.PAIRED_REPLAY_COMPLETED,
                CandidateAttemptStage.PAIRED_REPLAY_COMPARABLE,
            ):
                report_item["lifecycle_stage"] = "authoritative_replay"
            elif evaluated_attempt_key is not None:
                report_item["lifecycle_stage"] = attempt_tracker.last_stage(
                    evaluated_attempt_key
                ).value
            run_state.validation_feedback = validation_feedback
            candidate_record = run_state.record_candidate_evaluation(
                candidate_id=iteration_candidate.candidate_id,
                result=evaluation_result,
                counts_toward_authoritative=counts_toward_authoritative,
                merge_feedback=_merge_validation_feedback,
                shared_measurement_failure=_gate_has_typed_shared_measurement_failure,
                authoritative_attempt_consumed=_authoritative_attempt_consumed,
            )
            validation_feedback = run_state.validation_feedback
            state = candidate_record.state
            state_measurement = candidate_record.measurement_summary
            if policy.measurement_mode in {
                MeasurementPolicyMode.ADVISORY,
                MeasurementPolicyMode.REQUIRED,
            } and isinstance(state_measurement, MeasurementSummary):
                measurement_authority_run_id = next(
                    (
                        experiment.run_id
                        for experiment in runtime._measurement_experiments.values()
                        if experiment.experiment_id == state_measurement.experiment_id
                    ),
                    run_id,
                )
                attribution = runtime.store.read_measurement_attribution_report(
                    measurement_authority_run_id, state_measurement.experiment_id
                )
                run_state.measurement_attributions.append(attribution)
                stop_record = evaluate_measurement_stopping(
                    run_state.measurement_attributions,
                    policy=policy.measurement_early_stop_policy,
                    unused_budget=_remaining_measurement_budget(budget_context),
                )
                updated_attribution = replace(attribution, stopping=stop_record)
                runtime.store.write_measurement_attribution_report(updated_attribution)
                updated_summary = updated_attribution.summary(
                    attribution_report_path=runtime.store.measurement_attribution_ref(
                        measurement_authority_run_id, state_measurement.experiment_id
                    )
                )
                state["measurement_summary"] = updated_summary
                runtime._measurement_summaries[
                    run_id, iteration_candidate.candidate_id
                ] = updated_summary
                if stop_record.triggered:
                    run_state.measurement_frontier_stopped = True
                    report_item["measurement_stop"] = stop_record.to_dict()
            replay_state = candidate_record.replay_result
            if replay_state is not None:
                _record_authoritative_replay_observations(
                    runtime._candidate_screening_case_observations,
                    dataset=dataset,
                    replay_result=replay_state,
                    run_observations=runtime._current_run_authoritative_case_observations,
                    control_observations=runtime._candidate_screening_control_observations,
                )
                if _replay_result_has_reusable_baseline(
                    dataset=dataset, replay_result=replay_state
                ):
                    reusable_baseline_replay_dir = _baseline_replay_artifact_dir(
                        replay_state
                    )
            refreshed_baseline_dir = _find_reusable_baseline_replay_dir(
                store=runtime.store,
                run_id=run_id,
                target=target.identity,
                dataset=dataset,
                baseline_repetitions=estimated_baseline_repetitions,
                **runtime._baseline_reuse_provenance(
                    run_id=run_id, target=target, dataset=dataset
                ),
            )
            if refreshed_baseline_dir is not None:
                reusable_baseline_replay_dir = refreshed_baseline_dir
            loop_decision = run_state.finalize_candidate_record(
                candidate_record,
                shared_replay_failure_blocks_population=_shared_replay_failure_blocks_population,
                infrastructure_prevented_comparable_evaluation=_infrastructure_prevented_comparable_evaluation,
            )
            accepted_in_iteration = loop_decision.accepted
            if loop_decision.should_stop:
                break
        if (
            _is_verified_apply_policy(apply_policy)
            and (not accepted_in_iteration)
            and (
                run_state.authoritative_candidate_count
                >= policy.max_full_evaluation_candidates
            )
        ):
            run_state.generation.verification_frontier_exhausted = True
        if (
            accepted_in_iteration
            or run_state.baseline_preflight_blocked
            or run_state.infrastructure_blocked
            or run_state.generation.verification_frontier_exhausted
            or run_state.measurement_frontier_stopped
        ):
            break
    return IterationExecutionResult(
        validation_feedback,
        fresh_evaluation_required,
        latest_handbook_slice,
        scheduler_state,
        shared_validation_gate,
    )
