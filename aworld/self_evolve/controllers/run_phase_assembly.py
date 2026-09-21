"""Composition root for cohesive explicit-run phase factories."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from aworld.self_evolve.controllers.run_apply_phases import ApplyPhaseFactory
from aworld.self_evolve.controllers.run_candidate_phases import CandidatePhaseFactory
from aworld.self_evolve.controllers.run_configuration import RunnerConstructionResult
from aworld.self_evolve.controllers.run_measurement_phases import MeasurementPhaseFactory
from aworld.self_evolve.controllers.run_phase_context import (
    RunCompatibilityOverrides,
    RunLifecyclePublishedState,
    RunLifecycleServices,
    RunLifecycleState,
    RunPhaseContext,
    RunPhaseOperations,
)
from aworld.self_evolve.controllers.run_screening_phases import ScreeningPhaseFactory
from aworld.self_evolve.controllers.screening_execution import (
    execute_screen_candidate_population,
)


@dataclass(frozen=True)
class RunPhaseExecutions:
    """Small composed facade; substantive behavior remains in phase owners."""

    context: RunPhaseContext
    screening: ScreeningPhaseFactory
    measurement: MeasurementPhaseFactory
    candidate: CandidatePhaseFactory
    apply: ApplyPhaseFactory
    operations: RunPhaseOperations


def assemble_run_phases(
    construction: RunnerConstructionResult,
    *,
    services: RunLifecycleServices,
    published_state: RunLifecyclePublishedState,
    overrides: RunCompatibilityOverrides,
    execution_telemetry: object,
    active_target_intent: object | None,
    screening_observation_dataset_fingerprint: str | None,
) -> RunPhaseExecutions:
    state = RunLifecycleState(
        execution_telemetry=execution_telemetry,
        active_target_intent=active_target_intent,
        screening_observation_dataset_fingerprint=(
            screening_observation_dataset_fingerprint
        ),
    )
    context = RunPhaseContext(
        construction=construction,
        services=services,
        published_state=published_state,
        overrides=overrides,
        state=state,
    )
    screening = ScreeningPhaseFactory(context)
    measurement = MeasurementPhaseFactory(context)
    candidate = CandidatePhaseFactory(context)
    apply = ApplyPhaseFactory(context)

    def selected(name: str, default: Callable[..., Any]) -> Callable[..., Any]:
        return overrides.get(name) or default

    operations = RunPhaseOperations(
        screen_candidate_population=selected(
            "_screen_candidate_population", screening._screen_candidate_population
        ),
        execute_screen_candidate_population=selected(
            "_execute_screen_candidate_population", execute_screen_candidate_population
        ),
        replay_adaptation_execution=selected(
            "_replay_adaptation_execution", screening._replay_adaptation_execution
        ),
        repair_conformance_preflight_runtime=selected(
            "_repair_conformance_preflight_runtime",
            screening._repair_conformance_preflight_runtime,
        ),
        repair_conformance_preflight_override=selected(
            "_repair_conformance_preflight_override",
            screening._repair_conformance_preflight_override,
        ),
        repair_conformance_population_runtime=selected(
            "_repair_conformance_population_runtime",
            screening._repair_conformance_population_runtime,
        ),
        validate_candidate_repair_conformance_population=selected(
            "_validate_candidate_repair_conformance_population",
            screening._validate_candidate_repair_conformance_population,
        ),
        preflight_candidate_repair_conformance=selected(
            "_preflight_candidate_repair_conformance",
            screening._preflight_candidate_repair_conformance,
        ),
        capability_validation_runtime=selected(
            "_capability_validation_runtime",
            screening._capability_validation_runtime,
        ),
        load_measurement_resume_request=selected(
            "_load_measurement_resume_request",
            measurement._load_measurement_resume_request,
        ),
        plan_candidate_measurement=selected(
            "_plan_candidate_measurement", measurement._plan_candidate_measurement
        ),
        materialize_candidate_measurement=selected(
            "_materialize_candidate_measurement",
            measurement._materialize_candidate_measurement,
        ),
        measurement_search_projection_execution=selected(
            "_measurement_search_projection_execution",
            measurement._measurement_search_projection_execution,
        ),
        attach_measurement_search_performance=selected(
            "_attach_measurement_search_performance",
            measurement._attach_measurement_search_performance,
        ),
        evaluate_iteration_candidate=selected(
            "_evaluate_iteration_candidate",
            measurement._evaluate_iteration_candidate,
        ),
        candidate_iteration_execution=selected(
            "_candidate_iteration_execution", candidate._candidate_iteration_execution
        ),
        execute_iteration_candidate=selected(
            "_execute_iteration_candidate", candidate._execute_iteration_candidate
        ),
        validate_candidate_capabilities=selected(
            "_validate_candidate_capabilities",
            candidate._validate_candidate_capabilities,
        ),
        prepare_replay_adaptation=selected(
            "_prepare_replay_adaptation", candidate._prepare_replay_adaptation
        ),
        baseline_reuse_provenance=selected(
            "_baseline_reuse_provenance", candidate._baseline_reuse_provenance
        ),
        compile_authoritative_measurement_plan=selected(
            "_compile_authoritative_measurement_plan",
            candidate._compile_authoritative_measurement_plan,
        ),
        paired_replay_runtime=selected(
            "_paired_replay_runtime", candidate._paired_replay_runtime
        ),
        replay_selected_candidate=selected(
            "_replay_selected_candidate", candidate._replay_selected_candidate
        ),
        challenge_execution=selected(
            "_challenge_execution", candidate._challenge_execution
        ),
        regression_execution=selected(
            "_regression_execution", candidate._regression_execution
        ),
        evaluate_independent_regression=selected(
            "_evaluate_independent_regression",
            candidate._evaluate_independent_regression,
        ),
        prepare_challenge_suites=selected(
            "_prepare_challenge_suites", candidate._prepare_challenge_suites
        ),
        auto_apply_execution=selected(
            "_auto_apply_execution", apply._auto_apply_execution
        ),
        apply_auto_verified=selected(
            "_apply_auto_verified", apply._apply_auto_verified
        ),
        verified_only_apply_execution=selected(
            "_verified_only_apply_execution", apply._verified_only_apply_execution
        ),
        apply_verified_only=selected(
            "_apply_verified_only", apply._apply_verified_only
        ),
    )
    context.operations = operations
    return RunPhaseExecutions(
        context=context,
        screening=screening,
        measurement=measurement,
        candidate=candidate,
        apply=apply,
        operations=operations,
    )
