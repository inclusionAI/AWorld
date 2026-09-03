"""Typed repair-conformance preflight and population lifecycle."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Protocol

from aworld.self_evolve.budget import (
    BudgetDecision,
    BudgetStage,
    CandidateAttemptKey,
    CandidateAttemptStage,
)
from aworld.self_evolve.candidate_package import candidate_package_fingerprint
from aworld.self_evolve.controllers.run_resources import (
    CandidateAttemptTracker,
    RunBudgetContext,
)
from aworld.self_evolve.controllers.run_replay_adaptation import (
    ReplayAdaptationExecution,
    ReplayAdaptationRequest,
    execute_replay_adaptation,
)
from aworld.self_evolve.datasets import SelfEvolveDataset
from aworld.self_evolve.failure_events import (
    FailureEventSource,
    FailureOwner,
    FailureScope,
    FailureStage,
    ReplayFailureObservation,
    ReplayFailureEvent,
    aggregate_replay_failure_observations,
)
from aworld.self_evolve.repair_conformance import (
    RepairConformanceContract,
    RepairConformanceResult,
    build_repair_conformance_probe_plan,
    evaluate_candidate_source_conformance,
    evaluate_compiled_probe_conformance,
    merge_repair_conformance_constraint_context,
    project_replay_capability_for_probe_group,
    repair_conformance_contract_identity,
)
from aworld.self_evolve.replay import (
    _is_replayable_user_task_case,
    preflight_frozen_replay_capability,
    replay_capability_fixture_leaf_values,
    replay_capability_fixture_response_leaf_values,
)
from aworld.self_evolve.replay_adaptation import ReplayCapabilityRequirement
from aworld.self_evolve.repair_conformance_diagnostics import (
    _conformance_gate_blocks_population,
    _failed_probe_typed_feedback,
    _repair_conformance_required_nonempty_operations,
    _repair_conformance_screening_attempt,
    _repair_conformance_validation_surface_changed,
    _repair_probe_root_cause_code,
    _repair_conformance_gate,
)
from aworld.self_evolve.replay_capability import (
    frozen_replay_fixture_shape_fingerprints,
)
from aworld.self_evolve.sanitization import sanitize_path_ref, sanitize_text
from aworld.self_evolve.schema_diagnostics import _schema_field_contract_fingerprint
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.target_package import _safe_artifact_name
from aworld.self_evolve.targets import SelfEvolveTarget
from aworld.self_evolve.types import CandidateVariant, GateResult


@dataclass(frozen=True)
class RepairConformancePreflightRequest:
    run_id: str
    target: SelfEvolveTarget
    dataset: SelfEvolveDataset
    candidate: CandidateVariant
    contract: RepairConformanceContract
    capability_requirements: tuple[ReplayCapabilityRequirement, ...] = ()
    budget_context: RunBudgetContext | None = None


@dataclass(frozen=True)
class RepairConformancePreflightRuntime:
    store: FilesystemSelfEvolveStore
    replay_adaptation: ReplayAdaptationExecution
    create_candidate_skill_overlay: Callable[..., Any]
    evaluate_compiled_probe_conformance: Callable[..., RepairConformanceResult] = (
        evaluate_compiled_probe_conformance
    )
    replay_capability_fixture_leaf_values: Callable[..., Mapping[str, object]] = (
        replay_capability_fixture_leaf_values
    )
    replay_capability_fixture_response_leaf_values: Callable[
        ..., Mapping[str, object]
    ] = replay_capability_fixture_response_leaf_values
    frozen_replay_fixture_shape_fingerprints: Callable[..., Mapping[str, str]] = (
        frozen_replay_fixture_shape_fingerprints
    )
    preflight_frozen_replay_capability: Callable[..., Any] = (
        preflight_frozen_replay_capability
    )
    schema_field_contract_fingerprint: Callable[[object], str | None] = (
        _schema_field_contract_fingerprint
    )


@dataclass(frozen=True)
class RepairConformancePreflightResult:
    gate: GateResult


@dataclass(frozen=True)
class RepairConformancePopulationRequest:
    run_id: str
    target: SelfEvolveTarget
    dataset: SelfEvolveDataset
    candidates: tuple[CandidateVariant, ...]
    capability_requirements: tuple[ReplayCapabilityRequirement, ...]
    repair_conformance_contracts: Mapping[str, RepairConformanceContract]
    attempt_tracker: CandidateAttemptTracker | None = None
    attempt_keys: Mapping[str, CandidateAttemptKey] | None = None
    budget_context: RunBudgetContext | None = None


@dataclass(frozen=True)
class RepairConformancePopulationRuntime:
    progress_callback: Callable[[str, str], Any] | None
    preflight_runtime: RepairConformancePreflightRuntime
    emit_progress: Callable[[Callable[[str, str], Any] | None, str, str], None]
    evaluate_candidate_source_conformance: Callable[
        [CandidateVariant, RepairConformanceContract], RepairConformanceResult
    ] = evaluate_candidate_source_conformance
    preflight_override: RepairConformancePreflightOverride | None = None


class RepairConformancePreflightOverride(Protocol):
    """Typed outer-adapter seam for a legacy preflight monkeypatch."""

    async def __call__(
        self,
        request: RepairConformancePreflightRequest,
    ) -> RepairConformancePreflightResult: ...


@dataclass(frozen=True)
class RepairConformancePopulationResult:
    candidates: tuple[CandidateVariant, ...]
    report: dict[str, object] | None

    def as_tuple(
        self,
    ) -> tuple[tuple[CandidateVariant, ...], dict[str, object] | None]:
        return self.candidates, self.report
















async def preflight_candidate_repair_conformance(
    request: RepairConformancePreflightRequest,
    runtime: RepairConformancePreflightRuntime,
) -> RepairConformancePreflightResult:
    contract = request.contract
    if request.target.identity.path is None:
        return RepairConformancePreflightResult(
            _repair_conformance_gate(
                RepairConformanceResult(
                    passed=False,
                    code="repair_target_path_missing",
                    reason="repair conformance requires a filesystem skill target",
                    details={},
                    failure_class="framework",
                    repairable=False,
                ),
                contract=contract,
            )
        )
    overlay = runtime.create_candidate_skill_overlay(
        workspace_root=runtime.store.workspace_root,
        run_id=request.run_id,
        candidate=request.candidate,
        target_skill_path=request.target.identity.path,
        baseline_skill_roots=getattr(request.target, "baseline_skill_roots", ()),
    )
    adaptation_result = execute_replay_adaptation(
        ReplayAdaptationRequest(
            run_id=request.run_id,
            dataset=request.dataset,
            capability_skill_root=overlay.candidate_skill_path.parent,
            candidate_package_fingerprint=candidate_package_fingerprint(
                request.candidate
            ),
            emit_progress=False,
        ),
        runtime.replay_adaptation,
    )
    adaptation, adaptation_gate = adaptation_result.as_tuple()
    if adaptation is None or not adaptation_gate.passed:
        adaptation_details = dict(adaptation_gate.details or {})
        declared_owner = str(adaptation_details.get("failure_owner") or "")
        declared_scope = str(adaptation_details.get("failure_scope") or "")
        declared_source = str(adaptation_details.get("failure_source") or "")
        proven_shared = bool(
            declared_owner
            in {FailureOwner.INFRASTRUCTURE.value, FailureOwner.FRAMEWORK.value}
            and declared_scope == FailureScope.SHARED_RUN.value
            and declared_source == FailureEventSource.NATIVE.value
        )
        candidate_owned = not proven_shared
        capability_error_code = str(
            adaptation_details.get("capability_error_code") or ""
        ).strip()
        repair_conformance = (
            merge_repair_conformance_constraint_context(
                contract.to_public_dict(), adaptation_details
            )
            or contract.to_public_dict()
        )
        if proven_shared:
            return RepairConformancePreflightResult(
                replace(
                    adaptation_gate,
                    details={
                        **adaptation_details,
                        "stage": adaptation_details.get("stage")
                        or "repair_conformance_compile",
                        "repair_conformance": repair_conformance,
                        "source_gate_name": adaptation_gate.gate_name,
                    },
                )
            )
        failure_event = ReplayFailureEvent(
            code=capability_error_code or "repair_capability_compile_failed",
            owner=(
                FailureOwner.CANDIDATE
                if candidate_owned
                else FailureOwner(declared_owner)
            ),
            stage=FailureStage.CAPABILITY_COMPILE,
            scope=(
                FailureScope.CANDIDATE if candidate_owned else FailureScope.SHARED_RUN
            ),
            repairable=candidate_owned,
            category="repair_conformance",
            contract_fingerprint=runtime.schema_field_contract_fingerprint(
                repair_conformance
            ),
            summary=adaptation_gate.reason,
            diagnostics={
                "gate_name": adaptation_gate.gate_name,
                "outer_code": adaptation_details.get("code"),
                "capability_error_code": capability_error_code or None,
            },
        )
        return RepairConformancePreflightResult(
            GateResult(
                gate_name="candidate_repair_conformance",
                passed=False,
                reason=adaptation_gate.reason,
                details={
                    **adaptation_details,
                    "failure_class": (
                        "candidate" if candidate_owned else "infrastructure"
                    ),
                    "repairable": candidate_owned,
                    "stage": "repair_conformance_compile",
                    "code": "repair_capability_compile_failed",
                    "capability_error_code": capability_error_code or None,
                    "repair_conformance": repair_conformance,
                    "failure_event": failure_event.to_dict(),
                    "causal_failure_events": [failure_event.to_dict()],
                },
            )
        )
    capability = adaptation.replay_capability
    if capability is None:
        return RepairConformancePreflightResult(
            _repair_conformance_gate(
                RepairConformanceResult(
                    passed=False,
                    code="repair_capability_missing",
                    reason="repair candidate did not compile a frozen replay capability",
                    details={"focus_candidate_id": contract.focus_candidate_id},
                ),
                contract=contract,
            )
        )
    probe_conformance = runtime.evaluate_compiled_probe_conformance(
        capability.services,
        contract,
        fixture_leaf_values=runtime.replay_capability_fixture_leaf_values(capability),
        fixture_response_leaf_values=(
            runtime.replay_capability_fixture_response_leaf_values(capability)
        ),
    )
    if not probe_conformance.passed:
        return RepairConformancePreflightResult(
            _repair_conformance_gate(probe_conformance, contract=contract)
        )
    probe_plan = build_repair_conformance_probe_plan(
        capability_id=capability.capability_id,
        services=capability.services,
        requirements=request.capability_requirements,
        fixture_shape_fingerprints=(
            runtime.frozen_replay_fixture_shape_fingerprints(capability)
        ),
        contract=contract,
        dataset_case_ids=tuple(
            case.case_id
            for case in request.dataset.cases
            if _is_replayable_user_task_case(case)
        ),
    )
    artifact_root = (
        runtime.store.run_path(request.run_id)
        / "repair_conformance"
        / _safe_artifact_name(request.candidate.candidate_id)
    )
    group_results: list[dict[str, object]] = []
    failure_observations: list[ReplayFailureObservation] = []
    groups = probe_plan.groups
    conformance_budget: BudgetDecision | None = None
    if groups and request.budget_context is not None:
        conformance_budget = request.budget_context.reserve(
            BudgetStage.CONFORMANCE,
            f"{request.candidate.candidate_id}-conformance",
            units=len(groups),
        )
        if not conformance_budget.allowed:
            return RepairConformancePreflightResult(
                GateResult(
                    gate_name="candidate_repair_conformance",
                    passed=False,
                    reason="repair conformance was not run because budget was denied",
                    details={
                        "failure_class": "budget",
                        "repairable": False,
                        "stage": "repair_conformance",
                        "code": "conformance_budget_denied",
                        "probe_plan": probe_plan.to_dict(),
                        "distinct_conformance_shape_count": len(groups),
                        "budget_decision": conformance_budget.to_dict(),
                    },
                )
            )
    for group_index, group in enumerate(groups):
        fingerprint = group.fingerprint
        artifact_dir = artifact_root / (
            f"group-{group_index + 1:03d}-{fingerprint.removeprefix('sha256:')[:12]}"
        )
        try:
            projected_capability = project_replay_capability_for_probe_group(
                capability, group
            )
            required_nonempty_operations = tuple(
                operation
                for operation in _repair_conformance_required_nonempty_operations(
                    contract
                )
                if operation == group.operation
            )
            required_recorded_operations = tuple(
                operation
                for operation in (
                    (
                        contract.required_fixture_probe_operations
                        or contract.late_observed_operations[-1:]
                    )
                    if contract.requires_fixture_derived_probe
                    else ()
                )
                if operation == group.operation
            )
            await runtime.preflight_frozen_replay_capability(
                projected_capability,
                artifact_dir=artifact_dir,
                required_nonempty_probe_operations=required_nonempty_operations,
                required_recorded_probe_operations=required_recorded_operations,
                integrity_capability=capability,
            )
        except Exception as exc:
            artifact_ref = sanitize_path_ref(
                artifact_dir.relative_to(runtime.store.workspace_root).as_posix()
                if artifact_dir.is_relative_to(runtime.store.workspace_root)
                else artifact_dir.name
            )
            error_reason = sanitize_text(str(exc), max_chars=512)
            error_code = _repair_probe_root_cause_code(exc)
            raw_error_details = getattr(exc, "details", None)
            diagnostic_method = getattr(exc, "diagnostics", None)
            exception_diagnostics = (
                diagnostic_method()
                if callable(diagnostic_method)
                else {}
            )
            combined_error_details = {
                **(
                    dict(exception_diagnostics)
                    if isinstance(exception_diagnostics, Mapping)
                    else {}
                ),
                **(
                    dict(raw_error_details)
                    if isinstance(raw_error_details, Mapping)
                    else {}
                ),
            }
            typed_error_details = {
                key: value
                for key, value in combined_error_details.items()
                if key
                in {
                    "probe_phase",
                    "phase",
                    "probe_kind",
                    "probe_path",
                    "observed_http_status",
                    "required_http_status_class",
                    "service_id",
                    "transport",
                    "runtime_artifact_constraints",
                    "runtime_response_constraints",
                    "runtime_route_constraints",
                    "runtime_response_observation",
                    "schema_field_constraints",
                    "schema_field_violations",
                    "schema_field_violation_count",
                    "counterexample_contracts",
                }
            }
            failure_event = ReplayFailureEvent(
                code=error_code,
                owner=FailureOwner.CANDIDATE,
                stage=FailureStage.CAPABILITY_PREFLIGHT,
                scope=FailureScope.CANDIDATE,
                repairable=True,
                category="repair_conformance",
                summary="candidate conformance probe group failed",
                diagnostics={
                    "affected_case_ids": list(group.case_ids)[:100],
                    "error_type": type(exc).__name__,
                    "root_cause_code": error_code,
                    "reason": error_reason,
                    **typed_error_details,
                },
                artifact_refs=(artifact_ref,),
                capability_id=capability.capability_id,
                requirement_id=(
                    None
                    if (
                        typed_error_details.get("runtime_artifact_constraints")
                        or typed_error_details.get("runtime_route_constraints")
                    )
                    else group.requirement_id
                ),
                contract_fingerprint=(
                    runtime.schema_field_contract_fingerprint(typed_error_details)
                    or fingerprint
                ),
            )
            observations = tuple(
                ReplayFailureObservation(
                    event=failure_event,
                    case_id=case_id,
                    run_id=request.run_id,
                    candidate_id=request.candidate.candidate_id,
                )
                for case_id in group.case_ids
            ) or (
                ReplayFailureObservation(
                    event=failure_event,
                    run_id=request.run_id,
                    candidate_id=request.candidate.candidate_id,
                ),
            )
            failure_observations.extend(observations)
            failure_aggregate = aggregate_replay_failure_observations(observations)[0]
            group_results.append(
                {
                    "fingerprint": fingerprint,
                    "passed": False,
                    "code": error_code,
                    "root_cause_code": error_code,
                    "requirement_id": group.requirement_id,
                    "case_ids": list(group.case_ids),
                    "artifact_ref": artifact_ref,
                    "error_type": type(exc).__name__,
                    "reason": error_reason,
                    **typed_error_details,
                    "failure_event": failure_aggregate.to_dict(),
                }
            )
            continue
        group_results.append(
            {
                "fingerprint": fingerprint,
                "passed": True,
                "code": "repair_probe_group_passed",
                "requirement_id": group.requirement_id,
                "case_ids": list(group.case_ids),
                "artifact_ref": sanitize_path_ref(
                    artifact_dir.relative_to(runtime.store.workspace_root).as_posix()
                    if artifact_dir.is_relative_to(runtime.store.workspace_root)
                    else artifact_dir.name
                ),
            }
        )
    if conformance_budget is not None and request.budget_context is not None:
        request.budget_context.debit(
            conformance_budget,
            actual_source="reserved_fallback_local_conformance",
        )
    failed_groups = tuple(
        result for result in group_results if result.get("passed") is False
    )
    if failed_groups:
        causal_failure_events = [
            item.to_dict()
            for item in aggregate_replay_failure_observations(
                tuple(failure_observations)
            )
        ]
        gate = _repair_conformance_gate(
            RepairConformanceResult(
                passed=False,
                code="repair_probe_execution_failed",
                reason="candidate declared repair probe failed before task rollout",
                details={
                    "artifact_root": str(artifact_root),
                    "probe_plan": probe_plan.to_dict(),
                    "probe_group_results": group_results[:32],
                    "failed_probe_group_count": len(failed_groups),
                    "failed_case_ids": list(
                        dict.fromkeys(
                            case_id
                            for result in failed_groups
                            for case_id in result.get("case_ids", [])
                            if isinstance(case_id, str)
                        )
                    )[:100],
                    "causal_failure_events": causal_failure_events,
                    **_failed_probe_typed_feedback(failed_groups),
                },
            ),
            contract=contract,
        )
        return RepairConformancePreflightResult(gate)
    return RepairConformancePreflightResult(
        _repair_conformance_gate(
            RepairConformanceResult(
                passed=True,
                code="repair_conformance_passed",
                reason="candidate changed the failed branch and passed declared probes",
                details={
                    "focus_candidate_id": contract.focus_candidate_id,
                    "artifact_root": str(artifact_root),
                    "probe_plan": probe_plan.to_dict(),
                    "probe_group_results": group_results[:32],
                },
            ),
            contract=contract,
        )
    )


async def validate_repair_conformance_population(
    request: RepairConformancePopulationRequest,
    runtime: RepairConformancePopulationRuntime,
) -> RepairConformancePopulationResult:
    contracts_by_candidate = dict(request.repair_conformance_contracts)
    applicable = tuple(
        candidate
        for candidate in request.candidates
        if candidate.candidate_id in contracts_by_candidate
    )
    if not applicable:
        return RepairConformancePopulationResult(request.candidates, None)
    runtime.emit_progress(
        runtime.progress_callback,
        "candidate_conformance",
        (
            "Validating candidate repair conformance across "
            f"{len(request.dataset.cases)} dataset case(s)"
        ),
    )
    attempts: list[dict[str, object]] = []
    passed_candidates: list[CandidateVariant] = []
    stopped_by_shared_infrastructure = False
    superseded_candidate_ids: list[str] = []
    rebased_candidate_ids: list[str] = []
    superseding_contract_identity: str | None = None
    for candidate_index, candidate in enumerate(request.candidates):
        contract = contracts_by_candidate.get(candidate.candidate_id)
        if contract is None:
            passed_candidates.append(candidate)
            continue
        attempt_key = (
            request.attempt_keys.get(candidate.candidate_id)
            if request.attempt_keys is not None
            else None
        )
        source_conformance = runtime.evaluate_candidate_source_conformance(
            candidate, contract
        )
        if not source_conformance.passed:
            if request.attempt_tracker is not None and attempt_key is not None:
                request.attempt_tracker.emit(
                    attempt_key,
                    CandidateAttemptStage.REJECTED,
                    reason_code="source_conformance_rejected",
                )
            attempts.append(
                _repair_conformance_screening_attempt(
                    candidate, source_conformance, contract=contract
                )
            )
            if source_conformance.failure_class in {"framework", "infrastructure"}:
                stopped_by_shared_infrastructure = True
                passed_candidates.clear()
                break
            continue
        preflight_request = RepairConformancePreflightRequest(
            run_id=request.run_id,
            target=request.target,
            dataset=request.dataset,
            candidate=candidate,
            contract=contract,
            capability_requirements=request.capability_requirements,
            budget_context=request.budget_context,
        )
        preflight_result = (
            await runtime.preflight_override(preflight_request)
            if runtime.preflight_override is not None
            else await preflight_candidate_repair_conformance(
                preflight_request,
                runtime.preflight_runtime,
            )
        )
        gate = preflight_result.gate
        if request.attempt_tracker is not None and attempt_key is not None:
            if (
                request.attempt_tracker.last_stage(attempt_key)
                is CandidateAttemptStage.LOCAL_GATES
            ):
                request.attempt_tracker.emit(
                    attempt_key,
                    CandidateAttemptStage.ADAPTATION,
                    case_count=len(request.dataset.cases),
                )
            gate_code = (
                str(gate.details.get("code") or "")
                if isinstance(gate.details, Mapping)
                else ""
            )
            probe_plan_payload = (
                gate.details.get("probe_plan")
                if isinstance(gate.details, Mapping)
                else None
            )
            probe_groups = (
                probe_plan_payload.get("groups")
                if isinstance(probe_plan_payload, Mapping)
                else None
            )
            counterexample_contracts = (
                gate.details.get("counterexample_contracts")
                if isinstance(gate.details, Mapping)
                else None
            )
            violations = (
                gate.details.get("violations")
                if isinstance(gate.details, Mapping)
                else None
            )
            shape_count = max(
                len(probe_groups) if isinstance(probe_groups, (list, tuple)) else 0,
                len(counterexample_contracts)
                if isinstance(counterexample_contracts, (list, tuple))
                else 0,
                len(violations) if isinstance(violations, (list, tuple)) else 0,
            )
            if gate_code == "conformance_budget_denied":
                request.attempt_tracker.emit(
                    attempt_key,
                    CandidateAttemptStage.NOT_RUN,
                    reason_code="conformance_budget_denied",
                )
            elif gate_code != "repair_capability_compile_failed":
                request.attempt_tracker.emit(
                    attempt_key,
                    CandidateAttemptStage.CONFORMANCE,
                    case_count=len(request.dataset.cases),
                    distinct_conformance_shape_count=shape_count,
                )
        attempts.append(
            {
                "candidate_id": candidate.candidate_id,
                "screening_candidate_id": None,
                "stage": "conformance",
                "gate_name": gate.gate_name,
                "passed": gate.passed,
                "reason": gate.reason,
                "details": gate.details,
            }
        )
        if gate.passed:
            passed_candidates.append(candidate)
            continue
        evolved_contract = (
            gate.details.get("repair_conformance")
            if isinstance(gate.details, Mapping)
            else None
        )
        if isinstance(evolved_contract, Mapping):
            evolved_identity = repair_conformance_contract_identity(evolved_contract)
            if (
                evolved_identity != contract.contract_identity
                and _repair_conformance_validation_surface_changed(
                    contract, evolved_contract
                )
            ):
                superseding_contract_identity = evolved_identity
                if passed_candidates:
                    superseded_candidate_ids.extend(
                        item.candidate_id for item in passed_candidates
                    )
                    passed_candidates.clear()
                for sibling in request.candidates[candidate_index + 1 :]:
                    sibling_contract = contracts_by_candidate.get(
                        sibling.candidate_id
                    )
                    if sibling_contract is None:
                        continue
                    contracts_by_candidate[sibling.candidate_id] = (
                        _rebase_repair_conformance_contract(
                            sibling_contract,
                            evolved_contract,
                            details=(
                                gate.details
                                if isinstance(gate.details, Mapping)
                                else {}
                            ),
                        )
                    )
                    rebased_candidate_ids.append(sibling.candidate_id)
        if _conformance_gate_blocks_population(gate):
            stopped_by_shared_infrastructure = True
            passed_candidates.clear()
            break
    return RepairConformancePopulationResult(
        tuple(passed_candidates),
        {
            "generated_candidate_count": len(request.candidates),
            "applicable_candidate_count": len(applicable),
            "attempted_candidate_count": len(attempts),
            "passed_candidate_ids": [
                candidate.candidate_id for candidate in passed_candidates
            ],
            "stopped_by_shared_infrastructure": stopped_by_shared_infrastructure,
            "superseded_candidate_ids": superseded_candidate_ids,
            "rebased_candidate_ids": list(dict.fromkeys(rebased_candidate_ids)),
            "superseding_contract_identity": superseding_contract_identity,
            "attempts": attempts,
        },
    )


def _rebase_repair_conformance_contract(
    contract: RepairConformanceContract,
    evolved_contract: Mapping[str, object],
    *,
    details: Mapping[str, object],
) -> RepairConformanceContract:
    """Merge public constraints while retaining a sibling's private lease."""

    merged = merge_repair_conformance_constraint_context(
        contract.to_public_dict(), evolved_contract, details
    )
    if merged is None:
        return contract
    merged["projection_schema_version"] = (
        "aworld.self_evolve.repair_conformance.public.v1"
    )
    public_contract = RepairConformanceContract.from_public_dict(merged)
    return replace(
        contract,
        failure_codes=public_contract.failure_codes,
        required_branch_paths=public_contract.required_branch_paths,
        manifest_path=public_contract.manifest_path,
        compiler_path=public_contract.compiler_path,
        runtime_paths=public_contract.runtime_paths,
        late_observed_operations=public_contract.late_observed_operations,
        requires_compiler_fixture_reconstruction=(
            public_contract.requires_compiler_fixture_reconstruction
        ),
        requires_fixture_derived_probe=(
            public_contract.requires_fixture_derived_probe
        ),
        required_fixture_probe_operations=(
            public_contract.required_fixture_probe_operations
        ),
        fixture_probe_constraints=public_contract.fixture_probe_constraints,
        schema_field_constraints=public_contract.schema_field_constraints,
        runtime_response_constraints=public_contract.runtime_response_constraints,
        runtime_route_constraints=public_contract.runtime_route_constraints,
        runtime_artifact_constraints=public_contract.runtime_artifact_constraints,
        required_runtime_transitions=(
            public_contract.required_runtime_transitions
        ),
        artifact_lifecycle_constraint=(
            public_contract.artifact_lifecycle_constraint
        ),
    )




__all__ = [
    "RepairConformancePopulationRequest",
    "RepairConformancePopulationResult",
    "RepairConformancePopulationRuntime",
    "RepairConformancePreflightRequest",
    "RepairConformancePreflightResult",
    "RepairConformancePreflightRuntime",
    "RepairConformancePreflightOverride",
    "preflight_candidate_repair_conformance",
    "validate_repair_conformance_population",
]
