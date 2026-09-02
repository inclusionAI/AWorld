"""Terminal selection, promotion, reporting, and persistence orchestration."""

from __future__ import annotations
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any
from aworld.self_evolve.campaign_policy import (
    campaign_measurement_outcome_for_replay as _campaign_measurement_outcome_for_replay,
    is_verified_apply_policy as _is_verified_apply_policy,
)
from aworld.self_evolve.feedback_history import (
    _gate_has_candidate_prerequisite_failure,
)
from aworld.self_evolve.controllers.measurement import (
    CandidateMeasurementController,
    MeasurementSearchProjectionExecution,
    MeasurementSearchProjectionRequest,
    measurement_promotion_gate as _measurement_promotion_gate,
)
from aworld.self_evolve.controllers.run_apply_transaction import (
    ApplyTransactionExecution,
    ApplyTransactionRequest,
)
from aworld.self_evolve.controllers.run_verified_only_apply import (
    VerifiedOnlyApplyExecution,
    VerifiedOnlyApplyRequest,
)
from aworld.self_evolve.controllers.run_generation_helpers import (
    _VERIFICATION_CONTRACT_VERSION,
    _typed_repair_frontiers,
)
from aworld.self_evolve.controllers.run_budget_support import _execution_usage_report
from aworld.self_evolve.controllers.run_iteration_helpers import (
    _infrastructure_prevented_comparable_evaluation,
)
from aworld.self_evolve.controllers.run_state import (
    ExplicitRunStateAccumulator,
    VerificationFunnelRequest,
)
from aworld.self_evolve.controllers.run_terminal import (
    InferredDraftPromotionRequest,
    MeasurementReportRequest,
    TerminalPromotionRequest,
    TerminalPromotionRuntime,
    TerminalSelectionRequest,
    TerminalSelectionRuntime,
    plan_terminal_promotion,
    project_inferred_draft_promotion,
    project_measurement_report,
    project_target_selection_promotion_diagnostics,
    project_terminal_selection,
    settle_post_apply_status,
)
from aworld.self_evolve.controllers.run_terminal_finalization import (
    TerminalFinalizationRequest,
    TerminalFinalizationRuntime,
    finalize_terminal_run,
)
from aworld.self_evolve.controllers.screening_execution import (
    _emit_progress,
    _replay_artifact_path,
)
from aworld.self_evolve.datasets import SelfEvolveDataset
from aworld.self_evolve.measurement import MeasurementPolicyMode, MeasurementSummary
from aworld.self_evolve.optimizers.base import CandidateSourceDisposition
from aworld.self_evolve.replay_gates import _gate_blocks_measurement_materialization
from aworld.self_evolve.run_failure_attribution import (
    _campaign_failure_attribution,
    _candidate_generation_failure_events,
    _candidate_policy_filter_outcomes,
    _candidate_policy_frontier_stalled_event,
    _optimizer_iteration_diagnostics,
    _rejection_attribution,
    _resolved_conformance_contract_fingerprints,
    _status_without_selected_candidate,
    _terminal_cause,
)
from aworld.self_evolve.iteration_selection import _select_iteration_state
from aworld.self_evolve.lineage_history import (
    _lineage_addressed_lesson_ids,
    _persist_lineage_lifecycle,
)
from aworld.self_evolve.population_projection import _population_report
from aworld.self_evolve.run_history import _SEMANTIC_DEDUP_IDENTITY_VERSION
from aworld.self_evolve.run_reporting import (
    _acceptance_confidence_report,
    _evaluator_report_paths,
    _no_op_report,
    _repair_frontier_state_report,
    _replay_capability_report,
    _replay_report,
    _trajectory_set_report,
)
from aworld.self_evolve.provenance import InferredNewSkillPolicy, TargetMutationIntent
from aworld.self_evolve.replay import CandidateReplayResult
from aworld.self_evolve.skill_evolution_contract import (
    SkillEvolutionContract,
    evaluate_skill_evolution_replay,
)
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.target_package import _target_runtime_skill_path
from aworld.self_evolve.targets import DraftSkillTextTarget, SelfEvolveTarget
from aworld.self_evolve.trace_pack import TracePack
from aworld.self_evolve.types import (
    CandidateVariant,
    EvaluationSummary,
    GateResult,
    SelfEvolveRun,
    SelfEvolveRunStatus,
)


@dataclass(frozen=True)
class RunTerminalLifecycleServices:
    _finalize_run_report: Callable[..., Any]


@dataclass(frozen=True)
class RunTerminalLifecycleRuntime:
    store: FilesystemSelfEvolveStore
    progress_callback: Callable[[str, str], Any] | None
    execution_telemetry: Any
    measurement_mode: MeasurementPolicyMode
    _measurement_experiments: Mapping[object, Any]
    _candidate_screening_control_observations: Mapping[str, object]
    _current_run_authoritative_case_observations: Mapping[str, object]
    _candidate_screening_loaded_run_ids: set[str]
    _active_target_intent: TargetMutationIntent | None
    inferred_new_skill_policy: InferredNewSkillPolicy
    skill_evolution_contract: SkillEvolutionContract | None
    runtime_registry_refresher: Callable[..., Any] | None
    candidate_screening_max_cases: int
    max_generated_candidates: int
    max_full_evaluation_candidates: int
    max_score_tiebreak_candidates: int
    replay_candidate_limit: int
    regression_suites: tuple[Any, ...]
    deprecated_config_mappings: Mapping[str, object]
    measurement_controller: CandidateMeasurementController
    measurement_search_projection: MeasurementSearchProjectionExecution
    auto_apply: ApplyTransactionExecution | None
    verified_only_apply: VerifiedOnlyApplyExecution
    runtime_registry_compensator: Callable[..., Any] | None = None
    runtime_skill_compensator: Callable[..., Any] | None = None
    auto_apply_override: Callable[..., Any] | None = None
    verified_only_apply_override: Callable[..., Any] | None = None


@dataclass(frozen=True)
class RunTerminalLifecycleRequest:
    run_id: str
    target: SelfEvolveTarget
    dataset: SelfEvolveDataset
    trace_packs: tuple[TracePack, ...]
    apply_policy: str
    campaign_prior_run_ids: tuple[str, ...] | None
    campaign_id: str | None
    campaign_cycle: int | None
    target_provenance_report: Mapping[str, object]
    target_selection_report: Any
    screening_control_preflight: Mapping[str, object]
    startup_artifact_retention: Mapping[str, object] | None
    prior_feedback: tuple[EvaluationSummary, ...]
    validation_feedback: tuple[EvaluationSummary, ...]
    all_candidates: list[CandidateVariant]
    candidate_source_dispositions: Mapping[str, CandidateSourceDisposition]
    fresh_evaluation_required: bool
    optimizer_diagnostics: list[dict[str, object]]
    optimizer_lineage_paths: list[str]
    optimizer_lineage_paths_by_candidate: Mapping[str, str]
    iteration_reports: list[dict[str, object]]
    iteration_states: list[dict[str, object]]
    population_screening_reports: list[dict[str, object]]
    scheduler_decisions: list[dict[str, object]]
    scheduler_state: Any
    budget_context: Any
    run_state: ExplicitRunStateAccumulator
    iteration_budget: int
    repair_reserved_slot_count: int
    selected_candidate: CandidateVariant | None
    baseline_summary: EvaluationSummary | None
    candidate_summary: EvaluationSummary | None
    held_out_summary: EvaluationSummary | None
    measurement_summary: MeasurementSummary | None
    regression_evidence: Any
    challenge_report: Any
    replay_result: CandidateReplayResult | None
    replay_dataset: SelfEvolveDataset | None
    latest_handbook_slice: Mapping[str, object] | None
    shared_validation_gate: GateResult | None
    gate_results: list[GateResult]
    attempt_tracker: Any


@dataclass(frozen=True)
class RunTerminalLifecycleResult:
    completed_run: SelfEvolveRun
    selected_candidate: CandidateVariant | None


async def execute_terminal_lifecycle(
    request: RunTerminalLifecycleRequest,
    runtime: RunTerminalLifecycleRuntime,
    services: RunTerminalLifecycleServices,
) -> RunTerminalLifecycleResult:
    run_id = request.run_id
    target = request.target
    dataset = request.dataset
    trace_packs = request.trace_packs
    apply_policy = request.apply_policy
    campaign_prior_run_ids = request.campaign_prior_run_ids
    campaign_id = request.campaign_id
    campaign_cycle = request.campaign_cycle
    target_provenance_report = request.target_provenance_report
    target_selection_report = request.target_selection_report
    screening_control_preflight = request.screening_control_preflight
    startup_artifact_retention = request.startup_artifact_retention
    prior_feedback = request.prior_feedback
    validation_feedback = request.validation_feedback
    all_candidates = request.all_candidates
    candidate_source_dispositions = request.candidate_source_dispositions
    fresh_evaluation_required = request.fresh_evaluation_required
    optimizer_diagnostics = request.optimizer_diagnostics
    optimizer_lineage_paths = request.optimizer_lineage_paths
    optimizer_lineage_paths_by_candidate = request.optimizer_lineage_paths_by_candidate
    iteration_reports = request.iteration_reports
    iteration_states = request.iteration_states
    population_screening_reports = request.population_screening_reports
    scheduler_decisions = request.scheduler_decisions
    scheduler_state = request.scheduler_state
    budget_context = request.budget_context
    run_state = request.run_state
    iteration_budget = request.iteration_budget
    repair_reserved_slot_count = request.repair_reserved_slot_count
    selected_candidate = request.selected_candidate
    baseline_summary = request.baseline_summary
    candidate_summary = request.candidate_summary
    held_out_summary = request.held_out_summary
    measurement_summary = request.measurement_summary
    regression_evidence = request.regression_evidence
    challenge_report = request.challenge_report
    replay_result = request.replay_result
    replay_dataset = request.replay_dataset
    latest_handbook_slice = request.latest_handbook_slice
    shared_validation_gate = request.shared_validation_gate
    gate_results = request.gate_results
    attempt_tracker = request.attempt_tracker
    attempt_tracker.finalize_open(reason_code="run_terminated_before_candidate")
    budget_context.release_all(reason_code="run_terminal_cleanup")
    selected_projection = (
        None
        if shared_validation_gate is not None
        else run_state.select_iteration_evidence(
            fresh_evaluation_required=fresh_evaluation_required,
            selector=_select_iteration_state,
        )
    )
    selected_state = (
        selected_projection.state if selected_projection is not None else None
    )
    if shared_validation_gate is not None:
        gate_results.append(shared_validation_gate)
    elif selected_projection is not None:
        baseline_summary = selected_projection.baseline_summary
        candidate_summary = selected_projection.candidate_summary
        held_out_summary = selected_projection.held_out_summary
        regression_evidence = selected_projection.regression_evidence
        challenge_report = selected_projection.challenge_report
        replay_result = selected_projection.replay_result
        replay_dataset = selected_projection.replay_dataset
        gate_results = list(selected_projection.gate_results)
        selected_candidate = selected_projection.selected_candidate
    else:
        semantic_dedup_exhausted = (
            run_state.generation.semantic_lesson_duplicate_attempt_count > 0
            and run_state.generation.semantic_lesson_duplicate_attempt_count
            == run_state.generation.raw_generation_attempt_count
            and (not all_candidates)
        )
        candidate_generation_failure_events = (
            (
                _candidate_policy_frontier_stalled_event(
                    run_state.generation.last_policy_filter_outcomes
                ),
            )
            if run_state.generation.policy_frontier_exhausted
            else _candidate_generation_failure_events(optimizer_diagnostics)
        )
        candidate_generation_failure_event = (
            candidate_generation_failure_events[0]
            if candidate_generation_failure_events
            else None
        )
        candidate_generation_details: dict[str, object] = {
            "generated_candidate_count": len(all_candidates),
            "iterations": len(optimizer_diagnostics),
        }
        if run_state.generation.raw_generation_attempt_count:
            candidate_generation_details["generation_attempt_count"] = (
                run_state.generation.raw_generation_attempt_count
            )
        if run_state.generation.policy_frontier_exhausted:
            candidate_generation_details["generation_policy_frontier_exhausted"] = True
        if run_state.generation.materialization_frontier_exhausted:
            candidate_generation_details[
                "generation_materialization_frontier_exhausted"
            ] = True
        if run_state.generation.protocol_frontier_exhausted:
            candidate_generation_details["generation_protocol_frontier_exhausted"] = (
                True
            )
        if candidate_generation_failure_event is not None:
            candidate_generation_details.update(
                {
                    "failure_class": "candidate",
                    "code": candidate_generation_failure_event["code"],
                    "failure_event": candidate_generation_failure_event,
                    "causal_failure_events": list(candidate_generation_failure_events),
                }
            )
        gate_results.append(
            GateResult(
                gate_name="candidate_generation_exhausted_by_semantic_dedup"
                if semantic_dedup_exhausted
                else "candidate_generation"
                if _is_verified_apply_policy(apply_policy)
                else "no_candidate",
                passed=False,
                reason="all generated candidates repeated historically rejected complete semantic packages under the active verification contract"
                if semantic_dedup_exhausted
                else "candidate generation policy frontier repeated without structural progress"
                if run_state.generation.policy_frontier_exhausted
                else "candidate generation repeated the same typed materialization failure without repair progress"
                if run_state.generation.materialization_frontier_exhausted
                else "candidate generation produced a non-repairable protocol failure"
                if run_state.generation.protocol_frontier_exhausted
                else "optimizer did not produce a replayable candidate"
                if _is_verified_apply_policy(apply_policy)
                else "optimizer did not produce a candidate",
                details={
                    "failure_class": "candidate",
                    "code": "candidate_generation_exhausted_by_semantic_dedup",
                    "generation_attempt_count": run_state.generation.raw_generation_attempt_count,
                    "canonical_unique_candidate_count": len(all_candidates),
                    "semantic_lesson_duplicate_attempt_count": run_state.generation.semantic_lesson_duplicate_attempt_count,
                    "semantic_identity_version": _SEMANTIC_DEDUP_IDENTITY_VERSION,
                    "verification_contract_version": _VERIFICATION_CONTRACT_VERSION,
                    "iterations": len(optimizer_diagnostics),
                }
                if semantic_dedup_exhausted
                else candidate_generation_details
                if _is_verified_apply_policy(apply_policy)
                else None,
            )
        )
    terminal_selection = project_terminal_selection(
        TerminalSelectionRequest(
            selected_candidate=selected_candidate, gate_results=tuple(gate_results)
        ),
        runtime=TerminalSelectionRuntime(
            candidate_prerequisite_failure=_gate_has_candidate_prerequisite_failure,
            measurement_materialization_blocked=_gate_blocks_measurement_materialization,
        ),
    )
    gate_results = list(terminal_selection.gate_results)
    candidate_prerequisite_blocked = terminal_selection.candidate_prerequisite_blocked
    repair_focus_candidate = terminal_selection.repair_focus_candidate
    reported_selected_candidate = terminal_selection.reported_selected_candidate
    measurement_prerequisite_blocked = (
        terminal_selection.measurement_prerequisite_blocked
    )
    if selected_state is not None and (not candidate_prerequisite_blocked):
        raw_measurement_summary = selected_state.get("measurement_summary")
        if isinstance(raw_measurement_summary, MeasurementSummary):
            measurement_summary = raw_measurement_summary
        elif runtime.measurement_mode is not MeasurementPolicyMode.OFF and (
            not measurement_prerequisite_blocked
        ):
            state_candidate = selected_state.get("candidate")
            if isinstance(state_candidate, CandidateVariant):
                experiment = runtime._measurement_experiments.get(
                    (run_id, state_candidate.candidate_id)
                )
                if experiment is not None:
                    try:
                        measurement_summary = (
                            runtime.measurement_controller.materialize_candidate(
                                experiment=experiment,
                                materialization_run_id=run_id,
                                candidate=state_candidate,
                                dataset=dataset,
                                replay_result=selected_state.get("replay_result")
                                if isinstance(
                                    selected_state.get("replay_result"),
                                    CandidateReplayResult,
                                )
                                else None,
                                replay_dataset=selected_state.get("replay_dataset")
                                if isinstance(
                                    selected_state.get("replay_dataset"),
                                    SelfEvolveDataset,
                                )
                                else None,
                                baseline_summary=selected_state.get("baseline_summary")
                                if isinstance(
                                    selected_state.get("baseline_summary"),
                                    EvaluationSummary,
                                )
                                else None,
                                candidate_summary=selected_state.get(
                                    "candidate_summary"
                                )
                                if isinstance(
                                    selected_state.get("candidate_summary"),
                                    EvaluationSummary,
                                )
                                else None,
                                candidate_count=max(1, len(all_candidates)),
                                authoritative_candidate_count=1,
                                target_selection_report=target_selection_report,
                            )
                        )
                        selected_state["measurement_summary"] = measurement_summary
                        if (
                            runtime.measurement_mode is MeasurementPolicyMode.REQUIRED
                            and (
                                not any(
                                    (
                                        gate.gate_name
                                        == "trusted_improvement_measurement"
                                        for gate in gate_results
                                    )
                                )
                            )
                        ):
                            gate_results.append(
                                _measurement_promotion_gate(measurement_summary)
                            )
                    except (OSError, TypeError, ValueError):
                        if (
                            runtime.measurement_mode is MeasurementPolicyMode.REQUIRED
                            and (
                                not any(
                                    (
                                        gate.gate_name
                                        == "trusted_improvement_measurement"
                                        for gate in gate_results
                                    )
                                )
                            )
                        ):
                            gate_results.append(
                                GateResult(
                                    gate_name="trusted_improvement_measurement",
                                    passed=False,
                                    reason="controlled measurement could not be finalized",
                                    details={
                                        "failure_class": "measurement",
                                        "code": "measurement_materialization_failed",
                                    },
                                )
                            )
    elif (
        not candidate_prerequisite_blocked
        and (not measurement_prerequisite_blocked)
        and (runtime.measurement_mode is not MeasurementPolicyMode.OFF)
        and all_candidates
    ):
        fallback_candidate = all_candidates[-1]
        fallback_experiment = runtime._measurement_experiments.get(
            (run_id, fallback_candidate.candidate_id)
        )
        if fallback_experiment is not None:
            try:
                measurement_summary = runtime.measurement_controller.materialize_candidate(
                    experiment=fallback_experiment,
                    materialization_run_id=run_id,
                    candidate=fallback_candidate,
                    dataset=dataset,
                    replay_result=None,
                    replay_dataset=None,
                    baseline_summary=None,
                    candidate_summary=None,
                    candidate_count=max(1, len(all_candidates)),
                    authoritative_candidate_count=0,
                    target_selection_report=target_selection_report,
                )
                if runtime.measurement_mode is MeasurementPolicyMode.REQUIRED:
                    gate_results.append(
                        _measurement_promotion_gate(measurement_summary)
                    )
            except (OSError, TypeError, ValueError):
                if runtime.measurement_mode is MeasurementPolicyMode.REQUIRED:
                    gate_results.append(
                        GateResult(
                            gate_name="trusted_improvement_measurement",
                            passed=False,
                            reason="controlled measurement could not be finalized",
                            details={
                                "failure_class": "measurement",
                                "code": "measurement_materialization_failed",
                            },
                        )
                    )
    if measurement_summary is not None:
        try:
            measurement_summary = runtime.measurement_search_projection.execute(
                MeasurementSearchProjectionRequest(
                    run_id=run_id,
                    summary=measurement_summary,
                    candidates=tuple(all_candidates),
                    iteration_reports=tuple(iteration_reports),
                )
            ).summary
        except (OSError, TypeError, ValueError):
            pass
    skill_evolution_progress: dict[str, object] | None = None
    if runtime.skill_evolution_contract is not None and replay_result is not None:
        intervention_observed = any(
            (
                gate.gate_name == "candidate_replay"
                and isinstance(gate.details, Mapping)
                and (gate.details.get("candidate_intervention_observed") is True)
                for gate in gate_results
            )
        )
        skill_evolution_progress = evaluate_skill_evolution_replay(
            runtime.skill_evolution_contract,
            replay_result,
            candidate_intervention_observed=intervention_observed,
        )
        coverage_satisfied = skill_evolution_progress["coverage_satisfied"] is True
        gate_results.append(
            GateResult(
                gate_name="skill_evolution_contract",
                passed=coverage_satisfied,
                reason="target Skill capability coverage is satisfied"
                if coverage_satisfied
                else "target Skill capability coverage is incomplete",
                details={
                    **skill_evolution_progress,
                    "failure_class": None if coverage_satisfied else "candidate",
                    "failure_owner": None if coverage_satisfied else "candidate",
                    "failure_scope": None if coverage_satisfied else "candidate",
                    "repairable": not coverage_satisfied,
                    "code": "skill_contract_coverage_satisfied"
                    if coverage_satisfied
                    else "skill_contract_coverage_incomplete",
                },
            )
        )
    post_apply: dict[str, object] | None = None
    inferred_draft_creation = (
        runtime._active_target_intent == TargetMutationIntent.INFERRED_DRAFT_CREATION
    )
    promotion_plan = plan_terminal_promotion(
        TerminalPromotionRequest(
            selected_candidate=selected_candidate,
            gate_results=tuple(gate_results),
            apply_policy=apply_policy,
            measurement_mode=runtime.measurement_mode,
            measurement_summary=measurement_summary,
            fresh_evaluation_required=fresh_evaluation_required,
            optimizer_diagnostics=tuple(optimizer_diagnostics),
            baseline_summary=baseline_summary,
            candidate_summary=candidate_summary,
            inferred_draft_creation=inferred_draft_creation,
            inferred_new_skill_policy=runtime.inferred_new_skill_policy,
        ),
        runtime=TerminalPromotionRuntime(
            verified_apply_policy=_is_verified_apply_policy,
            infrastructure_prevented_comparable_evaluation=_infrastructure_prevented_comparable_evaluation,
            status_without_selected_candidate=_status_without_selected_candidate,
        ),
    )
    final_status = promotion_plan.final_status
    promotion = (
        dict(promotion_plan.promotion) if promotion_plan.promotion is not None else None
    )
    if promotion_plan.should_apply:
        assert selected_candidate is not None
        apply_kwargs = {
            "expected_package_fingerprint": replay_result.request.verified_candidate_package_fingerprint
            if replay_result is not None
            else None,
            "addressed_lesson_ids": _lineage_addressed_lesson_ids(
                optimizer_lineage_paths_by_candidate.get(
                    selected_candidate.candidate_id
                )
            ),
        }
        if apply_policy == "verified_only":
            if runtime.verified_only_apply_override is not None:
                post_apply = await runtime.verified_only_apply_override(
                    run_id, target, selected_candidate, **apply_kwargs
                )
            else:
                post_apply = (
                    await runtime.verified_only_apply.execute(
                        VerifiedOnlyApplyRequest(
                            run_id=run_id,
                            target=target,
                            candidate=selected_candidate,
                            **apply_kwargs,
                        )
                    )
                ).report
        else:
            if runtime.auto_apply_override is not None:
                post_apply = await runtime.auto_apply_override(
                    run_id, target, selected_candidate, **apply_kwargs
                )
            else:
                if runtime.auto_apply is None:
                    raise ValueError(
                        "auto_verified apply policy requires post_apply_evaluator"
                    )
                post_apply = (
                    await runtime.auto_apply.execute(
                        ApplyTransactionRequest(
                            run_id=run_id,
                            target=target,
                            candidate=selected_candidate,
                            **apply_kwargs,
                        )
                    )
                ).report
        final_status = settle_post_apply_status(final_status, post_apply)
    if inferred_draft_creation:
        published = (
            apply_policy == "auto_verified"
            and post_apply is not None
            and (post_apply.get("status") == "accepted")
        )
        if selected_candidate is not None and (not published):
            try:
                if isinstance(target, DraftSkillTextTarget):
                    target.preserve_selected_draft(selected_candidate.content)
            except (FileExistsError, OSError, ValueError) as exc:
                gate_results.append(
                    GateResult(
                        gate_name="draft_persistence",
                        passed=False,
                        reason="selected inferred skill draft could not be persisted",
                        details={
                            "failure_class": "infrastructure",
                            "code": "draft_persistence_failed",
                            "type": type(exc).__name__,
                            "reason": str(exc),
                        },
                    )
                )
                final_status = SelfEvolveRunStatus.FAILED
        runtime_skill_path = _target_runtime_skill_path(target)
        promotion = project_inferred_draft_promotion(
            InferredDraftPromotionRequest(
                policy=runtime.inferred_new_skill_policy,
                apply_policy=apply_policy,
                selected_candidate=selected_candidate,
                post_apply=post_apply,
                draft_path=target.identity.path,
                release_path=str(runtime_skill_path)
                if runtime_skill_path is not None
                else None,
                runtime_registry_refresh_configured=runtime.runtime_registry_refresher
                is not None,
                initial_promotion=promotion,
            )
        )
        if target_selection_report is not None:
            target_selection_report = replace(
                target_selection_report,
                diagnostics=project_target_selection_promotion_diagnostics(
                    target_selection_report.diagnostics, promotion
                ),
            )
            runtime.store.write_target_selection_report(run_id, target_selection_report)
    if optimizer_lineage_paths_by_candidate:
        _persist_lineage_lifecycle(
            optimizer_lineage_paths_by_candidate,
            iteration_states=iteration_states,
            attempt_events=runtime.store.read_all_candidate_attempt_events(run_id),
            selected_candidate_id=reported_selected_candidate.candidate_id
            if reported_selected_candidate is not None
            else None,
            post_apply=post_apply,
        )
    execution_stages = runtime.execution_telemetry.to_report()
    generation_stop_reason = run_state.generation.stop_reason()
    report = {
        "run_id": run_id,
        "target": {
            "target_type": target.identity.target_type,
            "target_id": target.identity.target_id,
            "path": target.identity.path,
        },
        "apply_policy": apply_policy,
        "candidate_ids": [candidate.candidate_id for candidate in all_candidates],
        "selected_candidate_id": reported_selected_candidate.candidate_id
        if reported_selected_candidate is not None
        else None,
        "repair_focus_candidate_id": repair_focus_candidate.candidate_id
        if repair_focus_candidate is not None
        else None,
        "status": final_status.value,
        "target_provenance": target_provenance_report,
        "optimizer_diagnostics": optimizer_diagnostics[0]["diagnostics"]
        if len(optimizer_diagnostics) == 1
        else {"iterations": optimizer_diagnostics},
        "prior_feedback_count": len(prior_feedback),
        "screening_control_preflight": screening_control_preflight,
        "support_specific_control_health": {
            "schema_version": "aworld.self_evolve.support_specific_control_health.v1",
            "identity_fields": [
                "case_id",
                "baseline_skill_fingerprint",
                "capability_package_fingerprint",
                "replay_capability_fingerprint",
                "adaptation_fingerprint",
                "timeout_envelope_fingerprint",
            ],
            "observations": [
                dict(observation)
                for observation in list(
                    runtime._candidate_screening_control_observations.values()
                )[-128:]
            ],
        },
        "iterations": iteration_reports,
        "execution": {
            "stages": execution_stages,
            "total_usage": _execution_usage_report(
                optimizer_diagnostics=optimizer_diagnostics,
                iteration_states=iteration_states,
                stages=execution_stages,
            ),
        },
        "budget": budget_context.to_dict(),
        "regression_evidence": regression_evidence.to_dict()
        if regression_evidence is not None
        else None,
        "challenge_report": challenge_report.to_dict()
        if challenge_report is not None
        else None,
        "composition_prerequisites": [
            {
                "candidate_id": candidate_id,
                "status": "verified_support",
                "next_stage": "target_behavior_composition",
                "inherit_candidate_package": True,
                "evidence": "all_applicable_prerequisite_gates_passed",
            }
            for candidate_id in dict.fromkeys(run_state.prerequisite_candidate_ids)
        ],
        "verification_funnel": run_state.verification_funnel_report(
            VerificationFunnelRequest(
                screening_max_cases=runtime.candidate_screening_max_cases,
                repair_iteration_horizon=iteration_budget,
                candidate_generation_batch_count=len(optimizer_diagnostics),
                max_generated_candidates=runtime.max_generated_candidates,
                repair_reserved_slot_count=repair_reserved_slot_count,
                unique_generated_candidate_count=len(all_candidates),
                policy_filtered_candidate_count=sum(
                    (
                        len(_candidate_policy_filter_outcomes(diagnostics))
                        for diagnostics in _optimizer_iteration_diagnostics(
                            optimizer_diagnostics
                        )
                    )
                ),
                max_authoritative_candidates=runtime.max_full_evaluation_candidates,
                max_score_tiebreak_candidates=runtime.max_score_tiebreak_candidates,
                authoritative_case_observations=runtime._current_run_authoritative_case_observations,
            )
        ),
        "handbook_slice": latest_handbook_slice,
        "repair_frontier_state": _repair_frontier_state_report(
            store=runtime.store,
            target=target.identity,
            current_run_id=run_id,
            allowed_run_ids=campaign_prior_run_ids,
            observed_frontiers=_typed_repair_frontiers(validation_feedback),
            scheduler_state=scheduler_state,
            selected_candidate_id=selected_candidate.candidate_id
            if selected_candidate is not None
            else None,
            run_succeeded=final_status is SelfEvolveRunStatus.SUCCEEDED,
            campaign_id=campaign_id,
            campaign_cycle=campaign_cycle,
        ),
        "regression_suites": [
            suite.spec.to_dict()
            for suite in (
                *runtime.regression_suites,
                *(challenge_report.suites if challenge_report is not None else ()),
            )
        ],
    }
    measurement_report = project_measurement_report(
        MeasurementReportRequest(
            summary=measurement_summary,
            mode=runtime.measurement_mode,
            candidate_prerequisite_blocked=candidate_prerequisite_blocked,
            measurement_prerequisite_blocked=measurement_prerequisite_blocked,
            gate_results=tuple(gate_results),
        ),
        candidate_prerequisite_failure=_gate_has_candidate_prerequisite_failure,
        measurement_materialization_blocked=_gate_blocks_measurement_materialization,
    )
    if measurement_report is not None:
        report["measurement"] = measurement_report
    _emit_progress(
        runtime.progress_callback,
        "lesson_extraction",
        "Extracting lesson memory and harness diagnostics",
    )
    finalization = finalize_terminal_run(
        TerminalFinalizationRequest(
            run_id=run_id,
            target=target.identity,
            final_status=final_status,
            reported_selected_candidate=reported_selected_candidate,
            repair_focus_candidate=repair_focus_candidate,
            apply_policy=apply_policy,
            base_report=report,
            optimizer_diagnostics=tuple(optimizer_diagnostics),
            gate_results=tuple(gate_results),
            scheduler_decisions=tuple(scheduler_decisions),
            population_screening_reports=tuple(population_screening_reports),
            iteration_states=tuple(iteration_states),
            iteration_reports=tuple(iteration_reports),
            generation_stop_reason=generation_stop_reason,
            dataset=dataset,
            all_candidates=tuple(all_candidates),
            replay_candidate_limit=runtime.replay_candidate_limit,
            budget_report=budget_context.to_dict(),
            optimizer_lineage_paths=tuple(optimizer_lineage_paths),
            target_selection_report=target_selection_report,
            post_apply=post_apply,
            promotion=promotion,
            baseline_summary=baseline_summary,
            candidate_summary=candidate_summary,
            held_out_summary=held_out_summary,
            replay_result=replay_result,
            replay_dataset=replay_dataset,
            skill_evolution_progress=skill_evolution_progress,
            trace_packs=tuple(trace_packs),
            candidate_source_dispositions=candidate_source_dispositions,
            deprecated_config_mappings=runtime.deprecated_config_mappings,
            previous_artifact_retention=startup_artifact_retention,
        ),
        runtime=TerminalFinalizationRuntime(
            store=runtime.store,
            terminal_cause=_terminal_cause,
            rejection_attribution=_rejection_attribution,
            resolved_contract_fingerprints=_resolved_conformance_contract_fingerprints,
            campaign_failure_attribution=_campaign_failure_attribution,
            trajectory_set_report=_trajectory_set_report,
            population_report=_population_report,
            no_op_report=_no_op_report,
            replay_report=_replay_report,
            replay_artifact_path=_replay_artifact_path,
            campaign_measurement_outcome=_campaign_measurement_outcome_for_replay,
            replay_capability_report=_replay_capability_report,
            evaluator_report_paths=_evaluator_report_paths,
            acceptance_confidence_report=_acceptance_confidence_report,
            finalize_run_report=services._finalize_run_report,
        ),
    )
    completed_run = finalization.completed_run
    runtime._candidate_screening_loaded_run_ids.add(run_id)
    _emit_progress(
        runtime.progress_callback,
        "completed",
        f"Self-evolve run {run_id} finished with status {completed_run.status.value}",
    )
    return RunTerminalLifecycleResult(
        completed_run=completed_run, selected_candidate=reported_selected_candidate
    )
