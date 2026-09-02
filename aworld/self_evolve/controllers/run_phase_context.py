"""Typed shared context and compatibility boundary for run phase components."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, fields
from typing import Any

from aworld.self_evolve.controllers.run_configuration import RunnerConstructionResult


@dataclass
class RunLifecyclePublishedState:
    """Mutable startup state shared with the public facade without callbacks."""

    run_budget_ledger: object | None = None
    execution_telemetry: object | None = None


@dataclass(frozen=True)
class RunLifecycleServices:
    artifact_retention_report: Callable[..., dict[str, object]]
    finalize_run_report: Callable[..., Any]
    load_prior_scheduler_state: Callable[..., Any]
    screening_control_preflight: Callable[..., Any]
    control_qualification_identity: Callable[..., Any]
    preflight_frozen_replay_capability: Callable[..., Any]
    evaluate_candidate_source_conformance: Callable[..., Any]
    create_candidate_skill_overlay: Callable[..., Any]
    evaluate_compiled_probe_conformance: Callable[..., Any]
    frozen_replay_fixture_shape_fingerprints: Callable[..., Any]
    replay_capability_fixture_leaf_values: Callable[..., Any]
    replay_capability_fixture_response_leaf_values: Callable[..., Any]
    verification_contract_fingerprint: Callable[..., str]
    replay_evaluator_admission_gate: Callable[..., Any]


@dataclass(frozen=True)
class RunCompatibilityOverrides:
    """Only genuine Runner subclass/instance overrides cross the facade edge."""

    _screen_candidate_population: Callable[..., Any] | None = None
    _execute_screen_candidate_population: Callable[..., Any] | None = None
    _replay_adaptation_execution: Callable[..., Any] | None = None
    _repair_conformance_preflight_runtime: Callable[..., Any] | None = None
    _repair_conformance_preflight_override: Callable[..., Any] | None = None
    _repair_conformance_population_runtime: Callable[..., Any] | None = None
    _capability_validation_runtime: Callable[..., Any] | None = None
    _validate_candidate_repair_conformance_population: Callable[..., Any] | None = None
    _preflight_candidate_repair_conformance: Callable[..., Any] | None = None
    _load_measurement_resume_request: Callable[..., Any] | None = None
    _plan_candidate_measurement: Callable[..., Any] | None = None
    _materialize_candidate_measurement: Callable[..., Any] | None = None
    _measurement_search_projection_execution: Callable[..., Any] | None = None
    _attach_measurement_search_performance: Callable[..., Any] | None = None
    _evaluate_iteration_candidate: Callable[..., Any] | None = None
    _candidate_iteration_execution: Callable[..., Any] | None = None
    _execute_iteration_candidate: Callable[..., Any] | None = None
    _validate_candidate_capabilities: Callable[..., Any] | None = None
    _prepare_replay_adaptation: Callable[..., Any] | None = None
    _baseline_reuse_provenance: Callable[..., Any] | None = None
    _compile_authoritative_measurement_plan: Callable[..., Any] | None = None
    _paired_replay_runtime: Callable[..., Any] | None = None
    _replay_selected_candidate: Callable[..., Any] | None = None
    _challenge_execution: Callable[..., Any] | None = None
    _regression_execution: Callable[..., Any] | None = None
    _evaluate_independent_regression: Callable[..., Any] | None = None
    _prepare_challenge_suites: Callable[..., Any] | None = None
    _auto_apply_execution: Callable[..., Any] | None = None
    _apply_auto_verified: Callable[..., Any] | None = None
    _verified_only_apply_execution: Callable[..., Any] | None = None
    _apply_verified_only: Callable[..., Any] | None = None

    def get(self, name: str) -> Callable[..., Any] | None:
        if name not in {item.name for item in fields(self)}:
            raise KeyError(name)
        return getattr(self, name)


@dataclass
class RunLifecycleState:
    execution_telemetry: object
    active_target_intent: object | None
    screening_observation_dataset_fingerprint: str | None
    run_budget_ledger: object | None = None


@dataclass
class RunPhaseContext:
    """Small context retaining construction as cohesive nested typed bundles."""

    construction: RunnerConstructionResult
    services: RunLifecycleServices
    published_state: RunLifecyclePublishedState
    overrides: RunCompatibilityOverrides
    state: RunLifecycleState
    operations: RunPhaseOperations | None = None

    def require_operations(self) -> RunPhaseOperations:
        if self.operations is None:
            raise RuntimeError("run phase operations are not assembled")
        return self.operations


@dataclass(frozen=True)
class RunPhaseOperations:
    screen_candidate_population: Callable[..., Any]
    execute_screen_candidate_population: Callable[..., Any]
    replay_adaptation_execution: Callable[..., Any]
    repair_conformance_preflight_runtime: Callable[..., Any]
    repair_conformance_preflight_override: Callable[..., Any]
    repair_conformance_population_runtime: Callable[..., Any]
    validate_candidate_repair_conformance_population: Callable[..., Any]
    preflight_candidate_repair_conformance: Callable[..., Any]
    capability_validation_runtime: Callable[..., Any]
    load_measurement_resume_request: Callable[..., Any]
    plan_candidate_measurement: Callable[..., Any]
    materialize_candidate_measurement: Callable[..., Any]
    measurement_search_projection_execution: Callable[..., Any]
    attach_measurement_search_performance: Callable[..., Any]
    evaluate_iteration_candidate: Callable[..., Any]
    candidate_iteration_execution: Callable[..., Any]
    execute_iteration_candidate: Callable[..., Any]
    validate_candidate_capabilities: Callable[..., Any]
    prepare_replay_adaptation: Callable[..., Any]
    baseline_reuse_provenance: Callable[..., Any]
    compile_authoritative_measurement_plan: Callable[..., Any]
    paired_replay_runtime: Callable[..., Any]
    replay_selected_candidate: Callable[..., Any]
    challenge_execution: Callable[..., Any]
    regression_execution: Callable[..., Any]
    evaluate_independent_regression: Callable[..., Any]
    prepare_challenge_suites: Callable[..., Any]
    auto_apply_execution: Callable[..., Any]
    apply_auto_verified: Callable[..., Any]
    verified_only_apply_execution: Callable[..., Any]
    apply_verified_only: Callable[..., Any]
