"""Typed validation feedback and replay diagnostic projections."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from typing import Any, Iterable, Mapping

from aworld.self_evolve.counterexamples import (
    candidate_failure_counterexample,
    normalize_counterexample,
)
from aworld.self_evolve.evidence_diagnostics import (
    EvidenceRepairConstraint,
    merge_evidence_repair_constraints,
)
from aworld.self_evolve.failure_events import (
    FailureOwner,
    _typed_causal_feedback_event,
)
from aworld.self_evolve.recovery_trace import (
    update_constraint_recovery_trace,
    validate_public_constraint_recovery_trace,
    validate_public_recovery_trace,
)
from aworld.self_evolve.repair_conformance import (
    merge_repair_conformance_constraint_context,
)
from aworld.self_evolve.sanitization import (
    public_diagnostic_projection,
    sanitize_text,
)
from aworld.self_evolve.schema_diagnostics import SchemaFieldRepairConstraint
from aworld.self_evolve.types import (
    EvaluationSummary,
    GateResult,
    to_json_dict,
)


_MAX_CURRENT_RUN_VALIDATION_FEEDBACK = 16


def _feedback_requires_counterexample_screening(
    feedback_items: Iterable[EvaluationSummary],
) -> bool:
    for feedback in feedback_items:
        metrics = feedback.metrics
        counterexamples = metrics.get("replay_counterexamples")
        if isinstance(counterexamples, (list, tuple)) and counterexamples:
            return True
        failed_gates = metrics.get("failed_gates")
        if isinstance(failed_gates, (list, tuple)) and any(
            str(gate) in {"candidate_replay", "replay_confidence"}
            for gate in failed_gates
        ):
            return True
    return False


def _feedback_has_candidate_repair_conformance(
    feedback_items: Iterable[EvaluationSummary],
) -> bool:
    """Return whether the active typed frontier owns support-package repair."""

    for feedback in feedback_items:
        failed_gates = feedback.metrics.get("failed_gates")
        if isinstance(failed_gates, str):
            failed_gates = (failed_gates,)
        if isinstance(failed_gates, (list, tuple)) and any(
            str(gate) == "candidate_repair_conformance"
            for gate in failed_gates
        ):
            return True
    return False


def _merge_validation_feedback(
    existing: Iterable[EvaluationSummary],
    new: Iterable[EvaluationSummary],
) -> tuple[EvaluationSummary, ...]:
    existing_items = tuple(existing)
    previous_constraint_trace = max(
        (
            trace
            for item in existing_items
            if (
                trace := validate_public_constraint_recovery_trace(
                    item.metrics.get("constraint_recovery_trace")
                )
            )
            is not None
        ),
        key=lambda trace: int(trace.get("attempt_count") or 0),
        default=None,
    )
    enriched_new: list[EvaluationSummary] = []
    for item in new:
        violated_ids = _feedback_violated_schema_constraint_ids(item)
        contract_ids = _feedback_contract_schema_constraint_ids(item)
        advanced_trace = update_constraint_recovery_trace(
            previous_constraint_trace,
            violated_constraint_ids=violated_ids,
            contract_constraint_ids=contract_ids,
        )
        if advanced_trace is not None:
            item = replace(
                item,
                metrics={
                    **dict(item.metrics),
                    "constraint_recovery_trace": advanced_trace,
                },
            )
            previous_constraint_trace = advanced_trace
        enriched_new.append(item)
    merged: list[EvaluationSummary] = []
    seen: set[str] = set()
    for item in (*existing_items, *tuple(enriched_new)):
        fingerprint = hashlib.sha256(
            json.dumps(
                to_json_dict(item),
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        merged.append(item)
    best_family_index: dict[str, int] = {}
    best_family_progress: dict[str, tuple[int, ...]] = {}
    for index, item in enumerate(merged):
        family = _validation_feedback_failure_family(item)
        if family is not None:
            progress = _feedback_interaction_progress(item)
            recovery_frontier = _feedback_recovery_frontier(item)
            constraint_frontier = _feedback_constraint_recovery_frontier(item)
            frontier = (*recovery_frontier, *constraint_frontier, progress)
            if frontier >= best_family_progress.get(
                family,
                tuple(-1 for _ in frontier),
            ):
                best_family_progress[family] = frontier
                best_family_index[family] = index
    compacted = [
        item
        for index, item in enumerate(merged)
        if (
            (family := _validation_feedback_failure_family(item)) is None
            or best_family_index.get(family) == index
        )
    ]
    return tuple(compacted[-_MAX_CURRENT_RUN_VALIDATION_FEEDBACK:])


def _feedback_interaction_progress(feedback: EvaluationSummary) -> int:
    value = feedback.metrics.get("interaction_progress")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


def _feedback_recovery_frontier(
    feedback: EvaluationSummary,
) -> tuple[int, ...]:
    trace = validate_public_recovery_trace(
        feedback.metrics.get("recovery_trace")
    )
    return (
        _recovery_trace_frontier(trace)
        if trace is not None
        else (0, 0, 0, 0, 0, 0, 0, 0)
    )


def _feedback_constraint_recovery_frontier(
    feedback: EvaluationSummary,
) -> tuple[int, ...]:
    trace = validate_public_constraint_recovery_trace(
        feedback.metrics.get("constraint_recovery_trace")
    )
    if trace is None:
        return (0, 0, 0, 0)
    return (
        int(trace.get("recovered_constraint_count") or 0),
        -int(trace.get("regressed_constraint_count") or 0),
        -int(trace.get("active_violation_count") or 0),
        int(trace.get("attempt_count") or 0),
    )


def _feedback_violated_schema_constraint_ids(
    feedback: EvaluationSummary,
) -> tuple[str, ...]:
    raw = feedback.metrics.get("violated_schema_constraint_ids")
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(
        item
        for item in raw[:100]
        if isinstance(item, str)
        and re.fullmatch(r"sha256:[0-9a-f]{64}", item)
    )


def _feedback_contract_schema_constraint_ids(
    feedback: EvaluationSummary,
) -> tuple[str, ...]:
    identities: set[str] = set(_feedback_violated_schema_constraint_ids(feedback))
    pending: list[object] = [
        feedback.metrics.get("candidate_validation_diagnostics")
    ]
    visited = 0
    while pending and visited < 512 and len(identities) < 100:
        current = pending.pop()
        visited += 1
        if isinstance(current, Mapping):
            raw_constraints = current.get("schema_field_constraints")
            if isinstance(raw_constraints, (list, tuple)):
                for raw_constraint in raw_constraints[:100]:
                    if not isinstance(raw_constraint, Mapping):
                        continue
                    try:
                        constraint = SchemaFieldRepairConstraint.from_dict(
                            raw_constraint
                        )
                    except ValueError:
                        continue
                    identities.add(f"sha256:{constraint.identity_digest}")
            pending.extend(current.values())
        elif isinstance(current, (list, tuple)):
            pending.extend(current)
    return tuple(sorted(identities))


def _recovery_trace_frontier(
    trace: Mapping[str, object],
) -> tuple[int, ...]:
    def integer(key: str) -> int:
        value = trace.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value)
        return 0

    success_rate = trace.get("candidate_success_rate")
    success_millis = (
        int(float(success_rate) * 1_000)
        if isinstance(success_rate, (int, float))
        and not isinstance(success_rate, bool)
        else 0
    )
    loop_free_members = 0
    switched_members = 0
    repeated_action_quality = 0
    failed_progress = 0
    members = trace.get("members")
    if isinstance(members, list):
        for member in members[:64]:
            if not isinstance(member, Mapping):
                continue
            if member.get("failure_loop_detected") is not True:
                loop_free_members += 1
            failed_path = member.get("failed_path")
            if isinstance(failed_path, Mapping):
                switches = failed_path.get("strategy_switch_count_max")
                if (
                    isinstance(switches, (int, float))
                    and not isinstance(switches, bool)
                    and switches > 0
                ):
                    switched_members += 1
                repeated_rate = failed_path.get("repeated_action_rate_max")
                if (
                    isinstance(repeated_rate, (int, float))
                    and not isinstance(repeated_rate, bool)
                ):
                    repeated_action_quality += int(
                        (1.0 - min(1.0, max(0.0, float(repeated_rate))))
                        * 1_000
                    )
            raw_progress = member.get("failed_progress_max")
            if isinstance(raw_progress, (int, float)) and not isinstance(
                raw_progress, bool
            ):
                failed_progress += min(100_000, max(0, int(raw_progress)))
    return (
        integer("recovered_member_count"),
        success_millis,
        integer("stable_recovery_member_count"),
        -integer("regressed_member_count"),
        loop_free_members,
        switched_members,
        repeated_action_quality,
        -failed_progress,
    )


def _next_progress_repair_extension_family(
    feedback_items: Iterable[EvaluationSummary],
    *,
    consumed_families: set[str],
) -> str | None:
    for feedback in reversed(tuple(feedback_items)):
        metrics = feedback.metrics
        if metrics.get("failure_class") != "candidate":
            continue
        if metrics.get("repairable") is not True:
            continue
        if not isinstance(metrics.get("repair_candidate_package"), Mapping):
            continue
        family = _validation_feedback_failure_family(feedback)
        if family is not None and family not in consumed_families:
            return family
    return None


def _validation_feedback_failure_family(
    feedback: EvaluationSummary,
) -> str | None:
    metrics = feedback.metrics
    if not isinstance(metrics.get("repair_candidate_package"), Mapping):
        return None
    signature = {
        "failed_gates": sorted(
            str(item) for item in metrics.get("failed_gates", [])
        ),
        "failure_class": metrics.get("failure_class"),
        "diagnostics": _failure_signature_values(
            metrics.get("candidate_validation_diagnostics")
        ),
    }
    return hashlib.sha256(
        json.dumps(
            signature,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _failure_signature_values(value: Any) -> list[tuple[str, str]]:
    selected_keys = {
        "code",
        "failure_class",
        "failure_fingerprint",
        "proof_fingerprint",
        "repairable",
        "stage",
        "type",
    }
    values: list[tuple[str, str]] = []

    def visit(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, nested in item.items():
                key_text = str(key)
                if key_text in selected_keys and not isinstance(
                    nested, (Mapping, list, tuple)
                ):
                    values.append((key_text, str(nested)))
                else:
                    visit(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item:
                visit(nested)

    visit(value)
    return sorted(values)


def _diagnostic_classification_text(value: Any) -> str:
    """Extract bounded diagnostic tails for type classification only."""

    selected_keys = {
        "detail",
        "error",
        "message",
        "reason",
        "stderr_tail",
        "stdout_tail",
        "tail",
    }
    values: list[str] = []

    def visit(item: Any) -> None:
        if len(values) >= 32:
            return
        if isinstance(item, Mapping):
            for key, nested in item.items():
                if len(values) >= 32:
                    break
                if str(key) in selected_keys and not isinstance(
                    nested, (Mapping, list, tuple)
                ):
                    values.append(str(nested)[-2_000:])
                else:
                    visit(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item[:32]:
                visit(nested)

    visit(value)
    return "\n".join(values)


def _candidate_repair_diagnostic_view(
    details: Mapping[str, object],
) -> Mapping[str, object]:
    """Select candidate-side evidence from a paired replay gate.

    Baseline failures remain in the persisted gate details for comparison, but
    they must not redirect repair attribution away from the candidate variant.
    """

    causal_events = details.get("causal_failure_events")
    if isinstance(causal_events, list):
        candidate_causes = [
            dict(event)
            for event in causal_events
            if isinstance(event, Mapping) and event.get("owner") == "candidate"
        ]
        if candidate_causes:
            view: dict[str, object] = {
                "failure_class": "candidate",
                "repairable": all(event.get("repairable") is True for event in candidate_causes),
                "causal_failure_events": candidate_causes,
            }
            completion_evidence = details.get(
                "paired_candidate_completion_evidence"
            )
            if isinstance(completion_evidence, Mapping):
                view["paired_candidate_completion_evidence"] = dict(
                    completion_evidence
                )
            direct_failure = details.get("candidate_failure")
            if isinstance(direct_failure, Mapping):
                view["candidate_failure"] = dict(direct_failure)
            recovery_trace = validate_public_recovery_trace(
                details.get("recovery_trace")
            )
            if recovery_trace is not None:
                view["recovery_trace"] = recovery_trace
            return view
        # Typed non-candidate causes must not be reclassified from prose stored
        # elsewhere in the gate details.
        return {"causal_failure_events": []}

    candidate_failures: list[Mapping[str, object]] = []
    direct = details.get("candidate_failure")
    if isinstance(direct, Mapping):
        candidate_failures.append(direct)
    failed_members = details.get("failed_members")
    if isinstance(failed_members, list):
        for member in failed_members[:64]:
            if not isinstance(member, Mapping):
                continue
            failure = member.get("candidate_failure")
            if isinstance(failure, Mapping):
                candidate_failures.append(failure)
    if not candidate_failures:
        return details
    return {
        "failure_class": details.get("failure_class"),
        "repairable": details.get("repairable"),
        "candidate_failures": candidate_failures,
    }


def _typed_gate_feedback_metrics(
    failed_gates: Iterable[GateResult],
) -> dict[str, object]:
    failed_gate_items = tuple(failed_gates)
    diagnostics: list[Mapping[str, object]] = []
    classification_fragments: list[str] = []
    classification_views: list[Mapping[str, object]] = []
    failure_classes: set[str] = set()
    repairable_values: list[bool] = []
    causal_events: dict[tuple[str, str], Mapping[str, object]] = {}
    candidate_causal_contexts: dict[str, Mapping[str, object]] = {}
    repair_contract_contexts: list[Mapping[str, object]] = []
    recovery_traces: list[dict[str, object]] = []
    violated_schema_constraint_ids: set[str] = set()
    evidence_constraint_groups: list[
        tuple[EvidenceRepairConstraint, ...]
    ] = []
    replay_counterexamples: list[dict[str, object]] = []
    replay_counterexample_fingerprints: set[str] = set()
    conformance_counterexample_contracts: dict[str, dict[str, object]] = {}
    active_schema_constraints: dict[str, dict[str, object]] = {}
    active_schema_violations: dict[str, dict[str, object]] = {}

    def add_replay_counterexample(value: object) -> None:
        normalized = normalize_counterexample(value)
        if normalized is None:
            return
        fingerprint = json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if fingerprint in replay_counterexample_fingerprints:
            return
        replay_counterexample_fingerprints.add(fingerprint)
        replay_counterexamples.append(normalized)

    for gate in failed_gate_items:
        details = gate.details
        gate_diagnostic: dict[str, object] = {
            "code": "failed_gate",
            "stage": sanitize_text(gate.gate_name, max_chars=120),
            "reason": sanitize_text(gate.reason, max_chars=400),
        }
        if not isinstance(details, Mapping):
            diagnostics.append(gate_diagnostic)
            continue
        raw_repair_contract = details.get("repair_conformance")
        if isinstance(raw_repair_contract, Mapping):
            projected_contract = public_diagnostic_projection(
                raw_repair_contract,
                max_chars=8_192,
            )
            if isinstance(projected_contract, Mapping):
                repair_contract_contexts.append(dict(projected_contract))
        raw_conformance_counterexamples = details.get(
            "counterexample_contracts"
        )
        if isinstance(raw_conformance_counterexamples, (list, tuple)):
            for raw_counterexample in raw_conformance_counterexamples[:64]:
                if not isinstance(raw_counterexample, Mapping):
                    continue
                projected_counterexample = public_diagnostic_projection(
                    raw_counterexample,
                    max_chars=2_048,
                )
                if not isinstance(projected_counterexample, Mapping):
                    continue
                counterexample_id = projected_counterexample.get(
                    "counterexample_id"
                )
                if isinstance(counterexample_id, str) and counterexample_id:
                    conformance_counterexample_contracts[counterexample_id] = (
                        dict(projected_counterexample)
                    )
        for counterexample in _public_replay_counterexamples(details):
            add_replay_counterexample(counterexample)
        raw_schema_constraints = details.get("schema_field_constraints")
        if isinstance(raw_schema_constraints, (list, tuple)):
            for raw_constraint in raw_schema_constraints[:100]:
                if not isinstance(raw_constraint, Mapping):
                    continue
                try:
                    constraint = SchemaFieldRepairConstraint.from_dict(
                        raw_constraint
                    )
                except ValueError:
                    continue
                violated_schema_constraint_ids.add(
                    f"sha256:{constraint.identity_digest}"
                )
                active_schema_constraints[constraint.identity_digest] = (
                    constraint.to_dict()
                )
        raw_schema_violations = details.get("schema_field_violations")
        if isinstance(raw_schema_violations, (list, tuple)):
            for raw_violation in raw_schema_violations[:100]:
                if not isinstance(raw_violation, Mapping):
                    continue
                identity = str(
                    raw_violation.get("constraint_identity_digest") or ""
                )
                if not identity:
                    continue
                projected = {
                    key: raw_violation.get(key)
                    for key in (
                        "constraint_identity_digest",
                        "schema_layer",
                        "field_path",
                        "rule",
                        "actual_type",
                        "actual_fingerprint",
                        "occurrence_count",
                    )
                    if raw_violation.get(key) is not None
                }
                observation_key = json.dumps(
                    projected,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                    default=str,
                )
                active_schema_violations[observation_key] = projected
        raw_evidence_regressions = details.get(
            "evidence_constraint_regressions"
        )
        raw_evidence_constraints = (
            raw_evidence_regressions
            if isinstance(raw_evidence_regressions, (list, tuple))
            else details.get("evidence_repair_constraints")
        )
        evidence_constraints: list[EvidenceRepairConstraint] = []
        if isinstance(raw_evidence_constraints, (list, tuple)):
            for raw_constraint in raw_evidence_constraints[:128]:
                if not isinstance(raw_constraint, Mapping):
                    continue
                try:
                    evidence_constraints.append(
                        EvidenceRepairConstraint.from_dict(raw_constraint)
                    )
                except (TypeError, ValueError):
                    continue
        evidence_constraint_groups.append(tuple(evidence_constraints))
        classification_view = _candidate_repair_diagnostic_view(details)
        classification_views.append(classification_view)
        recovery_trace = validate_public_recovery_trace(
            classification_view.get("recovery_trace")
        )
        if recovery_trace is not None and recovery_trace not in recovery_traces:
            recovery_traces.append(recovery_trace)
        classification_fragments.append(
            _diagnostic_classification_text(classification_view)
        )
        bounded_details = public_diagnostic_projection(details, max_chars=400)
        if isinstance(bounded_details, Mapping):
            gate_diagnostic["details"] = dict(bounded_details)
        diagnostics.append(gate_diagnostic)
        failure_class = details.get("failure_class")
        if isinstance(failure_class, str) and failure_class:
            failure_classes.add(failure_class)
        repairable = details.get("repairable")
        if isinstance(repairable, bool):
            repairable_values.append(repairable)
        raw_causal_events = details.get("causal_failure_events")
        if isinstance(raw_causal_events, list):
            for event in raw_causal_events[:64]:
                if not isinstance(event, Mapping):
                    continue
                try:
                    typed_event = _typed_causal_feedback_event(event)
                except (TypeError, ValueError):
                    if event.get("schema_version") is not None:
                        raise
                    continue
                transport = typed_event.to_feedback_dict()
                emission_key = (typed_event.semantic_key, typed_event.emission_id)
                previous = causal_events.setdefault(emission_key, transport)
                if previous != transport:
                    raise ValueError(
                        "causal emission id was reused with a different typed payload"
                    )
                if typed_event.owner is FailureOwner.CANDIDATE:
                    has_specific_counterexample = any(
                        item.get("failure_code") == typed_event.code
                        and item.get("stage") == typed_event.stage.value
                        for item in replay_counterexamples
                    )
                    generic_counterexample = (
                        None
                        if has_specific_counterexample
                        or _public_replay_counterexamples(event)
                        else candidate_failure_counterexample(
                            transport,
                            sequence=len(replay_counterexamples) + 1,
                        )
                    )
                    if generic_counterexample is not None:
                        add_replay_counterexample(
                            generic_counterexample.to_dict()
                        )
                    raw_contract = details.get("repair_conformance")
                    inherited_context = candidate_causal_contexts.get(
                        typed_event.semantic_key
                    )
                    base_context = (
                        raw_contract
                        if isinstance(raw_contract, Mapping)
                        else inherited_context
                    )
                    merged_context = (
                        merge_repair_conformance_constraint_context(
                            base_context,
                            *(
                                (inherited_context,)
                                if inherited_context is not None
                                and inherited_context is not base_context
                                else ()
                            ),
                            details,
                        )
                    )
                    if merged_context is not None:
                        bounded_contract = public_diagnostic_projection(
                            merged_context,
                            max_chars=2_048,
                        )
                        if isinstance(bounded_contract, Mapping):
                            candidate_causal_contexts[
                                typed_event.semantic_key
                            ] = dict(bounded_contract)
        raw_diagnostics = details.get("diagnostics")
        if isinstance(raw_diagnostics, list):
            diagnostics.extend(
                dict(item)
                for item in raw_diagnostics[:16]
                if isinstance(item, Mapping)
            )
    result: dict[str, object] = {}
    evidence_constraints = merge_evidence_repair_constraints(
        *evidence_constraint_groups
    )
    if evidence_constraints:
        result["evidence_repair_constraints"] = [
            constraint.to_dict() for constraint in evidence_constraints
        ]
    if replay_counterexamples:
        result["replay_counterexamples"] = replay_counterexamples[:16]
    if conformance_counterexample_contracts:
        result["counterexample_contracts"] = [
            conformance_counterexample_contracts[key]
            for key in sorted(conformance_counterexample_contracts)
        ]
    if recovery_traces:
        result["recovery_trace"] = max(
            recovery_traces,
            key=_recovery_trace_frontier,
        )
    if repair_contract_contexts:
        merged_repair_contract: Mapping[str, object] = dict(
            repair_contract_contexts[0]
        )
        for repair_contract in repair_contract_contexts[1:]:
            merged_repair_contract = (
                merge_repair_conformance_constraint_context(
                    merged_repair_contract,
                    repair_contract,
                )
                or merged_repair_contract
            )
        result["repair_conformance"] = dict(merged_repair_contract)
    if active_schema_constraints:
        result["active_schema_field_constraints"] = [
            active_schema_constraints[key]
            for key in sorted(active_schema_constraints)
        ]
    if active_schema_violations:
        result["active_schema_field_violations"] = [
            active_schema_violations[key]
            for key in sorted(active_schema_violations)
        ]
    if causal_events:
        ordered_events = [causal_events[key] for key in sorted(causal_events)]
        result["causal_failure_events"] = ordered_events
        candidate_events = [
            event for event in ordered_events if event.get("owner") == "candidate"
        ]
        if candidate_events:
            result["failure_class"] = "candidate"
            result["repairable"] = all(
                event.get("repairable") is True for event in candidate_events
            )
            candidate_diagnostics: list[dict[str, object]] = []
            for event in candidate_events[:16]:
                diagnostic = {
                    key: event.get(key)
                    for key in (
                        "semantic_key",
                        "code",
                        "owner",
                        "stage",
                        "scope",
                        "repairable",
                        "category",
                        "capability_id",
                        "requirement_id",
                        "occurrence_count",
                        "affected_member_count",
                    )
                    if event.get(key) is not None
                }
                semantic_key = event.get("semantic_key")
                if isinstance(semantic_key, str):
                    repair_conformance = candidate_causal_contexts.get(
                        semantic_key
                    )
                    if repair_conformance is not None:
                        # The causal event remains payload-free and stable.  Its
                        # bounded repair contract is separate execution context
                        # needed to validate the next candidate rather than part
                        # of semantic failure identity.
                        diagnostic["repair_conformance"] = dict(
                            repair_conformance
                        )
                candidate_diagnostics.append(diagnostic)
            result["candidate_validation_diagnostics"] = candidate_diagnostics
            if violated_schema_constraint_ids:
                result["violated_schema_constraint_ids"] = sorted(
                    violated_schema_constraint_ids
                )
            completion_event_observed = any(
                event.get("code") == "target_behavior_completion_missing"
                for event in candidate_events
            )
            completed_operations = _diagnostic_completed_data_plane_operations(
                tuple(classification_views)
            )
            if completion_event_observed and completed_operations:
                result["interaction_progress"] = max(
                    4,
                    _diagnostic_interaction_progress(
                        tuple(classification_views)
                    ),
                )
                result["required_behaviors"] = [
                    "persist_first_successful_structured_evidence",
                    "write_manifest_before_additional_collection",
                    "verify_task_semantic_sufficiency_before_finalizing",
                    "do_not_treat_transport_success_as_task_completion",
                    "continue_bounded_acquisition_when_payload_is_only_metadata_or_execution_summary",
                    "stop_after_sufficient_evidence",
                    "return_bounded_evidence_ledger",
                ]
                candidate_diagnostics.insert(
                    0,
                    {
                        "code": "finalize_after_successful_endpoint_interaction",
                        "stage": "candidate_task_behavior",
                        "failure_class": "candidate",
                        "repairable": True,
                        "completed_data_plane_operations": list(
                            completed_operations
                        ),
                        "reason": (
                            "Paired replay proved that the supplied service completed "
                            "a data-plane interaction, but the candidate exhausted its "
                            "execution envelope before returning a bounded task result. "
                            "Preserve the verified support surface and repair the reusable "
                            "target instructions to persist sufficient evidence and finalize."
                        ),
                    },
                )
        else:
            typed_owners = {
                str(event.get("owner") or "")
                for event in ordered_events
                if event.get("owner")
            }
            if len(typed_owners) == 1:
                result["failure_class"] = next(iter(typed_owners))
            result["repairable"] = all(
                event.get("repairable") is True for event in ordered_events
            )
        # Typed ownership is authoritative.  Do not add generic confidence or
        # free-form classification noise when a concrete causal event exists.
        return result
    if len(failure_classes) == 1:
        result["failure_class"] = next(iter(failure_classes))
    if repairable_values:
        result["repairable"] = all(repairable_values)
    interaction_progress = _diagnostic_interaction_progress(
        tuple(classification_views)
    )
    routing_continuity_gaps = _diagnostic_routing_continuity_gaps(
        tuple(classification_views)
    )
    fixture_root_types = _diagnostic_fixture_root_types(
        tuple(classification_views)
    )
    observed_request_operations = _diagnostic_observed_request_operations(
        tuple(classification_views)
    )
    protocol_probe_mismatch = _diagnostic_protocol_probe_mismatch(
        tuple(classification_views)
    )
    completed_data_plane_operations = (
        _diagnostic_completed_data_plane_operations(
            tuple(classification_views)
        )
    )
    if interaction_progress:
        result["interaction_progress"] = interaction_progress
    diagnostic_text = json.dumps(
        classification_views,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    ).lower() + "\n" + "\n".join(classification_fragments).lower()
    # A timeout with observed protocol operations is already evidence that the
    # task reached the stateful/data plane, even when the trace did not expose
    # a numeric progress counter.  Preserve a minimum task-plane marker so the
    # next repair contract requires operation-aware fixture reconstruction and
    # the framework response-index binding instead of another readiness-only
    # candidate.
    if (
        "replay timed out" in diagnostic_text
        and observed_request_operations
        and interaction_progress < 4
    ):
        interaction_progress = 4
        result["interaction_progress"] = interaction_progress
    if (
        "permissionerror" in diagnostic_text
        or "permission denied" in diagnostic_text
    ):
        diagnostics.insert(
            0,
            {
                "code": "repair_candidate_output_permission_collision",
                "stage": "capability_compile",
                "failure_class": "candidate",
                "repairable": True,
                "reason": (
                    "The candidate compiler attempted to overwrite a generated output "
                    "whose mode was inherited from a read-only evidence source. Preserve "
                    "the recorded source bytes, but use a unique output path for each "
                    "handled requirement (including requirements that share an evidence "
                    "reference), or write without preserving source permissions. Ensure "
                    "every declared fixture path matches the file actually written."
                ),
            },
        )
    elif (
        "protocol_trace.jsonl" in diagnostic_text
        and any(
            marker in diagnostic_text
            for marker in (
                "missing required summary fields",
                "fields must be a list",
                "correlation must be an object",
                "direction must describe",
                "must record both received and emitted",
                "must contain one json object per line",
                "records must be json objects",
                "wrote an empty",
                "did not write",
            )
        )
    ):
        diagnostics.insert(
            0,
            {
                "code": "repair_protocol_trace_contract",
                "stage": "replay_capability",
                "failure_class": "candidate",
                "repairable": True,
                "reason": (
                    "Repair the candidate runtime's bounded protocol_trace.jsonl "
                    "writer. Every interaction record must be one JSON object with "
                    "direction, sequence, kind, fields, and correlation. fields must "
                    "be a list, correlation must be an object, and direction must "
                    "describe only a received/inbound or emitted/outbound interaction. "
                    "Record both sides of readiness and data-plane exchanges; do not "
                    "write lifecycle-only directions such as system. Keep payload "
                    "bodies and credentials out of the trace."
                ),
            },
        )
    elif "classification=recorded_response_selector_drift" in diagnostic_text:
        diagnostics.insert(
            0,
            {
                "code": "align_compiler_runtime_recorded_response_selection",
                "stage": "replay_capability",
                "failure_class": "candidate",
                "repairable": True,
                **protocol_probe_mismatch,
                "reason": (
                    "The runtime response already contains immutable recorded-response "
                    "evidence, but the compiler-declared response_contains assertion "
                    "comes from a different fixture selection path. Change both the "
                    "compiler probe builder and the runtime selector. They must share "
                    "one deterministic gateway, payload, decoding, ordering, and "
                    "fallback algorithm so the declared scalar is a descendant of the "
                    "exact recorded container returned by the runtime. Do not replace "
                    "the runtime's response-index projection with the mismatched "
                    "diagnostic preview, and do not hard-code either preview."
                ),
            },
        )
    elif "protocol probe response mismatch" in diagnostic_text:
        diagnostics.insert(
            0,
            {
                "code": "verify_declared_protocol_probe_branch",
                "stage": "replay_capability",
                "failure_class": "candidate",
                "repairable": True,
                **protocol_probe_mismatch,
                "observed_request_operations": list(
                    observed_request_operations
                ),
                "reason": (
                    "Execute the declared request_text against the exact handler branch "
                    "in the returned candidate source and verify semantic containment: "
                    "its decoded response must contain response_contains while preserving "
                    "the protocol envelope. Use the content-free expected/response "
                    "fingerprints, byte counts, and shapes to locate the failing branch; "
                    "response length does not need to equal expected length. Never reconstruct "
                    "or hard-code payload values from diagnostics. A differing fixture-derived value in the "
                    "runtime response indicates that compiler and runtime selected different "
                    "leaves. Use one canonical deterministic selector in compiler and runtime "
                    "with identical JSON/JSONL parsing, recursive traversal, filtering, ordering, "
                    "deduplication, and fallback semantics. Prefer sharing the selector source "
                    "or passing the compiled selected value through generated configuration so "
                    "the two sides cannot drift. A single selected leaf may be reused by multiple "
                    "probes; do not invent mapping-key or raw-token fallbacks merely to make each "
                    "probe token unique. Place the derived value in the exact declared "
                    "response branch and self-check the serialized bytes before returning. "
                    "Every declared probe is executed. Do not copy one assertion onto every "
                    "observed operation: remove redundant probes for branches not required by "
                    "exact_probe or the final late_observed_operation, while still implementing "
                    "those operations for the real task. "
                    "A rationale claim is not a repair unless that returned candidate "
                    "source branch actually changes."
                ),
            },
        )
    elif (
        "websocket frame is incomplete" in diagnostic_text
        or "connection closed before" in diagnostic_text
    ):
        observed_roots = (
            ", ".join(fixture_root_types) if fixture_root_types else "unknown"
        )
        diagnostics.insert(
            0,
            {
                "code": "diagnose_protocol_handler_abort",
                "stage": "replay_capability",
                "failure_class": "candidate",
                "repairable": True,
                "observed_fixture_root_types": list(fixture_root_types),
                "reason": (
                    "The protocol trace records an inbound request but no complete "
                    "outbound frame, so the candidate handler closed or raised before "
                    "serializing its response. Re-run the exact declared probe against "
                    "the returned source and surface a bounded sanitized exception in "
                    "stderr or protocol_trace.jsonl; do not swallow handler exceptions. "
                    f"Observed frozen fixture root types: {observed_roots}. "
                    "Treat fixture payloads as arbitrary JSON root types (object, array, "
                    "scalar, or invalid JSON) and normalize the root before mapping-only "
                    "operations such as .get(). Preserve the working handshake and frame "
                    "helpers while repairing the request handler branch."
                ),
            },
        )
    elif routing_continuity_gaps:
        diagnostics.insert(
            0,
            {
                "code": "preserve_protocol_routing_continuity",
                "stage": "replay_capability",
                "failure_class": "candidate",
                "repairable": True,
                "routing_fields": list(routing_continuity_gaps),
                "reason": (
                    "The protocol trace shows that opaque routing fields present on "
                    "an inbound request were dropped from its outbound interaction "
                    f"envelope: {', '.join(routing_continuity_gaps)}. Preserve each "
                    "field byte-for-byte on every response and follow-up event emitted "
                    "for that request. Keep payload bodies redacted in protocol_trace.jsonl, "
                    "but include these routing field names and opaque values in the "
                    "correlation summary so continuity is directly verifiable."
                ),
            },
        )
    elif (
        "replay timed out" in diagnostic_text
        and completed_data_plane_operations
        and "candidate_task_behavior" in diagnostic_text
    ):
        result["required_behaviors"] = [
            "persist_first_successful_structured_evidence",
            "write_manifest_before_additional_collection",
            "verify_task_semantic_sufficiency_before_finalizing",
            "do_not_treat_transport_success_as_task_completion",
            "continue_bounded_acquisition_when_payload_is_only_metadata_or_execution_summary",
            "stop_after_sufficient_evidence",
            "return_bounded_evidence_ledger",
        ]
        diagnostics.insert(
            0,
            {
                "code": "finalize_after_successful_endpoint_interaction",
                "stage": "candidate_task_behavior",
                "failure_class": "candidate",
                "repairable": True,
                "completed_data_plane_operations": list(
                    completed_data_plane_operations
                ),
                "reason": (
                    "The supplied replay service completed a bidirectional "
                    "non-control interaction, but the candidate continued until "
                    "the outer task timeout instead of returning a bounded result. "
                    "Preserve the verified replay runtime. Repair the reusable target "
                    "instructions so the first successful structured extraction is "
                    "persisted immediately, a valid evidence manifest is written before "
                    "additional collection, and the saved payload is checked for direct "
                    "semantic support of the requested claims. A handshake, HTTP success, "
                    "structured envelope, metadata record, or execution summary is a delivery "
                    "signal rather than task completion. If the payload is insufficient, use "
                    "one materially different bounded artifact-backed source or report that "
                    "insufficiency; stop only once sufficient evidence exists. Return only "
                    "the requested bounded result and evidence ledger. "
                    "Do not hard-code an operation, endpoint, task, or fixture value."
                ),
            },
        )
    elif (
        "discovery methods failed" in diagnostic_text
        or "failed to deserialize" in diagnostic_text
        or "missing field" in diagnostic_text
        or "doesn't implement the expected protocol" in diagnostic_text
        or "does not implement the expected protocol" in diagnostic_text
        or "websocket protocol error" in diagnostic_text
        or (
            "not a " in diagnostic_text
            and " endpoint" in diagnostic_text
        )
        or (
            "replay timed out" in diagnostic_text
            and bool(observed_request_operations)
        )
    ):
        diagnostics.insert(
            0,
            {
                "code": "implement_observed_endpoint_interactions",
                "stage": "replay_capability",
                "failure_class": "candidate",
                "repairable": True,
                "observed_request_operations": list(observed_request_operations),
                "reason": (
                    "The candidate's declared probes passed but the real task still "
                    "rejected the supplied endpoint protocol. Use bounded task diagnostics "
                    "and trace interaction summaries to implement the actual observed "
                    "task-plane stateful interactions and add representative probes for "
                    "them. The late observed request operations are: "
                    + (", ".join(observed_request_operations) or "unknown")
                    + ". Recursively traverse arbitrary fixture objects and arrays, select "
                    "the recorded evidence needed by those operations, and make at least one "
                    "representative probe assert non-empty fixture-derived response content. "
                    "Do not return placeholder tokens or empty schemas, and do not preserve "
                    "a readiness-only runtime merely because its self-declared probes pass."
                ),
            },
        )
    elif (
        "hung during navigation" in diagnostic_text
        or "still navigating" in diagnostic_text
        or "waiting for the page to load" in diagnostic_text
        or "正在导航" in diagnostic_text
        or "仍在导航" in diagnostic_text
        or "等待页面加载" in diagnostic_text
    ):
        diagnostics.insert(
            0,
            {
                "code": "implement_async_endpoint_completion",
                "stage": "replay_capability",
                "failure_class": "candidate",
                "repairable": True,
                "reason": (
                    "The candidate handled synchronous endpoint requests but the real "
                    "task remained blocked waiting for completion. Preserve the working "
                    "request/response branches, then implement the observed stateful "
                    "interactions, including asynchronous completion or lifecycle "
                    "notifications required after a synchronous response. Preserve opaque "
                    "request correlation and routing metadata on both the response and its "
                    "follow-up events when the protocol multiplexes sessions or channels; "
                    "echoing only the numeric request id can leave the client waiting. Add a bounded "
                    "probe that verifies the completion notification rather than only "
                    "readiness or the initial response."
                ),
            },
        )
    if diagnostics:
        result["candidate_validation_diagnostics"] = diagnostics[:16]
    return result


def _public_replay_counterexamples(value: object) -> tuple[dict[str, object], ...]:
    """Extract bounded, payload-free counterexamples from gate diagnostics."""

    pending: list[tuple[object, int]] = [(value, 0)]
    result: list[dict[str, object]] = []
    visited = 0
    while pending and visited < 512 and len(result) < 16:
        current, depth = pending.pop()
        visited += 1
        if depth > 8:
            continue
        if isinstance(current, Mapping):
            raw = current.get("replay_counterexamples")
            if isinstance(raw, (list, tuple)):
                for item in raw[:16]:
                    normalized = _public_replay_counterexample(item)
                    if normalized is not None and normalized not in result:
                        result.append(normalized)
            for nested in current.values():
                if isinstance(nested, (Mapping, list, tuple)):
                    pending.append((nested, depth + 1))
        elif isinstance(current, (list, tuple)):
            for nested in current[:128]:
                if isinstance(nested, (Mapping, list, tuple)):
                    pending.append((nested, depth + 1))
    return tuple(result)


def _public_replay_counterexample(
    value: object,
) -> dict[str, object] | None:
    return normalize_counterexample(value)


def _diagnostic_completed_data_plane_operations(value: Any) -> tuple[str, ...]:
    operations: list[str] = []

    def visit(item: Any, *, depth: int = 0) -> None:
        if depth > 8 or len(operations) >= 32:
            return
        if isinstance(item, Mapping):
            raw = item.get("completed_data_plane_operations")
            if isinstance(raw, (list, tuple)):
                for operation in raw[:32]:
                    text = str(operation or "").strip()
                    if text and text not in operations:
                        operations.append(text)
            for nested in item.values():
                if isinstance(nested, (Mapping, list, tuple)):
                    visit(nested, depth=depth + 1)
        elif isinstance(item, (list, tuple)):
            for nested in item[:128]:
                visit(nested, depth=depth + 1)

    visit(value)
    return tuple(operations)


def _diagnostic_protocol_probe_mismatch(value: Any) -> dict[str, str]:
    """Parse only typed, content-free probe evidence from diagnostics."""

    messages: list[str] = []

    def collect(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, nested in list(item.items())[:128]:
                if (
                    str(key) in {"detail", "error", "message", "reason"}
                    and isinstance(nested, str)
                    and "protocol probe response mismatch" in nested.lower()
                ):
                    messages.append(nested)
                else:
                    collect(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item[:256]:
                collect(nested)

    collect(value)
    for message in reversed(messages[-16:]):
        parsed: dict[str, str] = {}
        fields = {
            match.group(1): match.group(2)
            for match in re.finditer(r"\b([a-z0-9_]+)=([^\s]+)", message)
        }
        if "kind" in fields:
            parsed["probe_kind"] = sanitize_text(fields["kind"], max_chars=40)
        if "path" in fields:
            parsed["probe_path"] = sanitize_text(fields["path"], max_chars=160)
        for field in (
            "expected_sha256",
            "expected_bytes",
            "expected_shape",
            "response_sha256",
            "response_bytes",
            "response_payload_bytes",
            "response_shape",
            "classification",
        ):
            if field in fields:
                parsed[field] = sanitize_text(fields[field], max_chars=160)
        if parsed:
            return parsed
    return {}


def _diagnostic_observed_request_operations(value: Any) -> tuple[str, ...]:
    """Extract bounded, payload-free operation names from protocol trace tails."""

    trace_tails: list[str] = []

    def collect_tails(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, nested in list(item.items())[:128]:
                if str(key) == "tail" and isinstance(nested, str):
                    trace_tails.append(nested)
                else:
                    collect_tails(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item[:256]:
                collect_tails(nested)

    collect_tails(value)
    observed: list[tuple[int, str]] = []
    operation_keys = {"action", "command", "method", "operation", "path", "route"}
    inbound_directions = {"in", "inbound", "receive", "received", "recv"}
    transport_methods = {
        "CONNECT",
        "DELETE",
        "GET",
        "HEAD",
        "OPTIONS",
        "PATCH",
        "POST",
        "PUT",
        "TRACE",
    }

    def record_operation(raw_operation: str, *, sequence_number: int) -> None:
        operation = sanitize_text(raw_operation, max_chars=120).strip()
        if not operation or operation.upper() in transport_methods:
            return
        observed.append((sequence_number, operation))

    def collect_operations(
        source: Any,
        *,
        sequence_number: int,
        depth: int = 0,
    ) -> None:
        if depth > 4:
            return
        if isinstance(source, Mapping):
            for key, nested in list(source.items())[:64]:
                normalized_key = str(key).strip().lower()
                if normalized_key in operation_keys and isinstance(nested, str):
                    record_operation(
                        nested,
                        sequence_number=sequence_number,
                    )
                    continue
                if isinstance(nested, (Mapping, list, tuple)):
                    collect_operations(
                        nested,
                        sequence_number=sequence_number,
                        depth=depth + 1,
                    )
            return
        if isinstance(source, (list, tuple)):
            for nested in source[:64]:
                collect_operations(
                    nested,
                    sequence_number=sequence_number,
                    depth=depth + 1,
                )
            return
        if not isinstance(source, str):
            return
        field_name, separator, field_value = source.partition(":")
        if not separator:
            field_name, separator, field_value = source.partition("=")
        if field_name.strip().lower() not in operation_keys:
            return
        record_operation(
            field_value,
            sequence_number=sequence_number,
        )

    for tail in trace_tails[:16]:
        for raw_line in tail.splitlines()[-256:]:
            try:
                record = json.loads(raw_line)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(record, Mapping):
                continue
            direction = str(record.get("direction") or "").strip().lower()
            kind = str(record.get("kind") or "").strip().lower()
            if direction not in inbound_directions and "request" not in kind:
                continue
            sequence = record.get("sequence")
            sequence_number = (
                int(sequence)
                if isinstance(sequence, (int, float)) and not isinstance(sequence, bool)
                else 0
            )
            collect_operations(
                record,
                sequence_number=sequence_number,
            )

    ordered: list[str] = []
    for _, operation in sorted(observed, key=lambda item: item[0]):
        if operation in ordered:
            ordered.remove(operation)
        ordered.append(operation)
    return tuple(ordered[-8:])


def _diagnostic_fixture_root_types(value: Any) -> tuple[str, ...]:
    """Collect bounded non-content fixture shape metadata from failure details."""

    root_types: set[str] = set()

    def collect(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, nested in list(item.items())[:128]:
                if str(key) == "json_root_type" and isinstance(nested, str):
                    normalized = nested.strip().lower()
                    if normalized:
                        root_types.add(normalized)
                else:
                    collect(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item[:256]:
                collect(nested)

    collect(value)
    return tuple(sorted(root_types))


def _diagnostic_routing_continuity_gaps(value: Any) -> tuple[str, ...]:
    """Infer dropped opaque routing fields from candidate-owned trace summaries."""

    trace_tails: list[str] = []

    def collect(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, nested in item.items():
                if str(key) == "tail" and isinstance(nested, str):
                    trace_tails.append(nested)
                else:
                    collect(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item[:256]:
                collect(nested)

    collect(value)
    gaps: set[str] = set()
    for tail in trace_tails[:16]:
        pending_routing_fields: set[str] = set()
        for line in tail.splitlines()[-256:]:
            try:
                record = json.loads(line)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(record, Mapping):
                continue
            direction = str(record.get("direction") or "").strip().lower()
            raw_fields = record.get("fields")
            fields = {
                str(field)
                for field in raw_fields
                if isinstance(field, str) and field
            } if isinstance(raw_fields, list) else set()
            correlation = record.get("correlation")
            correlation_fields = (
                {
                    str(key)
                    for key, nested in correlation.items()
                    if nested is not None and nested != ""
                }
                if isinstance(correlation, Mapping)
                else set()
            )
            if direction in {"in", "inbound", "received", "receive", "recv"}:
                pending_routing_fields = {
                    field
                    for field in fields | correlation_fields
                    if _looks_like_protocol_routing_field(field)
                }
                continue
            if direction not in {
                "out",
                "outbound",
                "emitted",
                "emit",
                "send",
                "sent",
            }:
                continue
            if pending_routing_fields:
                gaps.update(
                    pending_routing_fields.difference(fields | correlation_fields)
                )
    return tuple(sorted(gaps))


def _looks_like_protocol_routing_field(field_name: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", field_name.lower())
    if not normalized or normalized == "id":
        return False
    return any(
        marker in normalized
        for marker in (
            "session",
            "channel",
            "route",
            "routing",
            "stream",
            "correlation",
            "connection",
        )
    )


def _diagnostic_interaction_progress(value: Any) -> int:
    max_sequence = 0

    def visit(item: Any) -> None:
        nonlocal max_sequence
        if isinstance(item, Mapping):
            for key, nested in item.items():
                if (
                    str(key) in {"sequence", "seq"}
                    and not isinstance(nested, bool)
                    and isinstance(nested, (int, float))
                ):
                    max_sequence = max(max_sequence, int(nested))
                elif str(key) == "tail" and isinstance(nested, str):
                    for line in nested.splitlines()[-256:]:
                        try:
                            parsed = json.loads(line)
                        except (TypeError, ValueError, json.JSONDecodeError):
                            continue
                        visit(parsed)
                else:
                    visit(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item[:256]:
                visit(nested)

    visit(value)
    return max_sequence
