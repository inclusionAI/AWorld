"""Repair-conformance gate projection and bounded diagnostics."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from aworld.self_evolve.failure_events import (
    FailureEventSource,
    FailureOwner,
    FailureScope,
    FailureStage,
    ReplayFailureEvent,
    _typed_causal_feedback_event,
)
from aworld.self_evolve.repair_conformance import (
    RepairConformanceContract,
    RepairConformanceResult,
    merge_repair_conformance_constraint_context,
)
from aworld.self_evolve.replay import (
    ReplayServiceProcessExitedError,
    ReplayServiceReadinessTimeout,
    replay_capability_fixture_summaries,
)
from aworld.self_evolve.sanitization import (
    public_diagnostic_projection,
    sanitize_path_ref,
    sanitize_text,
)
from aworld.self_evolve.schema_diagnostics import (
    _schema_field_contract_fingerprint,
)
from aworld.self_evolve.types import CandidateVariant, GateResult


def _gate_has_typed_shared_infrastructure_failure(gate: GateResult) -> bool:
    details = gate.details
    if not isinstance(details, Mapping):
        return False
    raw_events: list[Mapping[str, object]] = []
    raw_event = details.get("failure_event")
    if isinstance(raw_event, Mapping):
        raw_events.append(raw_event)
    raw_causal_events = details.get("causal_failure_events")
    if isinstance(raw_causal_events, (list, tuple)):
        raw_events.extend(
            item for item in raw_causal_events if isinstance(item, Mapping)
        )
    for payload in raw_events:
        try:
            event = _typed_causal_feedback_event(payload)
        except (TypeError, ValueError):
            continue
        if (
            FailureEventSource.NATIVE.value in event.source_kinds
            and event.scope is FailureScope.SHARED_RUN
            and event.owner
            in {FailureOwner.INFRASTRUCTURE, FailureOwner.FRAMEWORK}
        ):
            return True
    return False

def _repair_conformance_gate(
    result: RepairConformanceResult,
    *,
    contract: RepairConformanceContract | None = None,
) -> GateResult:
    public_result_details = public_diagnostic_projection(dict(result.details))
    if not isinstance(public_result_details, Mapping):
        public_result_details = {}
    details = {
        "failure_class": (
            None if result.passed else result.failure_class
        ),
        "repairable": bool(not result.passed and result.repairable),
        "stage": "repair_conformance",
        "code": result.code,
        **dict(public_result_details),
    }
    if result.failure_fingerprint is not None:
        details["failure_fingerprint"] = result.failure_fingerprint
    if not result.passed:
        raw_causal_events = details.get("causal_failure_events")
        causal_events = (
            [dict(item) for item in raw_causal_events if isinstance(item, Mapping)]
            if isinstance(raw_causal_events, (list, tuple))
            else []
        )
        if not causal_events:
            failure_owner = (
                FailureOwner.FRAMEWORK
                if result.failure_class == "framework"
                else (
                    FailureOwner.INFRASTRUCTURE
                    if result.failure_class == "infrastructure"
                    else FailureOwner.CANDIDATE
                )
            )
            failure_event = ReplayFailureEvent(
                code=result.code,
                owner=failure_owner,
                stage=FailureStage.CAPABILITY_PREFLIGHT,
                scope=(
                    FailureScope.CANDIDATE
                    if failure_owner is FailureOwner.CANDIDATE
                    else FailureScope.SHARED_RUN
                ),
                repairable=result.repairable,
                category="repair_conformance",
                summary=result.reason,
                contract_fingerprint=(
                    _schema_field_contract_fingerprint(details)
                    or (
                        contract.contract_identity
                        if contract is not None
                        else None
                    )
                ),
                diagnostics={
                    "focus_candidate_id": (
                        contract.focus_candidate_id if contract is not None else None
                    ),
                },
            )
            causal_events = [failure_event.to_dict()]
        details["failure_event"] = causal_events[0]
        # Conformance is an independent pre-replay gate, so publish every
        # distinct failed group through the causal feedback channel.
        details["causal_failure_events"] = causal_events
    if contract is not None:
        details["repair_conformance"] = (
            merge_repair_conformance_constraint_context(
                contract.to_public_dict(),
                details,
            )
            or contract.to_public_dict()
        )
    return GateResult(
        gate_name="candidate_repair_conformance",
        passed=result.passed,
        reason=result.reason,
        details=details,
    )

def _repair_conformance_validation_surface_changed(
    current: RepairConformanceContract,
    evolved: Mapping[str, object],
) -> bool:
    try:
        evolved_contract = RepairConformanceContract.from_public_dict(evolved)
    except (TypeError, ValueError):
        return True

    def surface(contract: RepairConformanceContract) -> tuple[object, ...]:
        return (
            contract.required_branch_paths,
            contract.manifest_path,
            contract.compiler_path,
            contract.runtime_paths,
            contract.exact_probe,
            contract.late_observed_operations,
            contract.requires_compiler_fixture_reconstruction,
            contract.requires_fixture_derived_probe,
            contract.required_fixture_probe_operations,
            contract.fixture_probe_constraints,
            contract.schema_field_constraints,
            contract.runtime_response_constraints,
            contract.runtime_artifact_constraints,
            contract.required_runtime_transitions,
            contract.artifact_lifecycle_constraint,
        )

    return surface(current) != surface(evolved_contract)

def _failed_probe_typed_feedback(
    failed_groups: Iterable[Mapping[str, object]],
) -> dict[str, object]:
    constraints: dict[str, dict[str, object]] = {}
    runtime_artifact_constraints: dict[str, dict[str, object]] = {}
    runtime_response_constraints: dict[str, dict[str, object]] = {}
    runtime_response_observations: list[dict[str, object]] = []
    counterexample_contracts: dict[str, dict[str, object]] = {}
    violations: list[dict[str, object]] = []
    diagnostics: list[dict[str, object]] = []
    violation_count = 0
    for result in failed_groups:
        diagnostic: dict[str, object] = {
            "code": str(result.get("code") or "repair_probe_execution_failed"),
            "root_cause_code": str(
                result.get("root_cause_code")
                or result.get("code")
                or "repair_probe_execution_failed"
            ),
            "error_type": str(result.get("error_type") or "Exception"),
            "reason": str(result.get("reason") or "candidate probe failed"),
        }
        for source_key, destination, limit in (
            ("schema_field_constraints", constraints, None),
            ("runtime_response_constraints", runtime_response_constraints, 64),
            ("runtime_artifact_constraints", runtime_artifact_constraints, 64),
        ):
            raw_items = result.get(source_key)
            if not isinstance(raw_items, (list, tuple)):
                continue
            projected: list[dict[str, object]] = []
            selected_items = raw_items if limit is None else raw_items[:limit]
            for item in selected_items:
                if not isinstance(item, Mapping):
                    continue
                value = dict(item)
                identity = json.dumps(
                    value,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                    default=str,
                )
                destination[identity] = value
                projected.append(value)
            if projected:
                diagnostic[source_key] = projected
        raw_violations = result.get("schema_field_violations")
        if isinstance(raw_violations, (list, tuple)):
            projected_violations = [
                dict(item) for item in raw_violations if isinstance(item, Mapping)
            ][:100]
            violations.extend(projected_violations)
            if projected_violations:
                diagnostic["schema_field_violations"] = projected_violations
        raw_count = result.get("schema_field_violation_count")
        if isinstance(raw_count, int) and not isinstance(raw_count, bool):
            violation_count += max(0, raw_count)
        raw_counterexamples = result.get("counterexample_contracts")
        if isinstance(raw_counterexamples, (list, tuple)):
            projected_counterexamples: list[dict[str, object]] = []
            for item in raw_counterexamples[:100]:
                if not isinstance(item, Mapping):
                    continue
                counterexample_id = item.get("counterexample_id")
                if not isinstance(counterexample_id, str) or not counterexample_id:
                    continue
                value = dict(item)
                counterexample_contracts[counterexample_id] = value
                projected_counterexamples.append(value)
            if projected_counterexamples:
                diagnostic["counterexample_contracts"] = projected_counterexamples
        raw_runtime_observation = result.get("runtime_response_observation")
        if isinstance(raw_runtime_observation, Mapping):
            observation = dict(raw_runtime_observation)
            runtime_response_observations.append(observation)
            diagnostic["runtime_response_observation"] = observation
        diagnostics.append(diagnostic)
    feedback: dict[str, object] = {"diagnostics": diagnostics[:32]}
    if constraints:
        feedback["schema_field_constraints"] = [
            constraints[key] for key in sorted(constraints)
        ]
    if violations:
        feedback["schema_field_violations"] = violations[:100]
        feedback["schema_field_violation_count"] = (
            violation_count if violation_count else len(violations)
        )
    if counterexample_contracts:
        feedback["counterexample_contracts"] = [
            counterexample_contracts[key] for key in sorted(counterexample_contracts)
        ]
    if runtime_response_constraints:
        feedback["runtime_response_constraints"] = [
            runtime_response_constraints[key]
            for key in sorted(runtime_response_constraints)
        ]
    if runtime_artifact_constraints:
        feedback["runtime_artifact_constraints"] = [
            runtime_artifact_constraints[key]
            for key in sorted(runtime_artifact_constraints)
        ]
    if runtime_response_observations:
        feedback["runtime_response_observations"] = runtime_response_observations[:32]
    return feedback

def _repair_probe_root_cause_code(exc: Exception) -> str:
    declared = getattr(exc, "code", None)
    if isinstance(declared, str) and declared:
        return declared
    if isinstance(exc, ReplayServiceReadinessTimeout):
        if exc.phase == "protocol_probe":
            return "replay_service_protocol_probe_timeout"
        return "replay_service_readiness_failed"
    if isinstance(exc, ReplayServiceProcessExitedError):
        return "replay_service_process_exited_before_readiness"
    return "repair_probe_execution_failed"

def _repair_conformance_required_nonempty_operations(
    contract: RepairConformanceContract,
) -> tuple[str, ...]:
    if not contract.late_observed_operations:
        return ()
    if contract.requires_fixture_derived_probe or contract.exact_probe is not None:
        return (
            contract.required_fixture_probe_operations
            or contract.late_observed_operations[-1:]
        )
    return ()

def _repair_conformance_screening_attempt(
    candidate: CandidateVariant,
    result: RepairConformanceResult,
    *,
    contract: RepairConformanceContract,
) -> dict[str, object]:
    gate = _repair_conformance_gate(result, contract=contract)
    return {
        "candidate_id": candidate.candidate_id,
        "screening_candidate_id": None,
        "stage": "conformance",
        "gate_name": gate.gate_name,
        "passed": False,
        "reason": gate.reason,
        "details": gate.details,
    }

def _repair_conformance_failure_diagnostics(
    capability: Any,
    *,
    artifact_dir: Path,
) -> dict[str, object]:
    diagnostics: dict[str, object] = {}
    fixture_summaries = replay_capability_fixture_summaries(capability)
    if fixture_summaries:
        diagnostics["replay_fixture_summaries"] = fixture_summaries
    trace_excerpts: list[dict[str, str]] = []
    if artifact_dir.is_dir():
        inspected = 0
        for path in artifact_dir.rglob("*"):
            inspected += 1
            if inspected > 128 or len(trace_excerpts) >= 8:
                break
            if path.is_symlink() or not path.is_file():
                continue
            name = path.name.lower()
            if "protocol_trace" not in name and name not in {
                "stderr.txt",
                "stdout.txt",
            }:
                continue
            try:
                with path.open("rb") as handle:
                    handle.seek(0, 2)
                    size = handle.tell()
                    handle.seek(max(0, size - 4_096))
                    tail = handle.read(4_096).decode("utf-8", errors="replace")
            except OSError:
                continue
            bounded_tail = sanitize_text(tail, max_chars=4_000).strip()
            if bounded_tail:
                trace_excerpts.append(
                    {
                        "path": sanitize_path_ref(
                            path.relative_to(artifact_dir).as_posix()
                        ),
                        "tail": bounded_tail,
                    }
                )
    if trace_excerpts:
        diagnostics["replay_service_protocol_traces"] = trace_excerpts
    return diagnostics

def _conformance_gate_blocks_population(gate: GateResult) -> bool:
    return _gate_has_typed_shared_infrastructure_failure(gate)
