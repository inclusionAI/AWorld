"""Typed terminal runtime assembly for an explicit run."""

from __future__ import annotations


from aworld.self_evolve.feedback_diagnostics import (
    _typed_gate_feedback_metrics as _typed_gate_feedback_metrics,
)
from aworld.self_evolve.controllers.run_terminal_lifecycle import (
    RunTerminalLifecycleRequest,
    RunTerminalLifecycleResult,
    RunTerminalLifecycleRuntime,
    RunTerminalLifecycleServices,
    execute_terminal_lifecycle,
)





from aworld.self_evolve.controllers.run_phase_context import RunPhaseContext
from aworld.self_evolve.controllers.run_phase_assembly import RunPhaseExecutions


async def execute_lifecycle_terminal(
    request: RunTerminalLifecycleRequest, *, context: RunPhaseContext,
    phases: RunPhaseExecutions,
) -> RunTerminalLifecycleResult:
    return await execute_terminal_lifecycle(
        request,
            RunTerminalLifecycleRuntime(
                store=context.construction.runtime.store,
                progress_callback=context.construction.runtime.progress_callback,
                execution_telemetry=context.state.execution_telemetry,
                measurement_mode=context.construction.measurement.mode,
                _measurement_experiments=context.construction.measurement.experiments,
                _candidate_screening_control_observations=(
                    context.construction.mutable.candidate_screening_control_observations
                ),
                _current_run_authoritative_case_observations=(
                    context.construction.mutable.current_run_authoritative_case_observations
                ),
                _candidate_screening_loaded_run_ids=(
                    context.construction.mutable.candidate_screening_loaded_run_ids
                ),
                _active_target_intent=context.state.active_target_intent,
                inferred_new_skill_policy=context.construction.policy.inferred_new_skill_policy,
                skill_evolution_contract=context.construction.runtime.skill_evolution_contract,
                runtime_registry_refresher=context.construction.runtime.runtime_registry_refresher,
                runtime_registry_compensator=context.construction.runtime.runtime_registry_compensator,
                runtime_skill_compensator=context.construction.runtime.runtime_skill_compensator,
                candidate_screening_max_cases=context.construction.replay.candidate_screening_max_cases,
                max_generated_candidates=context.construction.replay.max_generated_candidates,
                max_full_evaluation_candidates=(context.construction.replay.max_full_evaluation_candidates),
                max_score_tiebreak_candidates=(context.construction.replay.max_score_tiebreak_candidates),
                replay_candidate_limit=context.construction.replay.replay_candidate_limit,
                regression_suites=tuple(context.construction.runtime.regression_suites),
                deprecated_config_mappings=context.construction.budget.deprecated_config_mappings,
                measurement_controller=context.construction.controllers.measurement,
                measurement_search_projection=(
                    phases.operations.measurement_search_projection_execution()
                ),
                auto_apply=(
                    phases.operations.auto_apply_execution()
                    if context.construction.runtime.post_apply_evaluator is not None
                    else None
                ),
                verified_only_apply=phases.operations.verified_only_apply_execution(),
                auto_apply_override=context.overrides._apply_auto_verified,
                verified_only_apply_override=context.overrides._apply_verified_only,
            ),
            RunTerminalLifecycleServices(
                _finalize_run_report=context.services.finalize_run_report,
            ),
        )
