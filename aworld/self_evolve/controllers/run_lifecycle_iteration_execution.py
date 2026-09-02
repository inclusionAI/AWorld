"""Typed history, workflow, and iteration steps for an explicit run."""

from __future__ import annotations

from dataclasses import dataclass

from aworld.self_evolve.lessons import extract_lesson_records
from aworld.self_evolve.campaign_policy import (
    is_verified_apply_policy as _is_verified_apply_policy,
)
from aworld.self_evolve.controllers.run_generation_execution import (
    GenerationExecutionPolicy,
    GenerationExecutionRuntime,
    GenerationExecutionState,
)
from aworld.self_evolve.controllers.run_iteration_execution import (
    IterationExecutionPolicy,
    IterationExecutionRequest,
    IterationExecutionRuntime,
    execute_iteration_lifecycle,
)
from aworld.self_evolve.feedback_history import (
    _non_authoritative_candidate_rejection,
)
from aworld.self_evolve.feedback_diagnostics import (
    _typed_gate_feedback_metrics as _typed_gate_feedback_metrics,
)
from aworld.self_evolve.run_history import (
    _load_prior_candidate_package_index,
    _load_prior_rejected_feedback,
    _load_prior_rejected_semantic_lesson_fingerprints,
)
from aworld.self_evolve.target_package import (
    _replayable_user_task_dataset,
    _target_package_inventory,
    _target_package_sources,
)
from aworld.self_evolve.controllers.run_execution import (
    ExplicitTargetRunRequest,
)
from aworld.self_evolve.controllers.run_bootstrap import (
    RunHistoryPolicy,
    RunHistoryRequest,
    RunHistoryRuntime,
    bootstrap_run_history,
)
from aworld.self_evolve.controllers.run_workflow import (
    WorkflowEstimationPolicy,
    WorkflowEstimationRequest,
    estimate_run_workflow,
)
from aworld.self_evolve.controllers.run_terminal_lifecycle import (
    RunTerminalLifecycleRequest,
)





from aworld.self_evolve.controllers.run_phase_context import RunPhaseContext
from aworld.self_evolve.controllers.run_phase_assembly import RunPhaseExecutions
from aworld.self_evolve.controllers.run_lifecycle_bootstrap_execution import RunLifecycleBootstrapResult
from aworld.self_evolve.controllers.run_workflow import WorkflowEstimationResult
from aworld.self_evolve.controllers.run_bootstrap import RunHistoryResult

_MAX_PROGRESS_REPAIR_EXTENSION_ITERATIONS = 6

@dataclass(frozen=True)
class RunIterationPreparation:
    request: ExplicitTargetRunRequest
    bootstrap: RunLifecycleBootstrapResult
    history: RunHistoryResult
    workflow: WorkflowEstimationResult
    generation_policy: GenerationExecutionPolicy
    generation_runtime: GenerationExecutionRuntime
    generation_state: GenerationExecutionState


def prepare_lifecycle_iteration(
    *, request: ExplicitTargetRunRequest, bootstrap: RunLifecycleBootstrapResult,
    context: RunPhaseContext,
) -> RunIterationPreparation:
    run_id = request.run_id
    target = request.target
    dataset = request.dataset
    trace_packs = request.trace_packs
    apply_policy = request.apply_policy
    campaign_prior_run_ids = request.campaign_prior_run_ids
    screening_control_preflight = bootstrap.bootstrap.screening_control_preflight
    scheduler = bootstrap.bootstrap.scheduler
    scheduler_state = bootstrap.bootstrap.scheduler_state
    scheduler_decisions = bootstrap.bootstrap.scheduler_decisions
    budget_context = bootstrap.bootstrap.budget_context
    attempt_tracker = bootstrap.attempt_tracker
    history = bootstrap_run_history(
        RunHistoryRequest(
            run_id=run_id,
            target=target,
            dataset=dataset,
            trace_packs=trace_packs,
            apply_policy=apply_policy,
            campaign_prior_run_ids=campaign_prior_run_ids,
            screening_control_preflight=screening_control_preflight,
            policy=RunHistoryPolicy(
                min_score_delta=context.construction.policy.min_score_delta,
                min_eval_cases=context.construction.policy.min_eval_cases,
                judge_repetitions=context.construction.policy.judge_repetitions,
                candidate_screening_max_cases=(context.construction.replay.candidate_screening_max_cases),
                max_generated_candidates=context.construction.replay.max_generated_candidates,
                max_full_evaluation_candidates=(
                    context.construction.replay.max_full_evaluation_candidates
                ),
                max_score_tiebreak_candidates=(context.construction.replay.max_score_tiebreak_candidates),
                replay_enabled=context.construction.replay.replay_enabled,
                baseline_replay_repetitions=(context.construction.replay.baseline_replay_repetitions),
                candidate_replay_repetitions=(context.construction.replay.candidate_replay_repetitions),
                replay_stability_margin=context.construction.replay.replay_stability_margin,
                replay_timeout_seconds=context.construction.replay.replay_timeout_seconds,
                replay_total_timeout_seconds=(context.construction.replay.replay_total_timeout_seconds),
                measurement_mode=context.construction.measurement.mode.value,
                measurement_primary_metric=context.construction.measurement.primary_metric,
                measurement_minimum_effect=context.construction.measurement.minimum_effect,
                measurement_confidence_level=(context.construction.measurement.confidence_level),
                measurement_min_independent_cases=(
                    context.construction.measurement.minimum_independent_cases
                ),
                measurement_early_stop_policy=(context.construction.measurement.early_stop_policy),
            ),
            runtime=RunHistoryRuntime(
                store=context.construction.runtime.store,
                replay_adaptation_compiler=context.construction.runtime.replay_adaptation_compiler,
                load_prior_rejected_feedback=_load_prior_rejected_feedback,
                extract_lesson_records=extract_lesson_records,
                non_authoritative_candidate_rejection=(
                    _non_authoritative_candidate_rejection
                ),
                load_prior_candidate_package_index=(
                    _load_prior_candidate_package_index
                ),
                load_prior_rejected_semantic_lesson_fingerprints=(
                    _load_prior_rejected_semantic_lesson_fingerprints
                ),
                replayable_user_task_dataset=(_replayable_user_task_dataset),
                target_package_inventory=_target_package_inventory,
                target_package_sources=_target_package_sources,
                is_verified_apply_policy=_is_verified_apply_policy,
            ),
        )
    )
    initial = history.iteration
    restored = history.restored

    workflow_estimation = estimate_run_workflow(
        WorkflowEstimationRequest(
            dataset=dataset,
            apply_policy=apply_policy,
            regression_suites=context.construction.runtime.regression_suites,
            policy=WorkflowEstimationPolicy(
                max_iterations=context.construction.policy.max_iterations,
                replay_enabled=context.construction.replay.replay_enabled,
                replay_backend_available=(
                    context.construction.runtime.candidate_replay_backend is not None
                ),
                repetitions_explicit=context.construction.replay.replay_repetitions_explicit,
                minimum_independent_cases=(context.construction.measurement.minimum_independent_cases),
                baseline_repetitions=context.construction.replay.baseline_replay_repetitions,
                candidate_repetitions=context.construction.replay.candidate_replay_repetitions,
                evaluation_backend_available=(context.construction.runtime.evaluation_backend is not None),
                judge_repetitions=context.construction.policy.judge_repetitions,
                progress_repair_extension_iterations=(
                    _MAX_PROGRESS_REPAIR_EXTENSION_ITERATIONS
                ),
            ),
            replayable_dataset=_replayable_user_task_dataset,
        )
    )
    repair_workflow_budget_items = workflow_estimation.budget_items

    generation_execution_state = GenerationExecutionState(
        scheduler_state=scheduler_state,
        validation_feedback=initial.validation_feedback,
        fresh_evaluation_required=initial.fresh_evaluation_required,
        latest_handbook_slice=initial.latest_handbook_slice,
        all_candidates=initial.all_candidates,
        candidate_source_dispositions=initial.candidate_source_dispositions,
        optimizer_diagnostics=initial.optimizer_diagnostics,
        optimizer_lineage_paths=initial.optimizer_lineage_paths,
        optimizer_lineage_paths_by_candidate=(
            initial.optimizer_lineage_paths_by_candidate
        ),
        scheduler_decisions=scheduler_decisions,
        iteration_reports=initial.iteration_reports,
        iteration_states=initial.iteration_states,
        gate_results=initial.gate_results,
        canonical_candidate_id_by_package=(
            restored.canonical_candidate_id_by_package
        ),
        package_fingerprint_by_candidate_id=(
            restored.package_fingerprint_by_candidate_id
        ),
        current_run_candidate_id_by_package=(
            initial.current_run_candidate_id_by_package
        ),
        current_run_package_fingerprint_by_candidate_id=(
            initial.current_run_package_fingerprint_by_candidate_id
        ),
        current_run_candidate_id_by_semantic_package=(
            initial.current_run_candidate_id_by_semantic_package
        ),
        attempt_key_by_candidate_id=initial.attempt_key_by_candidate_id,
    )
    generation_execution_policy = GenerationExecutionPolicy(
        max_iterations=context.construction.policy.max_iterations,
        max_generated_candidates=context.construction.replay.max_generated_candidates,
        max_full_evaluation_candidates=(context.construction.replay.max_full_evaluation_candidates),
        replay_candidate_limit=context.construction.replay.replay_candidate_limit,
        replay_enabled=context.construction.replay.replay_enabled,
        candidate_screening_max_cases=context.construction.replay.candidate_screening_max_cases,
    )
    generation_execution_runtime = GenerationExecutionRuntime(
        store=context.construction.runtime.store,
        optimizer=context.construction.runtime.optimizer,
        generation_controller=context.construction.controllers.generation,
        execution_telemetry=context.state.execution_telemetry,
        scheduler=scheduler,
        budget_context=budget_context,
        attempt_tracker=attempt_tracker,
        repair_workflow_budget_items=repair_workflow_budget_items,
        progress_callback=context.construction.runtime.progress_callback,
        skill_evolution_contract=context.construction.runtime.skill_evolution_contract,
        candidate_replay_backend=context.construction.runtime.candidate_replay_backend,
        verification_contract_fingerprint=(
            context.services.verification_contract_fingerprint
        ),
    )

    return RunIterationPreparation(
        request=request, bootstrap=bootstrap, history=history, workflow=workflow_estimation,
        generation_policy=generation_execution_policy,
        generation_runtime=generation_execution_runtime,
        generation_state=generation_execution_state,
    )


async def execute_lifecycle_iteration(
    preparation: RunIterationPreparation, *, context: RunPhaseContext,
    phases: RunPhaseExecutions,
) -> RunTerminalLifecycleRequest:
    request = preparation.request
    bootstrap = preparation.bootstrap
    history = preparation.history
    initial = history.iteration
    restored = history.restored
    workflow_estimation = preparation.workflow
    run_id = request.run_id
    target = request.target
    dataset = request.dataset
    trace_packs = request.trace_packs
    apply_policy = request.apply_policy
    campaign_prior_run_ids = request.campaign_prior_run_ids
    campaign_id = request.campaign_id
    campaign_cycle = request.campaign_cycle
    target_provenance = bootstrap.target_provenance
    target_provenance_unresolved_reason = bootstrap.target_provenance_unresolved_reason
    target_selection_report = bootstrap.target_selection_report
    target_provenance_report = bootstrap.target_provenance_report
    screening_control_preflight = bootstrap.bootstrap.screening_control_preflight
    startup_artifact_retention = bootstrap.startup_artifact_retention
    scheduler_decisions = bootstrap.bootstrap.scheduler_decisions
    budget_context = bootstrap.bootstrap.budget_context
    attempt_tracker = bootstrap.attempt_tracker
    iteration_budget = workflow_estimation.iteration_budget
    estimated_baseline_repetitions = workflow_estimation.estimated_baseline_repetitions
    generation_execution_policy = preparation.generation_policy
    generation_execution_runtime = preparation.generation_runtime
    generation_execution_state = preparation.generation_state
    iteration_execution = await execute_iteration_lifecycle(
        IterationExecutionRequest(
            run_id=run_id,
            target=target,
            dataset=dataset,
            trace_packs=trace_packs,
            apply_policy=apply_policy,
            target_provenance=target_provenance,
            target_provenance_unresolved_reason=(
                target_provenance_unresolved_reason
            ),
            target_selection_report=target_selection_report,
            prior_feedback=restored.prior_feedback,
            generation_lesson_records=restored.generation_lesson_records,
            rejected_candidate_ids=restored.rejected_candidate_ids,
            accepted_candidate_ids=restored.accepted_candidate_ids,
            current_run_attempted_candidate_ids=(
                initial.current_run_attempted_candidate_ids
            ),
            rejected_semantic_lesson_fingerprints=(
                restored.rejected_semantic_lesson_fingerprints
            ),
            repair_reserved_slot_count=(history.repair_reserved_slot_count),
            replay_preflight=restored.replay_preflight,
            target_package_inventory=restored.target_package_inventory,
            target_package_sources=restored.target_package_sources,
            verification_settings=history.verification_settings,
            estimated_baseline_repetitions=(estimated_baseline_repetitions),
            iteration_budget=iteration_budget,
            run_state=initial.run_state,
            attempt_tracker=attempt_tracker,
            budget_context=budget_context,
            generation_execution_policy=generation_execution_policy,
            generation_execution_runtime=generation_execution_runtime,
            generation_execution_state=generation_execution_state,
            population_screening_reports=initial.population_screening_reports,
        ),
        IterationExecutionRuntime(
            store=context.construction.runtime.store,
            optimizer=context.construction.runtime.optimizer,
            candidate_replay_backend=context.construction.runtime.candidate_replay_backend,
            progress_callback=context.construction.runtime.progress_callback,
            _plan_candidate_measurement=phases.operations.plan_candidate_measurement,
            _screen_candidate_population=phases.operations.screen_candidate_population,
            _prepare_replay_adaptation=phases.operations.prepare_replay_adaptation,
            _execute_iteration_candidate=(phases.operations.execute_iteration_candidate),
            _baseline_reuse_provenance=phases.operations.baseline_reuse_provenance,
            _candidate_screening_case_observations=(
                context.construction.mutable.candidate_screening_case_observations
            ),
            _candidate_screening_control_observations=(
                context.construction.mutable.candidate_screening_control_observations
            ),
            _current_run_authoritative_case_observations=(
                context.construction.mutable.current_run_authoritative_case_observations
            ),
            _measurement_experiments=context.construction.measurement.experiments,
            _measurement_summaries=context.construction.measurement.summaries,
        ),
        IterationExecutionPolicy(
            allow_external_target_mutation=(context.construction.policy.allow_external_target_mutation),
            allow_generated_target_mutation=(context.construction.policy.allow_generated_target_mutation),
            inferred_new_skill_policy=context.construction.policy.inferred_new_skill_policy,
            max_full_evaluation_candidates=(context.construction.replay.max_full_evaluation_candidates),
            max_score_tiebreak_candidates=(context.construction.replay.max_score_tiebreak_candidates),
            measurement_early_stop_policy=(context.construction.measurement.early_stop_policy),
            measurement_mode=context.construction.measurement.mode,
            replay_enabled=context.construction.replay.replay_enabled,
            _active_target_intent=context.state.active_target_intent,
        ),
    )
    validation_feedback = iteration_execution.validation_feedback
    fresh_evaluation_required = iteration_execution.fresh_evaluation_required
    latest_handbook_slice = iteration_execution.latest_handbook_slice
    scheduler_state = iteration_execution.scheduler_state
    shared_validation_gate = iteration_execution.shared_validation_gate
    terminal_request = RunTerminalLifecycleRequest(
    run_id=run_id,
    target=target,
    dataset=dataset,
    trace_packs=trace_packs,
    apply_policy=apply_policy,
    campaign_prior_run_ids=campaign_prior_run_ids,
    campaign_id=campaign_id,
    campaign_cycle=campaign_cycle,
    target_provenance_report=target_provenance_report,
    target_selection_report=target_selection_report,
    screening_control_preflight=screening_control_preflight,
    startup_artifact_retention=startup_artifact_retention,
    prior_feedback=restored.prior_feedback,
    validation_feedback=validation_feedback,
    all_candidates=initial.all_candidates,
    candidate_source_dispositions=(initial.candidate_source_dispositions),
    fresh_evaluation_required=fresh_evaluation_required,
    optimizer_diagnostics=initial.optimizer_diagnostics,
    optimizer_lineage_paths=initial.optimizer_lineage_paths,
    optimizer_lineage_paths_by_candidate=(
        initial.optimizer_lineage_paths_by_candidate
    ),
    iteration_reports=initial.iteration_reports,
    iteration_states=initial.iteration_states,
    population_screening_reports=(initial.population_screening_reports),
    scheduler_decisions=scheduler_decisions,
    scheduler_state=scheduler_state,
    budget_context=budget_context,
    run_state=initial.run_state,
    iteration_budget=iteration_budget,
    repair_reserved_slot_count=(history.repair_reserved_slot_count),
    selected_candidate=initial.selected_candidate,
    baseline_summary=initial.baseline_summary,
    candidate_summary=initial.candidate_summary,
    held_out_summary=initial.held_out_summary,
    measurement_summary=initial.measurement_summary,
    regression_evidence=initial.regression_evidence,
    challenge_report=initial.challenge_report,
    replay_result=initial.replay_result,
    replay_dataset=initial.replay_dataset,
    latest_handbook_slice=latest_handbook_slice,
    shared_validation_gate=shared_validation_gate,
    gate_results=initial.gate_results,
    attempt_tracker=attempt_tracker,
)
    return terminal_request
