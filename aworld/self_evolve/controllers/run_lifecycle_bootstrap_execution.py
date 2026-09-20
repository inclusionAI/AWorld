"""Typed bootstrap and target-selection steps for an explicit run."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from aworld.self_evolve.credit_assignment import (
    TargetSelectionDecision,
    build_target_selection_decision,
    build_default_target_inventory,
)
from aworld.self_evolve.gates import (
    StoppingConditionGate,
    StoppingConditionState,
)
from aworld.self_evolve.campaign_policy import (
    is_verified_apply_policy as _is_verified_apply_policy,
)
from aworld.self_evolve.target_selection_support import (
    explicit_target_selection_report as _explicit_target_selection_report,
)
from aworld.self_evolve.feedback_diagnostics import (
    _typed_gate_feedback_metrics as _typed_gate_feedback_metrics,
)
from aworld.self_evolve.iteration_selection import (
    _candidate_generation_limit,
)
from aworld.self_evolve.screening_observation_history import (
    _restore_campaign_screening_case_observations,
    _restore_historical_screening_lifecycle_observations,
    _screening_control_harness_fingerprint,
    _screening_observation_scope_fingerprint,
)
from aworld.self_evolve.controllers.run_execution import (
    ExplicitTargetRunRequest,
)
from aworld.self_evolve.controllers.run_budget_support import (
    _execution_usage_report,
    backend_proves_zero_budget_usage as _backend_proves_zero_budget_usage,
)
from aworld.self_evolve.controllers.run_bootstrap import (
    RunBootstrapPolicy,
    RunBootstrapRequest,
    RunBootstrapRuntime,
    RunBootstrapState,
    bootstrap_explicit_target_run,
)
from aworld.self_evolve.controllers.run_resources import (
    CandidateAttemptTracker as _CandidateAttemptTracker,
    RunBudgetContext as _RunBudgetContext,
    RunFailureCleanup as _RunFailureCleanup,
)
from aworld.self_evolve.controllers.screening_helpers import (
    _MAX_CANDIDATE_SCREENING_TIMEOUT_SECONDS,
)
from aworld.self_evolve.controllers.screening_execution import (
    _emit_progress,
)
from aworld.self_evolve.provenance import (
    TargetProvenanceResolution,
    TargetProvenanceStatus,
    TargetSelectionOrigin,
    resolve_target_provenance,
)
from aworld.self_evolve.types import (
    SelfEvolveRun,
    SelfEvolveRunStatus,
    to_json_dict,
)





from aworld.self_evolve.controllers.run_phase_context import RunPhaseContext
from aworld.self_evolve.controllers.run_bootstrap import RunBootstrapResult
from aworld.self_evolve.credit_assignment import TargetSelectionReport
from aworld.self_evolve.provenance import TargetProvenance

@dataclass(frozen=True)
class RunLifecycleBootstrapResult:
    bootstrap: RunBootstrapResult
    target_selection_report: TargetSelectionReport | None
    target_provenance: TargetProvenance | None
    target_provenance_unresolved_reason: str | None
    target_provenance_report: dict[str, object]
    startup_artifact_retention: dict[str, object]
    attempt_tracker: _CandidateAttemptTracker
    early_run: SelfEvolveRun | None = None


@dataclass(frozen=True)
class RunTargetSelectionResult:
    report: TargetSelectionReport | None
    provenance: TargetProvenance | None
    provenance_unresolved_reason: str | None
    provenance_resolution: TargetProvenanceResolution


def resolve_run_target_selection(
    request: ExplicitTargetRunRequest,
    *,
    context: RunPhaseContext,
) -> RunTargetSelectionResult:
    target = request.target
    dataset = request.dataset
    trace_packs = request.trace_packs
    apply_policy = request.apply_policy
    target_selection_report = request.target_selection_report
    target_provenance = request.target_provenance
    target_selection_decision = request.target_selection_decision
    if apply_policy not in {"proposal", "auto_verified", "verified_only"}:
        raise ValueError(f"unsupported apply policy: {apply_policy}")
    if context.construction.runtime.skill_evolution_contract is not None:
        if not _is_verified_apply_policy(apply_policy):
            raise ValueError(
                "skill evolution contract requires a verified apply policy"
            )
        context.construction.runtime.skill_evolution_contract.validate_run(
            target_type=target.identity.target_type,
            target_id=target.identity.target_id,
            dataset_case_ids=tuple(case.case_id for case in dataset.cases),
        )
    supplied_provenance = target_provenance
    supplied_decision = target_selection_decision
    if target_selection_decision is None and target_selection_report is None:
        target_selection_report = _explicit_target_selection_report(
            target.identity,
            trace_packs,
        )
    if target_selection_decision is not None:
        target_selection_report = target_selection_decision.report
        selection_origin = target_selection_decision.selection_origin
    elif (
        target_selection_report is not None
        and target_selection_report.selection_origin is not None
    ):
        selection_origin = target_selection_report.selection_origin
    elif target_selection_report is not None:
        selection_origin = TargetSelectionOrigin.UNKNOWN
    else:
        selection_origin = TargetSelectionOrigin.OPERATOR_EXPLICIT

    inventory = build_default_target_inventory(context.construction.runtime.store.workspace_root)
    if target_selection_report is not None:
        selected_target = target_selection_report.selected_target
        if selected_target != target.identity:
            provenance_resolution = TargetProvenanceResolution(
                status=TargetProvenanceStatus.UNRESOLVED,
                provenance=None,
                reason="target selection does not match the executable target",
            )
            target_selection_decision = TargetSelectionDecision(
                report=replace(
                    target_selection_report,
                    provenance_status=provenance_resolution.status,
                    provenance_reason=provenance_resolution.reason,
                    selection_origin=selection_origin,
                ),
                provenance_resolution=provenance_resolution,
                selection_origin=selection_origin,
                target_intent=target_selection_report.target_intent,
            )
        else:
            target_selection_decision = build_target_selection_decision(
                target_selection_report,
                inventory=inventory,
                selection_origin=selection_origin,
                workspace_root=context.construction.runtime.store.workspace_root,
            )
        target_selection_report = target_selection_decision.report
        provenance_resolution = target_selection_decision.provenance_resolution
    else:
        inventory_entries = inventory.find_all(
            target.identity.target_type,
            target.identity.target_id,
        )
        if len(inventory_entries) > 1:
            provenance_resolution = TargetProvenanceResolution(
                status=TargetProvenanceStatus.UNRESOLVED,
                provenance=None,
                reason="inventory contains duplicate target identity",
            )
        else:
            provenance_resolution = resolve_target_provenance(
                target.identity,
                selection_origin=selection_origin,
                inventory_provenance=(
                    inventory_entries[0].provenance if inventory_entries else None
                ),
                workspace_root=context.construction.runtime.store.workspace_root,
            )

    authoritative_resolution = provenance_resolution
    if (
        supplied_decision is not None
        and supplied_decision.provenance_resolution != authoritative_resolution
    ):
        provenance_resolution = TargetProvenanceResolution(
            status=TargetProvenanceStatus.UNRESOLVED,
            provenance=None,
            reason=(
                "supplied target decision does not match authoritative resolution"
            ),
        )

    if supplied_provenance is not None:
        if (
            not authoritative_resolution.resolved
            or authoritative_resolution.provenance != supplied_provenance
        ):
            provenance_resolution = TargetProvenanceResolution(
                status=TargetProvenanceStatus.UNRESOLVED,
                provenance=None,
                reason="supplied provenance does not match authoritative resolution",
            )
        if target_selection_report is not None:
            target_selection_report = replace(
                target_selection_report,
                provenance_status=provenance_resolution.status,
                provenance_reason=provenance_resolution.reason,
            )

    target_provenance = (
        provenance_resolution.provenance if provenance_resolution.resolved else None
    )
    target_provenance_unresolved_reason = (
        None if provenance_resolution.resolved else provenance_resolution.reason
    )
    context.state.active_target_intent = (
        target_selection_decision.target_intent
        if target_selection_decision is not None
        else None
    )
    if target_selection_report is not None and (
        target_selection_report.provenance_status != provenance_resolution.status
        or target_selection_report.provenance_reason != provenance_resolution.reason
    ):
        target_selection_report = replace(
            target_selection_report,
            provenance_status=provenance_resolution.status,
            provenance_reason=provenance_resolution.reason,
        )
    return RunTargetSelectionResult(
        report=target_selection_report,
        provenance=target_provenance,
        provenance_unresolved_reason=target_provenance_unresolved_reason,
        provenance_resolution=provenance_resolution,
    )


def execute_lifecycle_bootstrap(
    *,
    request: ExplicitTargetRunRequest,
    failure_cleanup: _RunFailureCleanup,
    context: RunPhaseContext,
) -> RunLifecycleBootstrapResult:
    run_id = request.run_id
    target = request.target
    dataset = request.dataset
    apply_policy = request.apply_policy

    def register_budget_context(budget_context: _RunBudgetContext) -> None:
        failure_cleanup.register_budget_context(budget_context)
        context.state.run_budget_ledger = budget_context.ledger
        context.published_state.run_budget_ledger = budget_context.ledger

    bootstrap = bootstrap_explicit_target_run(
        RunBootstrapRequest(
            run=request,
            policy=RunBootstrapPolicy(
                total_run_token_budget=context.construction.budget.total_run_token_budget,
                max_run_cost_usd=context.construction.budget.max_run_cost_usd,
                max_run_wall_seconds=context.construction.budget.max_run_wall_seconds,
                ingestion_model_call_count=context.construction.policy.ingestion_model_call_count,
                replay_timeout_seconds=context.construction.replay.replay_timeout_seconds,
                replay_candidate_limit=context.construction.replay.replay_candidate_limit,
                screening_timeout_ceiling_seconds=(
                    _MAX_CANDIDATE_SCREENING_TIMEOUT_SECONDS
                ),
            ),
            state=RunBootstrapState(
                candidate_screening_case_observations=(
                    context.construction.mutable.candidate_screening_case_observations
                ),
                candidate_screening_control_observations=(
                    context.construction.mutable.candidate_screening_control_observations
                ),
                candidate_screening_loaded_run_ids=(
                    context.construction.mutable.candidate_screening_loaded_run_ids
                ),
                current_run_authoritative_case_observations=(
                    context.construction.mutable.current_run_authoritative_case_observations
                ),
                candidate_screening_observation_dataset_fingerprint=(
                    context.state.screening_observation_dataset_fingerprint
                ),
            ),
            runtime=RunBootstrapRuntime(
                store=context.construction.runtime.store,
                optimizer=context.construction.runtime.optimizer,
                challenger_backend=context.construction.runtime.challenger_backend,
                candidate_replay_backend=context.construction.runtime.candidate_replay_backend,
                regression_replay_backend=context.construction.runtime.regression_replay_backend,
                evaluation_backend=context.construction.runtime.evaluation_backend,
                cold_start_by_stage=context.construction.budget.cold_start_by_stage,
                screening_observation_scope_fingerprint=(
                    _screening_observation_scope_fingerprint
                ),
                restore_campaign_screening_case_observations=(
                    _restore_campaign_screening_case_observations
                ),
                restore_historical_screening_lifecycle_observations=(
                    _restore_historical_screening_lifecycle_observations
                ),
                screening_control_harness_fingerprint=(
                    _screening_control_harness_fingerprint
                ),
                screening_control_preflight=(
                    context.services.screening_control_preflight
                ),
                backend_proves_zero_budget_usage=(
                    _backend_proves_zero_budget_usage
                ),
                load_prior_scheduler_state=(
                    context.services.load_prior_scheduler_state
                ),
                candidate_generation_limit=_candidate_generation_limit,
                register_budget_context=register_budget_context,
            ),
        )
    )
    context.state.execution_telemetry = bootstrap.execution_telemetry
    context.published_state.execution_telemetry = bootstrap.execution_telemetry
    context.state.screening_observation_dataset_fingerprint = (
        bootstrap.screening_observation_dataset_fingerprint
    )
    screening_control_preflight = bootstrap.screening_control_preflight
    selection = resolve_run_target_selection(request, context=context)
    target_selection_report = selection.report
    target_provenance = selection.provenance
    target_provenance_unresolved_reason = selection.provenance_unresolved_reason
    provenance_resolution = selection.provenance_resolution
    _emit_progress(
        context.construction.runtime.progress_callback,
        "start",
        f"Starting self-evolve run {run_id}",
    )
    startup_artifact_retention = context.services.artifact_retention_report(
        context.construction.runtime.store,
        run_id,
    )
    _emit_progress(
        context.construction.runtime.progress_callback,
        "trajectory_set_loading",
        (f"Loaded self-evolve trajectory set with {len(dataset.cases)} case(s)"),
    )

    run = SelfEvolveRun(
        run_id=run_id, target=target.identity, status=SelfEvolveRunStatus.RUNNING
    )
    context.construction.runtime.store.create_run(run)
    context.construction.runtime.store.write_screening_control_preflight(
        run_id,
        screening_control_preflight,
    )
    _emit_progress(
        context.construction.runtime.progress_callback,
        "candidate_screening_preflight",
        (
            "Control preflight before candidate generation: "
            f"{screening_control_preflight.get('status')}; "
            f"feasible {len(screening_control_preflight.get('feasible_case_ids', []))}; "
            f"infeasible {len(screening_control_preflight.get('infeasible_case_ids', []))}; "
            f"unknown {len(screening_control_preflight.get('unknown_case_ids', []))}"
        ),
    )
    attempt_tracker = _CandidateAttemptTracker(context.construction.runtime.store, run_id)
    failure_cleanup.attempt_tracker = attempt_tracker
    context.construction.runtime.store.write_dataset_recipe(run_id, dataset.recipe)
    if context.construction.runtime.regression_suites:
        context.construction.runtime.store.write_regression_suite_manifest(
            run_id,
            tuple(suite.spec for suite in context.construction.runtime.regression_suites),
        )
    if target_selection_report is not None:
        context.construction.runtime.store.write_target_selection_report(run_id, target_selection_report)
    target_provenance_path: Path | None = None
    if target_provenance is not None:
        target_provenance_path = context.construction.runtime.store.write_target_provenance(
            run_id,
            target_provenance,
        )
    target_provenance_report = {
        "status": provenance_resolution.status,
        "path": (
            str(target_provenance_path)
            if target_provenance_path is not None
            else None
        ),
        "reason": provenance_resolution.reason,
    }

    stopping_gate = StoppingConditionGate(
        max_iterations=context.construction.policy.max_iterations,
        max_stalled_iterations=1,
        max_repeated_gate_failures=1,
    )
    stopping_result = stopping_gate.evaluate(
        StoppingConditionState(
            iteration=0, pending_duplicate=context.construction.policy.pending_duplicate
        )
    )
    if not stopping_result.passed:
        report = {
            "run_id": run_id,
            "target": {
                "target_type": target.identity.target_type,
                "target_id": target.identity.target_id,
                "path": target.identity.path,
            },
            "apply_policy": apply_policy,
            "candidate_ids": [],
            "selected_candidate_id": None,
            "status": SelfEvolveRunStatus.REJECTED.value,
            "target_provenance": target_provenance_report,
            "stopping_condition": {
                "gate_name": stopping_result.gate_name,
                "passed": stopping_result.passed,
                "reason": stopping_result.reason,
                "details": stopping_result.details,
            },
        }
        if target_selection_report is not None:
            report["target_selection"] = to_json_dict(target_selection_report)
        report["execution"] = {
            "stages": {},
            "total_usage": _execution_usage_report(
                optimizer_diagnostics=[],
                iteration_states=[],
                stages={},
            ),
        }
        completed_run = SelfEvolveRun(
            run_id=run_id,
            target=target.identity,
            status=SelfEvolveRunStatus.REJECTED,
            gate_results=(stopping_result,),
        )
        context.services.finalize_run_report(
            context.construction.runtime.store,
            run_id,
            report=report,
            completed_run=completed_run,
            previous_artifact_retention=startup_artifact_retention,
        )
        _emit_progress(
            context.construction.runtime.progress_callback,
            "completed",
            f"Self-evolve run {run_id} finished with status {completed_run.status.value}",
        )
        return RunLifecycleBootstrapResult(
            bootstrap=bootstrap,
            target_selection_report=target_selection_report,
            target_provenance=target_provenance,
            target_provenance_unresolved_reason=target_provenance_unresolved_reason,
            target_provenance_report=target_provenance_report,
            startup_artifact_retention=startup_artifact_retention,
            attempt_tracker=attempt_tracker,
            early_run=completed_run,
        )
    return RunLifecycleBootstrapResult(
        bootstrap=bootstrap,
        target_selection_report=target_selection_report,
        target_provenance=target_provenance,
        target_provenance_unresolved_reason=target_provenance_unresolved_reason,
        target_provenance_report=target_provenance_report,
        startup_artifact_retention=startup_artifact_retention,
        attempt_tracker=attempt_tracker,
    )
