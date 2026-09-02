"""Run-level failure attribution and candidate-generation diagnostics."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence

from aworld.self_evolve.campaign_policy import (
    gate_has_candidate_owned_repair as _gate_has_candidate_owned_repair,
)
from aworld.self_evolve.candidate_errors import (
    candidate_materialization_requirement_id,
    normalize_candidate_contract_fingerprint,
    normalize_candidate_failure_field,
    normalize_candidate_materialization_code,
    normalize_candidate_representation,
)
from aworld.self_evolve.failure_events import (
    FailureOwner,
    FailureScope,
    FailureStage,
    ReplayFailureEvent,
)
from aworld.self_evolve.history_support import (
    _non_negative_numeric_int as _non_negative_int,
)
from aworld.self_evolve.optimizers.base import (
    CandidateGenerationOutcome,
    CandidateGenerationOutcomeKind,
)
from aworld.self_evolve.sanitization import (
    public_diagnostic_projection,
    sanitize_text,
)
from aworld.self_evolve.schema_diagnostics import (
    _repair_contract_fingerprint,
    _schema_field_contract_fingerprint,
)
from aworld.self_evolve.types import (
    CandidateVariant,
    GateResult,
    SelfEvolveRunStatus,
)

_MAX_CONSECUTIVE_POLICY_FILTER_STALLS = 2


def _candidate_materialization_failures(
    diagnostics: Mapping[str, object],
) -> tuple[dict[str, object], ...]:
    raw_failures = diagnostics.get("candidate_materialization_failures")
    if not isinstance(raw_failures, (list, tuple)):
        return ()
    failures: list[dict[str, object]] = []
    for item in raw_failures[:16]:
        if not isinstance(item, Mapping):
            continue
        code = normalize_candidate_materialization_code(item.get("code")).value
        representation = normalize_candidate_representation(
            item.get("representation")
        ).value
        field_path = normalize_candidate_failure_field(item.get("field_path")).value
        raw_stage = str(item.get("stage") or "").strip()
        failure = {
            "code": code,
            "stage": (
                raw_stage
                if raw_stage
                in {
                    "candidate_generation",
                    "candidate_protocol",
                    "candidate_semantic_validation",
                }
                else "candidate_generation"
            ),
            "failure_class": "candidate",
            "repairable": item.get("repairable") is not False,
            "candidate_index": _non_negative_int(item.get("candidate_index")),
            "representation": representation,
            "field_path": field_path,
            "reason": sanitize_text(item.get("reason"), max_chars=240),
        }
        contract_fingerprint = normalize_candidate_contract_fingerprint(
            item.get("contract_fingerprint")
        )
        if contract_fingerprint is not None:
            failure["contract_fingerprint"] = contract_fingerprint
        raw_details = item.get("details")
        if isinstance(raw_details, Mapping):
            public_details = public_diagnostic_projection(raw_details)
            if isinstance(public_details, Mapping):
                failure["details"] = dict(public_details)
        raw_allowed_ids = item.get("allowed_improvement_signal_ids")
        if isinstance(raw_allowed_ids, (list, tuple)):
            failure["allowed_improvement_signal_ids"] = [
                sanitize_text(value, max_chars=512)
                for value in raw_allowed_ids[:256]
                if isinstance(value, str) and value
            ]
        failures.append(failure)
    return tuple(failures)

def _candidate_materialization_failure_event(
    failure: Mapping[str, object],
) -> dict[str, object]:
    code = normalize_candidate_materialization_code(failure.get("code")).value
    field_path = normalize_candidate_failure_field(failure.get("field_path")).value
    representation = normalize_candidate_representation(
        failure.get("representation")
    ).value
    contract_fingerprint = normalize_candidate_contract_fingerprint(
        failure.get("contract_fingerprint")
    )
    event = ReplayFailureEvent(
        code=code,
        owner=FailureOwner.CANDIDATE,
        stage=FailureStage.CANDIDATE_GENERATION,
        scope=FailureScope.CANDIDATE,
        repairable=failure.get("repairable") is not False,
        category="candidate_generation",
        summary="candidate package could not be materialized",
        diagnostics={
            "field_path": field_path,
            "representation": representation,
        },
        requirement_id=candidate_materialization_requirement_id(
            representation=representation,
            field_path=field_path,
        ),
        contract_fingerprint=(contract_fingerprint),
    )
    return event.to_dict()

def _candidate_materialization_failure_events(
    failures: Iterable[Mapping[str, object]],
) -> tuple[dict[str, object], ...]:
    events: list[dict[str, object]] = []
    seen_semantic_keys: set[str] = set()
    for failure in failures:
        event = _candidate_materialization_failure_event(failure)
        semantic_key = str(event["semantic_key"])
        if semantic_key in seen_semantic_keys:
            continue
        seen_semantic_keys.add(semantic_key)
        events.append(event)
    return tuple(events)

def _candidate_policy_filter_event(
    outcome: CandidateGenerationOutcome,
) -> dict[str, object]:
    constraint_identity = json.dumps(
        {
            "policy_id": outcome.policy_id,
            "constraint_ids": list(outcome.constraint_ids),
            "enforcement": outcome.enforcement,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    event = ReplayFailureEvent(
        code="candidate_generation_policy_filtered",
        owner=FailureOwner.CANDIDATE,
        stage=FailureStage.CANDIDATE_GENERATION,
        scope=FailureScope.CANDIDATE,
        repairable=outcome.repairable,
        category="candidate_generation_policy",
        summary="candidate violated a deterministic generation policy",
        diagnostics={
            "policy_id": outcome.policy_id,
            "enforcement": outcome.enforcement,
            "reason_codes": list(outcome.reason_codes),
            "constraint_ids": list(outcome.constraint_ids),
            "active_frontier_key": outcome.active_frontier_key,
            "affected_case_ids": list(outcome.affected_case_ids),
            "candidate_fingerprint": outcome.candidate_fingerprint,
            "semantic_fingerprint": outcome.semantic_fingerprint,
            "strategy_id": outcome.strategy_id,
        },
        requirement_id=(
            "candidate-policy:sha256:"
            + hashlib.sha256(constraint_identity.encode("utf-8")).hexdigest()
        ),
    )
    return event.to_dict()

def _candidate_policy_filter_signature(
    outcomes: Sequence[CandidateGenerationOutcome],
) -> str | None:
    policy_outcomes = [
        outcome
        for outcome in outcomes
        if outcome.kind is CandidateGenerationOutcomeKind.POLICY_FILTERED
    ]
    if not policy_outcomes:
        return None
    payload = sorted(
        {
            (
                str(outcome.policy_id),
                str(outcome.enforcement),
                tuple(sorted(outcome.reason_codes)),
                tuple(sorted(outcome.constraint_ids)),
                tuple(sorted(outcome.affected_case_ids)),
            )
            for outcome in policy_outcomes
        }
    )
    return (
        "candidate-policy-filter:sha256:"
        + hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    )

def _retryable_candidate_generation_failure(
    failure: Mapping[str, object],
) -> bool:
    error_type = str(failure.get("error_type") or "").strip().casefold()
    stage = str(failure.get("stage") or "").strip().casefold()
    if stage not in {"model_provider", "model_response"}:
        return False
    return error_type in {
        "apiconnectionerror",
        "apitimeouterror",
        "connectionerror",
        "llmresponseerror",
        "ratelimiterror",
        "timeouterror",
    }

def _optimizer_iteration_diagnostics(
    optimizer_diagnostics: Iterable[Mapping[str, object]],
) -> Iterable[Mapping[str, object]]:
    for item in optimizer_diagnostics:
        diagnostics = item.get("diagnostics")
        if isinstance(diagnostics, Mapping):
            yield diagnostics

def _status_without_selected_candidate(
    optimizer_diagnostics: list[dict[str, object]],
) -> SelfEvolveRunStatus:
    infrastructure_failure = False
    candidate_owned_outcome = False
    candidate_outcome_keys = {
        "candidate_protocol_invalid_count",
        "filtered_invalid_patch_candidates",
        "filtered_noop_candidates",
        "filtered_high_baseline_regression_candidates",
        "filtered_duplicate_candidates",
        "filtered_known_duplicate_candidates",
        "filtered_semantic_lesson_duplicate_candidates",
    }
    for diagnostics in _optimizer_iteration_diagnostics(optimizer_diagnostics):
        if isinstance(diagnostics.get("candidate_generation_failure"), Mapping):
            infrastructure_failure = True
        if any(
            _non_negative_int(diagnostics.get(key)) > 0
            for key in candidate_outcome_keys
        ):
            candidate_owned_outcome = True
    if infrastructure_failure and not candidate_owned_outcome:
        return SelfEvolveRunStatus.FAILED
    return SelfEvolveRunStatus.REJECTED

def _candidate_generation_failure_events(
    optimizer_diagnostics: Iterable[Mapping[str, object]],
) -> tuple[dict[str, object], ...]:
    failures: list[dict[str, object]] = []
    policy_events: list[dict[str, object]] = []
    protocol_events: list[dict[str, object]] = []
    for item in _optimizer_iteration_diagnostics(optimizer_diagnostics):
        failures.extend(_candidate_materialization_failures(item))
        policy_events.extend(_candidate_policy_filter_events(item))
        protocol_events.extend(_candidate_protocol_failure_events(item))
    materialization_events = _candidate_materialization_failure_events(failures)
    events: list[dict[str, object]] = []
    seen_semantic_keys: set[str] = set()
    for event in (*materialization_events, *policy_events, *protocol_events):
        semantic_key = str(event["semantic_key"])
        if semantic_key in seen_semantic_keys:
            continue
        seen_semantic_keys.add(semantic_key)
        events.append(event)
    return tuple(events)

def _candidate_protocol_failure_events(
    diagnostics: Mapping[str, object],
) -> tuple[dict[str, object], ...]:
    raw_outcomes = diagnostics.get("candidate_generation_outcomes")
    if not isinstance(raw_outcomes, (list, tuple)):
        return ()
    events: list[dict[str, object]] = []
    seen_semantic_keys: set[str] = set()
    for item in raw_outcomes[:64]:
        if not isinstance(item, Mapping):
            continue
        try:
            outcome = CandidateGenerationOutcome.from_dict(item)
        except (TypeError, ValueError):
            continue
        if outcome.kind is not CandidateGenerationOutcomeKind.PROTOCOL_INVALID:
            continue
        code = (
            outcome.reason_codes[0]
            if outcome.reason_codes
            else ("candidate_protocol_invalid")
        )
        event = ReplayFailureEvent(
            code=code,
            owner=FailureOwner.CANDIDATE,
            stage=FailureStage.CANDIDATE_GENERATION,
            scope=FailureScope.CANDIDATE,
            repairable=outcome.repairable,
            category="candidate_generation",
            summary="candidate response violated the generation protocol",
            diagnostics={
                "candidate_index": outcome.candidate_index,
                "active_frontier_key": outcome.active_frontier_key,
            },
            requirement_id=f"candidate-protocol/{code}",
        ).to_dict()
        semantic_key = str(event["semantic_key"])
        if semantic_key in seen_semantic_keys:
            continue
        seen_semantic_keys.add(semantic_key)
        events.append(event)
    return tuple(events)

def _candidate_policy_filter_outcomes(
    diagnostics: Mapping[str, object],
) -> tuple[CandidateGenerationOutcome, ...]:
    raw_outcomes = diagnostics.get("candidate_generation_outcomes")
    if not isinstance(raw_outcomes, (list, tuple)):
        return ()
    outcomes: list[CandidateGenerationOutcome] = []
    for item in raw_outcomes[:64]:
        if not isinstance(item, Mapping):
            continue
        try:
            outcome = CandidateGenerationOutcome.from_dict(item)
        except (TypeError, ValueError):
            continue
        if outcome.kind is CandidateGenerationOutcomeKind.POLICY_FILTERED:
            outcomes.append(outcome)
    return tuple(outcomes)

def _candidate_policy_frontier_stalled_event(
    outcomes: Sequence[CandidateGenerationOutcome],
) -> dict[str, object]:
    policy_ids = tuple(
        sorted({str(outcome.policy_id) for outcome in outcomes if outcome.policy_id})
    )
    constraint_ids = tuple(
        sorted(
            {
                constraint_id
                for outcome in outcomes
                for constraint_id in outcome.constraint_ids
            }
        )
    )
    signature = _candidate_policy_filter_signature(outcomes) or "unknown"
    event = ReplayFailureEvent(
        code="candidate_generation_policy_frontier_stalled",
        owner=FailureOwner.CANDIDATE,
        stage=FailureStage.CANDIDATE_GENERATION,
        scope=FailureScope.CANDIDATE,
        repairable=False,
        category="candidate_generation_policy",
        summary="generation policy frontier repeated without structural progress",
        diagnostics={
            "policy_ids": list(policy_ids),
            "constraint_ids": list(constraint_ids),
            "filter_signature": signature,
            "consecutive_stall_limit": _MAX_CONSECUTIVE_POLICY_FILTER_STALLS,
        },
        requirement_id=signature,
    )
    return event.to_dict()

def _candidate_policy_filter_events(
    diagnostics: Mapping[str, object],
) -> tuple[dict[str, object], ...]:
    events: list[dict[str, object]] = []
    seen_semantic_keys: set[str] = set()
    for outcome in _candidate_policy_filter_outcomes(diagnostics):
        event = _candidate_policy_filter_event(outcome)
        semantic_key = str(event["semantic_key"])
        if semantic_key in seen_semantic_keys:
            continue
        seen_semantic_keys.add(semantic_key)
        events.append(event)
    return tuple(events)

def _repair_contract_fingerprints(
    details: Mapping[str, object],
) -> tuple[str, ...]:
    """Return full and component identities for frontier-resolution matching."""

    fingerprints: set[str] = set()
    direct = _schema_field_contract_fingerprint(details)
    if direct is not None:
        fingerprints.add(direct)
    projected = details.get("repair_conformance")
    if isinstance(projected, Mapping):
        combined = _schema_field_contract_fingerprint(projected)
        if combined is not None:
            fingerprints.add(combined)
        for field_name in (
            "schema_field_constraints",
            "runtime_response_constraints",
            "runtime_artifact_constraints",
        ):
            component = _schema_field_contract_fingerprint(
                {field_name: projected.get(field_name)}
            )
            if component is not None:
                fingerprints.add(component)
    return tuple(sorted(fingerprints))

def _terminal_cause(
    *,
    final_status: SelfEvolveRunStatus,
    optimizer_diagnostics: list[dict[str, object]],
    gate_results: Iterable[GateResult],
) -> dict[str, object] | None:
    if final_status is not SelfEvolveRunStatus.FAILED:
        return None
    for diagnostics in reversed(
        list(_optimizer_iteration_diagnostics(optimizer_diagnostics))
    ):
        failure = diagnostics.get("candidate_generation_failure")
        if not isinstance(failure, Mapping):
            continue
        cause: dict[str, object] = {
            "failure_class": "infrastructure",
            "stage": "candidate_generation",
            "code": str(
                failure.get("code") or "candidate_generation_infrastructure_error"
            ),
            "retryable": _retryable_candidate_generation_failure(failure),
        }
        error_type = failure.get("error_type")
        if isinstance(error_type, str) and error_type:
            cause["error_type"] = error_type
        return cause
    for gate in gate_results:
        details = gate.details
        if (
            gate.passed
            or not isinstance(details, Mapping)
            or details.get("failure_class") != "infrastructure"
        ):
            continue
        cause = {
            "failure_class": "infrastructure",
            "stage": gate.gate_name,
            "code": str(details.get("code") or "infrastructure_error"),
            "retryable": _retryable_infrastructure_details(details),
        }
        error_type = details.get("type")
        if isinstance(error_type, str) and error_type:
            cause["error_type"] = error_type
        return cause
    return {
        "failure_class": "infrastructure",
        "stage": "self_evolve",
        "code": "infrastructure_error",
        "retryable": False,
    }

def _retryable_infrastructure_details(details: Mapping[str, object]) -> bool:
    if details.get("retryable") is True or details.get("repairable") is True:
        return True
    error_type = (
        str(details.get("error_type") or details.get("type") or "").strip().casefold()
    )
    return error_type in {
        "apiconnectionerror",
        "apitimeouterror",
        "connectionerror",
        "llmresponseerror",
        "ratelimiterror",
        "timeouterror",
    }

def _rejection_attribution(
    *,
    final_status: SelfEvolveRunStatus,
    selected_candidate_id: str | None,
    gate_results: Iterable[GateResult],
    scheduler_decisions: Sequence[Mapping[str, object]],
) -> dict[str, object] | None:
    if final_status is not SelfEvolveRunStatus.REJECTED:
        return None
    failed = [gate for gate in gate_results if not gate.passed]
    if not failed:
        return None
    substantive = [
        gate
        for gate in failed
        if gate.gate_name
        not in {
            "duplicate_accepted_candidate",
            "duplicate_rejected_candidate",
            "candidate_generation_exhausted_by_semantic_dedup",
        }
    ]
    # Gate execution order must not decide campaign ownership.  Prefer an
    # actionable candidate repair over a simultaneous framework uncertainty
    # signal (for example evidence_quality plus noisy score evidence).
    actionable_candidate_failures = [
        gate for gate in substantive if _gate_has_candidate_owned_repair(gate)
    ]
    primary = (
        actionable_candidate_failures[0]
        if actionable_candidate_failures
        else substantive[0]
        if substantive
        else failed[0]
    )
    details = primary.details if isinstance(primary.details, Mapping) else {}
    attribution: dict[str, object] = {
        "candidate_id": selected_candidate_id,
        "primary_gate": primary.gate_name,
        "primary_reason": sanitize_text(primary.reason, max_chars=400),
        "failure_class": str(details.get("failure_class") or "candidate"),
        "code": str(details.get("code") or primary.gate_name),
        "duplicate_only": not substantive,
    }
    for key in (
        "failure_owner",
        "failure_scope",
        "failure_stage",
        "repairable",
        "next_action",
        "resume_safe",
        "resume_candidate_id",
        "resume_candidate_package_fingerprint",
        "completed_baseline_case_count",
        "completed_candidate_case_count",
        "completed_comparable_pair_count",
        "pending_case_count",
    ):
        if details.get(key) is not None:
            attribution[key] = details[key]
    diagnostic_refs = _attribution_diagnostic_refs(details)
    if diagnostic_refs:
        attribution["diagnostic_refs"] = list(diagnostic_refs)
    capability_error_code = details.get("capability_error_code")
    if isinstance(capability_error_code, str) and capability_error_code:
        attribution["capability_error_code"] = capability_error_code
    if scheduler_decisions:
        terminal_decision = scheduler_decisions[-1]
        scheduler_reason_code = str(terminal_decision.get("reason_code") or "unknown")
        attribution["scheduler_reason_code"] = scheduler_reason_code
        attribution["scheduler_stop"] = terminal_decision.get("stop") is True
        if (
            attribution["scheduler_stop"] is True
            and scheduler_reason_code == "shared_run_blocked"
        ):
            attribution["failure_class"] = "framework"
            attribution["code"] = "shared_run_blocked"
        if (
            attribution["scheduler_stop"] is True
            and scheduler_reason_code == "repair_frontier_stalled"
            and primary.gate_name in {"candidate_generation", "no_candidate"}
        ):
            attribution["code"] = "candidate_repair_frontier_stalled"
    return attribution

def _campaign_failure_attribution(
    iteration_states: Iterable[Mapping[str, object]],
    *,
    generation_stop_reason: str | None,
    terminal_gates: Iterable[GateResult] = (),
    resolved_contract_fingerprints: Iterable[str] = (),
) -> dict[str, object] | None:
    """Attribute a rejected search to its dominant typed failure frontier.

    ``rejection_attribution`` explains the selected representative candidate.
    A campaign can reject many candidates before that selection, so using only
    the representative can surface an incidental Markdown error while hiding a
    repeated compiler/runtime frontier.  This aggregate is candidate-deduped and
    keeps the two concepts separate.
    """

    resolved_contracts = set(resolved_contract_fingerprints)
    for gate in terminal_gates:
        if gate.passed or not isinstance(gate.details, Mapping):
            continue
        details = gate.details
        owner = str(details.get("failure_owner") or "")
        scope = str(details.get("failure_scope") or "")
        failure_class = str(details.get("failure_class") or "")
        if (
            owner in {"framework", "infrastructure"}
            and scope == "shared_run"
            and failure_class in {"framework", "infrastructure", "measurement"}
        ):
            result: dict[str, object] = {
                "primary_gate": gate.gate_name,
                "code": str(details.get("code") or gate.gate_name),
                "failure_class": failure_class,
                "failure_owner": owner,
                "failure_scope": scope,
                "primary_reason": sanitize_text(gate.reason, max_chars=400),
                "occurrence_count": 1,
                "affected_candidate_count": 0,
                "affected_candidate_ids": [],
                "resolved_failure_count": len(resolved_contracts),
            }
            for key in (
                "next_action",
                "repairable",
                "failure_stage",
                "resume_safe",
                "resume_candidate_id",
                "resume_candidate_package_fingerprint",
                "completed_baseline_case_count",
                "completed_candidate_case_count",
                "completed_comparable_pair_count",
                "pending_case_count",
            ):
                if details.get(key) is not None:
                    result[key] = details[key]
            diagnostic_refs = _attribution_diagnostic_refs(details)
            if diagnostic_refs:
                result["diagnostic_refs"] = list(diagnostic_refs)
            if generation_stop_reason is not None:
                result["generation_stop_reason"] = generation_stop_reason
            return result

    groups: dict[
        tuple[str, str, str, str | None],
        dict[str, object],
    ] = {}
    seen_attempts: set[tuple[str, str, str, str | None]] = set()
    for state in iteration_states:
        # A verified evaluation-support package is an intermediate prerequisite,
        # not a rejected campaign frontier.  Its target_behavior_delta gate is
        # intentionally deferred until the composed behavior candidate exists.
        # Counting it as a terminal failure can hide the later authoritative
        # candidate's real replay failure when both occur once.
        if state.get("status") == "prerequisite":
            continue
        candidate = state.get("candidate")
        candidate_id = (
            candidate.candidate_id if isinstance(candidate, CandidateVariant) else None
        )
        raw_gates = state.get("gate_results")
        if not isinstance(raw_gates, (list, tuple)):
            continue
        for gate in raw_gates:
            if not isinstance(gate, GateResult) or gate.passed:
                continue
            if gate.gate_name in {
                "duplicate_accepted_candidate",
                "duplicate_rejected_candidate",
            }:
                continue
            details = gate.details if isinstance(gate.details, Mapping) else {}
            code = str(details.get("code") or gate.gate_name)
            contract_fingerprint = _repair_contract_fingerprint(details)
            if (
                gate.gate_name == "candidate_repair_conformance"
                and contract_fingerprint in resolved_contracts
            ):
                continue
            attempt_identity = (
                candidate_id or "<none>",
                gate.gate_name,
                code,
                contract_fingerprint,
            )
            if attempt_identity in seen_attempts:
                continue
            seen_attempts.add(attempt_identity)
            key = (
                gate.gate_name,
                code,
                str(details.get("failure_class") or "candidate"),
                contract_fingerprint,
            )
            group = groups.setdefault(
                key,
                {
                    "primary_gate": gate.gate_name,
                    "code": code,
                    "failure_class": str(details.get("failure_class") or "candidate"),
                    "primary_reason": sanitize_text(gate.reason, max_chars=400),
                    "occurrence_count": 0,
                    "candidate_ids": set(),
                    "contract_fingerprint": contract_fingerprint,
                    "failure_owner": details.get("failure_owner"),
                    "failure_scope": details.get("failure_scope"),
                    "failure_stage": details.get("failure_stage"),
                    "repairable": details.get("repairable"),
                    "next_action": details.get("next_action"),
                    "diagnostic_refs": set(_attribution_diagnostic_refs(details)),
                },
            )
            refs = group.get("diagnostic_refs")
            if isinstance(refs, set):
                refs.update(_attribution_diagnostic_refs(details))
            group["occurrence_count"] = int(group["occurrence_count"]) + 1
            if candidate_id is not None:
                candidate_ids = group["candidate_ids"]
                assert isinstance(candidate_ids, set)
                candidate_ids.add(candidate_id)
    if not groups:
        return None
    primary = dict(
        max(
            groups.values(),
            key=lambda item: (
                len(item["candidate_ids"]),
                int(item["occurrence_count"]),
                item["failure_class"] == "candidate",
                str(item["primary_gate"]),
            ),
        )
    )
    candidate_ids = primary.pop("candidate_ids")
    diagnostic_refs = primary.pop("diagnostic_refs", set())
    assert isinstance(candidate_ids, set)
    result = {
        **primary,
        "affected_candidate_count": len(candidate_ids),
        "affected_candidate_ids": sorted(candidate_ids)[:16],
        "resolved_failure_count": len(resolved_contracts),
    }
    if isinstance(diagnostic_refs, set) and diagnostic_refs:
        result["diagnostic_refs"] = sorted(diagnostic_refs)[:16]
    for optional_key in (
        "failure_owner",
        "failure_scope",
        "failure_stage",
        "repairable",
        "next_action",
    ):
        if result.get(optional_key) is None:
            result.pop(optional_key, None)
    if result.get("contract_fingerprint") is None:
        result.pop("contract_fingerprint", None)
    if generation_stop_reason is not None:
        result["generation_stop_reason"] = generation_stop_reason
    return result

def _attribution_diagnostic_refs(
    value: object,
) -> tuple[str, ...]:
    """Collect bounded artifact references from typed failure details."""

    refs: set[str] = set()
    pending: list[tuple[object, int]] = [(value, 0)]
    visited = 0
    while pending and visited < 512 and len(refs) < 16:
        current, depth = pending.pop()
        visited += 1
        if depth > 8:
            continue
        if isinstance(current, Mapping):
            for key in ("artifact_refs", "diagnostic_refs", "evidence_refs"):
                raw = current.get(key)
                if not isinstance(raw, (list, tuple)):
                    continue
                for item in raw[:16]:
                    text = str(item).strip()
                    if text and "\n" not in text and "\r" not in text:
                        refs.add(text[:500])
            for nested in current.values():
                if isinstance(nested, (Mapping, list, tuple)):
                    pending.append((nested, depth + 1))
        elif isinstance(current, (list, tuple)):
            for nested in current[:128]:
                if isinstance(nested, (Mapping, list, tuple)):
                    pending.append((nested, depth + 1))
    return tuple(sorted(refs))[:16]

def _resolved_conformance_contract_fingerprints(
    validation_reports: Iterable[Mapping[str, object]],
) -> tuple[str, ...]:
    """Return typed repair frontiers closed by a later conformance success."""

    resolved: set[str] = set()
    for report in validation_reports:
        conformance = report.get("conformance")
        attempts = (
            conformance.get("attempts") if isinstance(conformance, Mapping) else None
        )
        if not isinstance(attempts, (list, tuple)):
            continue
        for attempt in attempts:
            if not isinstance(attempt, Mapping) or attempt.get("passed") is not True:
                continue
            details = attempt.get("details")
            if not isinstance(details, Mapping):
                continue
            resolved.update(_repair_contract_fingerprints(details))
    return tuple(sorted(resolved))
