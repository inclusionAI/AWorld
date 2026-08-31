"""Control, gate, and report helpers for candidate population screening."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from aworld.self_evolve.candidate_package import classify_candidate_mutation
from aworld.self_evolve.datasets import EvalCase, SelfEvolveDataset
from aworld.self_evolve.failure_events import (
    FailureOwner,
    FailureScope,
    FailureStage,
    ReplayExecutionStatus,
    ReplayFailureEvent,
)
from aworld.self_evolve.replay import (
    AWorldCliCandidateReplayBackend,
    AWorldCliReplayExecutor,
    CandidateReplayBackend,
    CandidateReplayResult,
    NormalizedReplayMembers,
    _is_replayable_user_task_case,
    normalize_replay_members,
    replay_support_fingerprint,
    replay_timeout_envelope_fingerprint,
)
from aworld.self_evolve.replay_adaptation import (
    ReplayAdaptationBundle,
    ReplayCapabilityRequirement,
    replay_adaptation_semantic_fingerprint,
)
from aworld.self_evolve.replay_capability import (
    replay_capability_semantic_fingerprint,
)
from aworld.self_evolve.sanitization import public_diagnostic_projection
from aworld.self_evolve.types import CandidateVariant, GateResult


SCREENING_BUDGET_CENSORED_CODE = "screening_budget_censored"
_SCREENING_BUDGET_CENSORED_CODE = SCREENING_BUDGET_CENSORED_CODE
_DEFAULT_CANDIDATE_SCREENING_TIMEOUT_SECONDS = 90
_MAX_CANDIDATE_SCREENING_TIMEOUT_SECONDS = 300
_DEFAULT_CANDIDATE_SCREENING_TRACE_HORIZON = 4
_DEFAULT_CANDIDATE_SCREENING_TOOL_CALL_LIMIT = 8
_SCREENING_STEP_TIMEOUT_SECONDS = 30


def _non_negative_int(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return max(0, value)
    return 0


def _stable_json_fingerprint(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _screening_attempt_is_budget_censored(
    attempt: Mapping[str, object],
) -> bool:
    details = attempt.get("details")
    return bool(
        isinstance(details, Mapping)
        and details.get("code") == SCREENING_BUDGET_CENSORED_CODE
        and details.get("screening_outcome") == "right_censored"
    )


def _candidate_requires_task_plane_intervention(
    candidate: CandidateVariant,
) -> bool:
    mutation = classify_candidate_mutation(
        candidate,
        current_content=(
            Path(candidate.target.path).read_text(encoding="utf-8")
            if candidate.target.path
            and Path(candidate.target.path).is_file()
            else candidate.content
        ),
    )
    changed_paths = mutation.support_file_paths
    return bool(changed_paths) and all(
        path == "replay/capability.json" or path.startswith("replay/")
        for path in changed_paths
    )


def _candidate_changes_target_behavior(candidate: CandidateVariant) -> bool:
    current_content = (
        Path(candidate.target.path).read_text(encoding="utf-8")
        if candidate.target.path and Path(candidate.target.path).is_file()
        else candidate.content
    )
    return classify_candidate_mutation(
        candidate,
        current_content=current_content,
    ).target_behavior_changed


def _replay_backend_provides_skill_activation_attestation(
    backend: CandidateReplayBackend | None,
) -> bool:
    """Return true only for the production CLI skill execution boundary."""

    return bool(
        isinstance(backend, AWorldCliCandidateReplayBackend)
        and isinstance(getattr(backend, "executor", None), AWorldCliReplayExecutor)
    )


def _candidate_task_plane_intervention_case_ids(
    capability_requirements: tuple[ReplayCapabilityRequirement, ...],
) -> tuple[str, ...]:
    """Return cases that can exercise a candidate-owned replay service."""

    return tuple(
        dict.fromkeys(
            case_id
            for requirement in capability_requirements
            if requirement.kind != "conversation_context"
            for case_id in requirement.case_ids
        )
    )


def _candidate_task_plane_intervention_observed(
    normalized: NormalizedReplayMembers,
    *,
    require_service_intervention: bool = True,
    require_skill_activation: bool = False,
    expected_skill_package_fingerprint: str | None = None,
) -> bool:
    return bool(
        _candidate_task_plane_intervention_observation(
            normalized,
            require_service_intervention=require_service_intervention,
            require_skill_activation=require_skill_activation,
            expected_skill_package_fingerprint=(
                expected_skill_package_fingerprint
            ),
        )["observed"]
    )


def _candidate_task_plane_intervention_observation(
    normalized: NormalizedReplayMembers,
    *,
    require_service_intervention: bool = True,
    require_skill_activation: bool = False,
    expected_skill_package_fingerprint: str | None = None,
) -> dict[str, bool]:
    service_observed = not require_service_intervention
    activation_observed = not require_skill_activation
    for member in normalized.members:
        variant = member.candidate
        results = variant.repetition_results or (variant,)
        for result in results:
            count = result.metrics.get("replay_service_protocol_trace_count")
            if isinstance(count, (int, float)) and not isinstance(count, bool):
                if count > 0:
                    service_observed = True
            if (
                result.metrics.get("skill_activation_attested") is True
                and isinstance(expected_skill_package_fingerprint, str)
                and result.metrics.get("activated_skill_package_fingerprint")
                == expected_skill_package_fingerprint
            ):
                activation_observed = True
            failure = result.failure
            diagnostics = (
                failure.diagnostics
                if isinstance(failure, ReplayFailureEvent)
                else failure.get("diagnostics")
                if isinstance(failure, Mapping)
                else None
            )
            traces = (
                diagnostics.get("replay_service_protocol_traces")
                if isinstance(diagnostics, Mapping)
                else None
            )
            if isinstance(traces, list) and traces:
                service_observed = True
    return {
        "observed": service_observed and activation_observed,
        "service_observed": service_observed,
        "skill_activation_observed": activation_observed,
    }


def _candidate_replay_has_repairable_capability_failure(
    replay_result: CandidateReplayResult,
) -> bool:
    failures: list[Mapping[str, Any] | None] = [
        replay_result.baseline.failure,
        replay_result.candidate.failure,
    ]
    for member in replay_result.member_results or ():
        failures.extend((member.baseline.failure, member.candidate.failure))
    return any(_repairable_capability_failure(failure) for failure in failures)


def _repairable_capability_failure(failure: Mapping[str, Any] | None) -> bool:
    if isinstance(failure, ReplayFailureEvent):
        return failure.owner is FailureOwner.CANDIDATE and failure.repairable
    if not isinstance(failure, Mapping):
        return False
    if failure.get("outcome") == "candidate_failure":
        return True
    if (
        failure.get("failure_class") == "candidate_replay_capability"
        and failure.get("repairable") is True
    ):
        return True
    for key in ("failures", "repetition_failures"):
        nested = failure.get(key)
        if isinstance(nested, list) and any(
            _repairable_capability_failure(item)
            for item in nested
            if isinstance(item, Mapping)
        ):
            return True
    return False


def _screening_attempt_requires_candidate_repair(
    attempt: Mapping[str, object],
) -> bool:
    details = attempt.get("details")
    return bool(
        isinstance(details, Mapping)
        and details.get("failure_class") == "candidate"
        and details.get("repairable") is True
    )


def _screening_attempt_is_candidate_failure(
    attempt: Mapping[str, object],
) -> bool:
    details = attempt.get("details")
    return bool(
        isinstance(details, Mapping)
        and details.get("failure_class") == "candidate"
    )


def _screening_attempt_requires_artifact_lifecycle_proof(
    attempt: Mapping[str, object],
) -> bool:
    return attempt.get("artifact_lifecycle_proof_required") is True


def _screening_attempt_has_artifact_lifecycle_proof(
    attempt: Mapping[str, object],
) -> bool:
    details = attempt.get("details")
    conformance = (
        details.get("artifact_lifecycle_conformance")
        if isinstance(details, Mapping)
        else None
    )
    return bool(
        isinstance(conformance, Mapping)
        and conformance.get("passed") is True
    )


def _candidate_artifact_lifecycle_observations(
    replay_result: CandidateReplayResult,
    *,
    dataset: SelfEvolveDataset,
) -> tuple[dict[str, object], ...]:
    normalized = normalize_replay_members(
        dataset=dataset,
        replay_result=replay_result,
    )
    observations: list[dict[str, object]] = []
    for member in normalized.members:
        repetitions = (
            member.candidate.repetition_results or (member.candidate,)
        )
        for repetition_index, repetition in enumerate(repetitions, start=1):
            if not repetition.executed:
                continue
            metrics = repetition.metrics
            observations.append(
                {
                    "case_id": member.case_id,
                    "repetition_index": repetition_index,
                    "execution_succeeded": (
                        repetition.status is ReplayExecutionStatus.SUCCEEDED
                    ),
                    "policy_active": metrics.get(
                        "evidence_runtime_policy_active"
                    ),
                    "policy_passed": metrics.get(
                        "evidence_runtime_policy_authoritative_passed",
                        metrics.get("evidence_runtime_policy_passed"),
                    ),
                    "artifact_file_count": metrics.get(
                        "evidence_runtime_policy_artifact_file_count"
                    ),
                    "artifact_bytes": metrics.get(
                        "evidence_runtime_policy_artifact_bytes"
                    ),
                    "tool_call_attempt_count": metrics.get(
                        "evidence_runtime_policy_tool_call_attempt_count"
                    ),
                    "manifest_entry_count": metrics.get(
                        "evidence_manifest_entry_count"
                    ),
                    "manifest_valid": metrics.get("evidence_manifest_valid"),
                    "policy_phase": metrics.get(
                        "evidence_runtime_policy_phase"
                    ),
                }
            )
    return tuple(observations)


def _screening_gate_has_invalid_control(
    gate: GateResult | None,
) -> bool:
    if gate is None or gate.passed:
        return False
    if (
        isinstance(gate.details, Mapping)
        and gate.details.get("code")
        == "screening_support_control_circuit_open"
    ):
        return True
    if (
        isinstance(gate.details, Mapping)
        and gate.details.get("code")
        == "candidate_replay_support_baseline_incompatible"
        and gate.details.get("failure_owner") == FailureOwner.CANDIDATE.value
    ):
        return False
    if (
        isinstance(gate.details, Mapping)
        and gate.details.get("code") == _SCREENING_BUDGET_CENSORED_CODE
        and gate.details.get("screening_outcome") == "right_censored"
    ):
        # A paired horizon is an inconclusive ranking experiment.  A baseline
        # horizon blocks candidate execution entirely, so it is an invalid
        # control and must consume a distinct fallback case rather than
        # promote an unobserved candidate.
        return gate.details.get("screening_censor_basis") == "baseline_horizon"
    details = gate.details if isinstance(gate.details, Mapping) else {}
    if (
        details.get("failure_class") == "candidate"
        and details.get("failure_owner", "candidate") == "candidate"
        and details.get("evaluator_skipped") is True
        and details.get("baseline_status")
        == ReplayExecutionStatus.SUCCEEDED.value
    ):
        return False
    baseline_failures: list[object] = [
        details.get("baseline_failure"),
        details.get("baseline_failure_event"),
    ]
    failed_members = details.get("failed_members")
    if isinstance(failed_members, (list, tuple)):
        for member in failed_members:
            if not isinstance(member, Mapping):
                continue
            baseline_failures.extend(
                (
                    member.get("baseline_failure"),
                    member.get("baseline_failure_event"),
                )
            )
    if any(_framework_phase_timeout(failure) for failure in baseline_failures):
        return True
    stack: list[object] = [gate.details]
    inspected = 0
    while stack and inspected < 256:
        current = stack.pop()
        inspected += 1
        if isinstance(current, Mapping):
            code = current.get("code")
            owner = current.get("failure_owner") or current.get("owner")
            if code in {
                "control_not_comparable",
                "authoritative_replay_invalid_control",
                "trusted_measurement_invalid_control_frontier",
            } and owner in {None, FailureOwner.FRAMEWORK.value}:
                return True
            # Candidate-phase timeouts are directional candidate evidence, not
            # control invalidity. Only explicit control codes above or typed
            # baseline failures may quarantine a control case.
            stack.extend(
                value
                for key, value in current.items()
                if str(key)
                not in {
                    "candidate_failure",
                    "candidate_failure_event",
                }
            )
        elif isinstance(current, (list, tuple)):
            stack.extend(current)
    return False


def _screening_invalid_control_is_timeout(gate: GateResult | None) -> bool:
    if not _screening_gate_has_invalid_control(gate) or gate is None:
        return False
    details = gate.details if isinstance(gate.details, Mapping) else {}
    if details.get("code") == "screening_support_control_circuit_open":
        return True
    if (
        details.get("code") == _SCREENING_BUDGET_CENSORED_CODE
        and details.get("screening_censor_basis") == "baseline_horizon"
    ):
        return True
    failures: list[object] = [
        details.get("baseline_failure"),
        details.get("baseline_failure_event"),
    ]
    failed_members = details.get("failed_members")
    if isinstance(failed_members, (list, tuple)):
        for member in failed_members:
            if isinstance(member, Mapping):
                failures.extend(
                    (
                        member.get("baseline_failure"),
                        member.get("baseline_failure_event"),
                    )
                )
    return any(_framework_phase_timeout(item) for item in failures)


def _screening_gate_has_baseline_execution_failure(
    gate: GateResult | None,
) -> bool:
    if gate is None or gate.passed or not isinstance(gate.details, Mapping):
        return False
    details = gate.details
    if details.get("baseline_status") == ReplayExecutionStatus.FAILED.value:
        return True
    if isinstance(details.get("baseline_failure"), Mapping):
        return True
    failed_members = details.get("failed_members")
    return bool(
        isinstance(failed_members, (list, tuple))
        and any(
            isinstance(member, Mapping)
            and (
                member.get("baseline_status")
                == ReplayExecutionStatus.FAILED.value
                or isinstance(member.get("baseline_failure"), Mapping)
            )
            for member in failed_members
        )
    )


def _screening_baseline_failure_case_ids(
    gate: GateResult | None,
    *,
    fallback_case_ids: tuple[str, ...],
) -> tuple[str, ...]:
    if not _screening_gate_has_baseline_execution_failure(gate):
        return ()
    assert gate is not None and isinstance(gate.details, Mapping)
    located: list[str] = []
    failed_members = gate.details.get("failed_members")
    if isinstance(failed_members, (list, tuple)):
        for member in failed_members:
            if not isinstance(member, Mapping):
                continue
            case_id = member.get("case_id")
            if (
                isinstance(case_id, str)
                and case_id in fallback_case_ids
                and (
                    member.get("baseline_status")
                    == ReplayExecutionStatus.FAILED.value
                    or isinstance(member.get("baseline_failure"), Mapping)
                )
            ):
                located.append(case_id)
    return tuple(dict.fromkeys(located or fallback_case_ids))


def _candidate_support_baseline_incompatibility_gate(
    gate: GateResult | None,
    *,
    control_identity: Mapping[str, object] | None,
    control_observations: Mapping[str, Mapping[str, object]],
) -> GateResult | None:
    """Attribute a baseline failure to candidate support only with a counterfactual."""

    if (
        gate is None
        or gate.passed
        or control_identity is None
        or not _screening_gate_has_baseline_execution_failure(gate)
        or control_identity.get("capability_package_fingerprint")
        == "framework-only"
    ):
        return gate
    if _screening_required_intervention_unobserved(gate):
        # A baseline-only framework failure cannot prove that the candidate's
        # required data-plane intervention caused the failure. Historical
        # success under a different support package is control-health evidence,
        # not a treatment observation, so preserve the framework owner.
        return gate
    case_id = control_identity.get("case_id")
    support_fingerprint = control_identity.get("support_fingerprint")
    counterfactual_control_fields = (
        "case_id",
        "baseline_skill_fingerprint",
        "timeout_envelope_fingerprint",
        "timeout_seconds",
        "max_steps",
        "max_tool_calls",
    )
    counterfactuals: list[dict[str, object]] = []
    prior_current_support_failure_count = 0
    for observation in control_observations.values():
        identity = observation.get("identity")
        if not isinstance(identity, Mapping):
            continue
        same_control_envelope = not any(
            identity.get(field_name) != control_identity.get(field_name)
            for field_name in counterfactual_control_fields
        )
        if (
            same_control_envelope
            and identity.get("support_fingerprint") == support_fingerprint
        ):
            prior_current_support_failure_count += max(
                0,
                _non_negative_int(observation.get("baseline_attempt_count"))
                - _non_negative_int(observation.get("baseline_success_count")),
            )
            continue
        if (
            not same_control_envelope
            or _non_negative_int(
                observation.get("baseline_success_count")
            )
            <= 0
        ):
            continue
        counterfactuals.append(
            {
                "support_fingerprint": identity.get("support_fingerprint"),
                "capability_package_fingerprint": identity.get(
                    "capability_package_fingerprint"
                ),
                "replay_capability_fingerprint": identity.get(
                    "replay_capability_fingerprint"
                ),
                "timeout_envelope_fingerprint": identity.get(
                    "timeout_envelope_fingerprint"
                ),
                "timeout_seconds": identity.get("timeout_seconds"),
                "max_steps": identity.get("max_steps"),
                "max_tool_calls": identity.get("max_tool_calls"),
            }
        )
    if not counterfactuals or prior_current_support_failure_count <= 0:
        return gate
    event = ReplayFailureEvent(
        code="candidate_replay_support_baseline_incompatible",
        owner=FailureOwner.CANDIDATE,
        stage=FailureStage.ADAPTATION,
        scope=FailureScope.CANDIDATE,
        repairable=True,
        category="candidate_replay_support",
        summary=(
            "candidate-owned replay support breaks a baseline that succeeds "
            "under another qualified support surface"
        ),
        diagnostics={
            "case_id": case_id,
            "candidate_support_fingerprint": support_fingerprint,
            "current_support_baseline_failure_count": (
                prior_current_support_failure_count + 1
            ),
            "qualified_counterfactual_supports": counterfactuals[:8],
        },
    )
    payload = event.to_dict()
    details = dict(gate.details or {})
    return GateResult(
        gate_name=gate.gate_name,
        passed=False,
        reason=(
            "baseline is incompatible only with candidate-owned replay support; "
            "repair the candidate support package"
        ),
        details={
            **details,
            "code": event.code,
            "failure_class": "candidate",
            "failure_owner": event.owner.value,
            "failure_scope": event.scope.value,
            "failure_stage": event.stage.value,
            "repairable": True,
            "next_action": "continue_candidate_repair",
            "candidate_support_fingerprint": support_fingerprint,
            "current_support_baseline_failure_count": (
                prior_current_support_failure_count + 1
            ),
            "control_identity": dict(control_identity),
            "qualified_counterfactual_supports": counterfactuals[:8],
            "failure_event": payload,
            "causal_failure_events": [payload],
        },
    )


def _screening_required_intervention_unobserved(
    gate: GateResult | None,
) -> bool:
    if gate is None or not isinstance(gate.details, Mapping):
        return False
    return bool(
        gate.details.get("candidate_intervention_required") is True
        and gate.details.get("candidate_intervention_observed") is not True
    )


def _screening_control_infeasible_before_candidate_observation(
    gate: GateResult | None,
    *,
    control_case_attempts: tuple[Mapping[str, object], ...]
    | list[Mapping[str, object]],
) -> bool:
    """Admit authoritative fallback only for baseline-only control failures."""

    if (
        gate is None
        or gate.passed
        or not isinstance(gate.details, Mapping)
        or gate.details.get("code") != "screening_control_infeasible"
        or gate.details.get("failure_owner") != FailureOwner.FRAMEWORK.value
        or not _screening_required_intervention_unobserved(gate)
    ):
        return False
    if not control_case_attempts:
        prequalified = gate.details.get(
            "prequalified_invalid_control_case_ids"
        )
        return bool(
            isinstance(prequalified, (list, tuple))
            and prequalified
            and all(isinstance(case_id, str) for case_id in prequalified)
        )
    for attempt in control_case_attempts:
        if (
            attempt.get("invalid_control") is not True
            or attempt.get("candidate_status") not in {"blocked", "not_run"}
            or not _framework_phase_timeout(attempt.get("baseline_failure"))
        ):
            return False
    return True


def _framework_phase_timeout(value: object) -> bool:
    return bool(
        isinstance(value, Mapping)
        and value.get("code") == "replay_member_phase_timeout"
        and (
            (value.get("failure_owner") or value.get("owner"))
            == FailureOwner.FRAMEWORK.value
            or value.get("outcome") == "framework_failure"
        )
    )


def _screening_invalid_control_case_ids(
    gate: GateResult | None,
    *,
    fallback_case_ids: tuple[str, ...],
) -> tuple[str, ...]:
    """Locate invalid members without quarantining healthy panel siblings."""

    if not _screening_gate_has_invalid_control(gate) or gate is None:
        return ()
    located: list[str] = []
    stack: list[object] = [gate.details]
    inspected = 0
    while stack and inspected < 512:
        current = stack.pop()
        inspected += 1
        if isinstance(current, Mapping):
            raw_case_ids = current.get("affected_case_ids")
            if isinstance(raw_case_ids, (list, tuple)):
                located.extend(
                    case_id
                    for case_id in raw_case_ids
                    if isinstance(case_id, str)
                    and case_id in fallback_case_ids
                    and case_id not in located
                )
            case_id = current.get("case_id")
            if (
                isinstance(case_id, str)
                and case_id in fallback_case_ids
                and case_id not in located
            ):
                located.append(case_id)
            stack.extend(current.values())
        elif isinstance(current, (list, tuple)):
            stack.extend(current)
    return tuple(located or fallback_case_ids)


def _deduplicate_conformance_phenotypes(
    candidates: tuple[CandidateVariant, ...],
    *,
    conformance_report: Mapping[str, object] | None,
    current_content: str,
) -> tuple[
    tuple[CandidateVariant, ...],
    dict[str, str],
    dict[str, str],
]:
    """Keep one representative for each deterministically observed phenotype.

    Source bytes remain part of generation-level semantic deduplication.  After a
    repair candidate passes the same frozen probe contract, task screening should
    compare observable behavior rather than repeatedly paying for source variants
    with the same target surface and conformance outcome.
    """

    attempts = (
        conformance_report.get("attempts")
        if isinstance(conformance_report, Mapping)
        else None
    )
    if not isinstance(attempts, list):
        return candidates, {}, {}
    passed_by_candidate = {
        str(attempt.get("candidate_id")): attempt
        for attempt in attempts
        if isinstance(attempt, Mapping)
        and attempt.get("passed") is True
        and isinstance(attempt.get("candidate_id"), str)
    }
    representatives: list[CandidateVariant] = []
    representative_by_fingerprint: dict[str, str] = {}
    duplicate_of: dict[str, str] = {}
    fingerprints: dict[str, str] = {}
    for candidate in candidates:
        attempt = passed_by_candidate.get(candidate.candidate_id)
        if attempt is None:
            representatives.append(candidate)
            continue
        details = attempt.get("details")
        if not isinstance(details, Mapping):
            representatives.append(candidate)
            continue
        probe_results = details.get("probe_group_results")
        normalized_probes = []
        if isinstance(probe_results, list):
            normalized_probes = sorted(
                (
                    {
                        "passed": item.get("passed") is True,
                        "code": str(item.get("code") or ""),
                        "requirement_id": str(
                            item.get("requirement_id") or ""
                        ),
                        "case_ids": sorted(
                            str(case_id)
                            for case_id in item.get("case_ids", [])
                            if isinstance(case_id, str)
                        )
                        if isinstance(item.get("case_ids"), list)
                        else [],
                    }
                    for item in probe_results
                    if isinstance(item, Mapping)
                ),
                key=lambda item: json.dumps(
                    item,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
        mutation = classify_candidate_mutation(
            candidate,
            current_content=current_content,
        )
        fingerprint = _stable_json_fingerprint(
            {
                "schema_version": "aworld.self_evolve.conformance_phenotype.v1",
                "mutation_kind": mutation.kind.value,
                "target_behavior_fingerprint": (
                    mutation.candidate_target_behavior_fingerprint
                ),
                "support_file_paths": list(mutation.support_file_paths),
                "gate_name": str(attempt.get("gate_name") or ""),
                "gate_code": str(details.get("code") or ""),
                "probe_results": normalized_probes,
            }
        )
        fingerprints[candidate.candidate_id] = fingerprint
        representative_id = representative_by_fingerprint.get(fingerprint)
        if representative_id is not None:
            duplicate_of[candidate.candidate_id] = representative_id
            continue
        representative_by_fingerprint[fingerprint] = candidate.candidate_id
        representatives.append(candidate)
    return tuple(representatives), duplicate_of, fingerprints


def _combined_candidate_validation_report(
    *,
    candidates: tuple[CandidateVariant, ...],
    conformance: Mapping[str, object] | None,
    screening: Mapping[str, object] | None,
) -> dict[str, object] | None:
    if conformance is None and screening is None:
        return None
    conformance_attempts = (
        list(conformance.get("attempts", []))
        if isinstance(conformance, Mapping)
        and isinstance(conformance.get("attempts"), list)
        else []
    )
    failed_conformance_attempts = [
        attempt
        for attempt in conformance_attempts
        if isinstance(attempt, Mapping) and attempt.get("passed") is False
    ]
    screening_attempts = (
        list(screening.get("attempts", []))
        if isinstance(screening, Mapping)
        and isinstance(screening.get("attempts"), list)
        else []
    )
    selected_candidate_ids = (
        list(screening.get("selected_candidate_ids", []))
        if isinstance(screening, Mapping)
        and isinstance(screening.get("selected_candidate_ids"), list)
        else (
            list(conformance.get("passed_candidate_ids", []))
            if isinstance(conformance, Mapping)
            and isinstance(conformance.get("passed_candidate_ids"), list)
            else [candidate.candidate_id for candidate in candidates]
        )
    )
    report: dict[str, object] = {
        "generated_candidate_count": len(candidates),
        "attempted_candidate_count": len(failed_conformance_attempts)
        + len(screening_attempts),
        "selected_candidate_id": (
            screening.get("selected_candidate_id")
            if isinstance(screening, Mapping)
            else None
        ),
        "selected_candidate_ids": selected_candidate_ids,
        "selection_reason": (
            screening.get("selection_reason")
            if isinstance(screening, Mapping)
            else "repair conformance completed before optional task screening"
        ),
        "attempts": [*failed_conformance_attempts, *screening_attempts],
        "conformance": dict(conformance) if conformance is not None else None,
        "screening": dict(screening) if screening is not None else None,
    }
    if isinstance(screening, Mapping):
        for key in (
            "representative_case_id",
            "representative_case_ids",
            "baseline_repetitions",
            "candidate_repetitions",
            "max_steps",
            "max_tool_calls",
            "progressive_repetition",
            "authoritative_baseline_repetitions",
            "authoritative_candidate_repetitions",
            "ranked_below_screening_candidate_ids",
            "candidate_dispositions",
            "stopped_after_budget_censor",
            "screening_outcome",
        ):
            if key in screening:
                report[key] = screening[key]
    return report


def _candidate_validation_report_for_persistence(
    value: object,
) -> object:
    """Use the shared recursive type-aware projection for persisted reports."""

    return public_diagnostic_projection(value)


def _candidate_screening_rank(
    replay_result: CandidateReplayResult | None,
) -> tuple[int, ...]:
    if replay_result is None:
        return (0, 0, 0, 0, 0, 0, 0, 0)
    candidate = replay_result.candidate
    metrics = candidate.metrics or {}

    def tri_state(value: object) -> int:
        if value is True:
            return 1
        if value is False:
            return -1
        return 0

    def count(value: object) -> int:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value)
        return 0

    return (
        int(candidate.succeeded),
        count(metrics.get("successful_repetition_count")),
        tri_state(metrics.get("evidence_strategy_passed")),
        tri_state(metrics.get("evidence_bundle_valid")),
        tri_state(metrics.get("evidence_manifest_valid")),
        -count(metrics.get("evidence_manifest_invalid_entry_count")),
        -count(metrics.get("evidence_unmanifested_artifact_reference_count")),
        -count(metrics.get("failed_repetition_count")),
    )


def _candidate_screening_rank_details(rank: tuple[int, ...]) -> dict[str, int]:
    labels = (
        "succeeded",
        "successful_repetition_count",
        "evidence_strategy_passed",
        "evidence_bundle_valid",
        "evidence_manifest_valid",
        "negative_invalid_manifest_entry_count",
        "negative_unmanifested_artifact_reference_count",
        "negative_failed_repetition_count",
    )
    return dict(zip(labels, rank, strict=True))


def _control_qualification_identity(
    *,
    case_id: str,
    baseline_skill_fingerprint: str,
    replay_adaptation: ReplayAdaptationBundle,
    timeout_seconds: float,
    max_steps: int | None,
    max_tool_calls: int | None,
    replay_capability_fingerprint: Callable[[object], str] = (
        replay_capability_semantic_fingerprint
    ),
    replay_adaptation_fingerprint: Callable[[object], str] = (
        replay_adaptation_semantic_fingerprint
    ),
    support_fingerprint: Callable[[object], str | None] = (
        replay_support_fingerprint
    ),
) -> dict[str, object]:
    """Freeze the exact support and envelope used to qualify one control."""

    capability = replay_adaptation.replay_capability
    capability_package_fingerprint = (
        capability.capability_package_fingerprint
        if capability is not None
        else "framework-only"
    )
    resolved_replay_capability_fingerprint = (
        replay_capability_fingerprint(capability)
        if capability is not None
        else "framework-only"
    )
    resolved_support_fingerprint = support_fingerprint(replay_adaptation)
    assert resolved_support_fingerprint is not None
    timeout_fingerprint = replay_timeout_envelope_fingerprint(
        timeout_seconds=timeout_seconds,
        max_steps=max_steps,
        max_tool_calls=max_tool_calls,
    )
    identity: dict[str, object] = {
        "schema_version": "aworld.self_evolve.control_qualification_identity.v1",
        "case_id": case_id,
        "baseline_skill_fingerprint": baseline_skill_fingerprint,
        "capability_package_fingerprint": capability_package_fingerprint,
        "replay_capability_fingerprint": (
            resolved_replay_capability_fingerprint
        ),
        "adaptation_fingerprint": replay_adaptation_fingerprint(
            replay_adaptation
        ),
        "support_fingerprint": resolved_support_fingerprint,
        "timeout_envelope_fingerprint": timeout_fingerprint,
        "timeout_seconds": float(timeout_seconds),
        "max_steps": max_steps,
        "max_tool_calls": max_tool_calls,
    }
    encoded = json.dumps(
        identity,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    identity["control_identity_fingerprint"] = (
        "sha256:" + hashlib.sha256(encoded).hexdigest()
    )
    return identity


def _record_support_specific_control_observation(
    observations: dict[str, dict[str, object]],
    *,
    identity: Mapping[str, object],
    attempt: Mapping[str, object],
) -> None:
    fingerprint = identity.get("control_identity_fingerprint")
    required_fields = (
        "case_id",
        "baseline_skill_fingerprint",
        "capability_package_fingerprint",
        "replay_capability_fingerprint",
        "adaptation_fingerprint",
        "support_fingerprint",
        "timeout_envelope_fingerprint",
    )
    if (
        not isinstance(fingerprint, str)
        or not fingerprint
        or any(not isinstance(identity.get(key), str) for key in required_fields)
    ):
        return
    current = observations.setdefault(
        fingerprint,
        {
            "identity": dict(identity),
            "attempt_count": 0,
            "baseline_attempt_count": 0,
            "baseline_success_count": 0,
            "baseline_timeout_count": 0,
            "passed_count": 0,
            "total_wall_seconds": 0.0,
        },
    )
    if current.get("identity") != dict(identity):
        return
    details = attempt.get("details")
    baseline_status = (
        str(details.get("baseline_status") or "")
        if isinstance(details, Mapping)
        else ""
    )
    baseline_failure = (
        details.get("baseline_failure")
        if isinstance(details, Mapping)
        else None
    )
    current["attempt_count"] = _non_negative_int(
        current.get("attempt_count")
    ) + 1
    current["passed_count"] = _non_negative_int(
        current.get("passed_count")
    ) + int(attempt.get("passed") is True)
    current["total_wall_seconds"] = _non_negative_screening_float(
        current.get("total_wall_seconds")
    ) + _non_negative_screening_float(attempt.get("wall_seconds"))
    if baseline_status and baseline_status not in {"blocked", "not_run"}:
        current["baseline_attempt_count"] = _non_negative_int(
            current.get("baseline_attempt_count")
        ) + 1
        current["baseline_success_count"] = _non_negative_int(
            current.get("baseline_success_count")
        ) + int(baseline_status == ReplayExecutionStatus.SUCCEEDED.value)
        current["baseline_timeout_count"] = _non_negative_int(
            current.get("baseline_timeout_count")
        ) + int(_framework_phase_timeout(baseline_failure))


def _candidate_screening_dataset(
    dataset: SelfEvolveDataset,
    *,
    capability_requirements: tuple[ReplayCapabilityRequirement, ...] = (),
    max_cases: int = 1,
    required_case_ids: tuple[str, ...] = (),
    allow_held_out_control_rescue: bool = False,
    empirical_observations: Mapping[
        str, Mapping[str, float | int]
    ] | None = None,
) -> SelfEvolveDataset | None:
    if max_cases <= 0:
        raise ValueError("candidate screening max_cases must be positive")
    replayable_cases = tuple(
        case for case in dataset.cases if _is_replayable_user_task_case(case)
    )
    if not replayable_cases:
        return None

    replayable_by_id = {case.case_id: case for case in replayable_cases}
    held_out_case_ids = set(dataset.recipe.held_out_case_ids)
    held_out_case_ids.update(dataset.recipe.splits.get("held_out", ()))
    preferred_case_ids = (
        *dataset.recipe.trainable_case_ids,
        *dataset.recipe.splits.get("train", ()),
        *dataset.recipe.splits.get("validation", ()),
        *(case.case_id for case in replayable_cases),
    )
    ordered_candidates: list[EvalCase] = []
    seen_case_ids: set[str] = set()
    for case_id in preferred_case_ids:
        if (
            case_id in replayable_by_id
            and case_id not in held_out_case_ids
            and case_id not in seen_case_ids
        ):
            ordered_candidates.append(replayable_by_id[case_id])
            seen_case_ids.add(case_id)
    if not ordered_candidates:
        ordered_candidates = list(replayable_cases)
    required_case_id_set = set(required_case_ids)
    requirement_ids_by_case: dict[str, set[str]] = {}
    context_requirement_count_by_case: dict[str, int] = {}
    for requirement in capability_requirements:
        for case_id in requirement.case_ids:
            if requirement.kind == "conversation_context":
                context_requirement_count_by_case[case_id] = (
                    context_requirement_count_by_case.get(case_id, 0) + 1
                )
                continue
            requirement_ids_by_case.setdefault(case_id, set()).add(
                requirement.requirement_id
            )
    held_out_control_rescue_case_ids: tuple[str, ...] = ()
    if allow_held_out_control_rescue and empirical_observations is not None:
        trainable_control_candidates = tuple(
            case
            for case in ordered_candidates
            if not required_case_id_set
            or case.case_id in required_case_id_set
        )
        has_trainable_feasible_control = any(
            _screening_case_has_feasible_baseline(
                empirical_observations.get(case.case_id, {})
            )
            for case in trainable_control_candidates
        )
        has_trainable_viable_control = any(
            not _screening_case_has_only_invalid_baselines(
                empirical_observations.get(case.case_id, {})
            )
            for case in trainable_control_candidates
        )
        held_out_controls = tuple(
            case
            for case in replayable_cases
            if case.case_id in held_out_case_ids
            and case.case_id not in seen_case_ids
            and (
                not required_case_id_set
                or case.case_id in required_case_id_set
            )
        )
        held_out_feasible_controls = tuple(
            case
            for case in held_out_controls
            if _screening_case_has_feasible_baseline(
                empirical_observations.get(case.case_id, {})
            )
        )
        rescue_controls = (
            held_out_controls
            if required_case_id_set and not has_trainable_viable_control
            else held_out_feasible_controls
            if not has_trainable_feasible_control
            else ()
        )
        if rescue_controls:
            # Screening a sole conforming candidate is a support qualification
            # gate, not population ranking. In that narrow mode a held-out
            # control may rescue either a historically healthy task or the
            # only remaining task capable of exercising candidate replay code.
            rescue_control = max(
                rescue_controls,
                key=lambda case: (
                    _screening_case_has_feasible_baseline(
                        empirical_observations.get(case.case_id, {})
                    ),
                    not _screening_case_has_only_invalid_baselines(
                        empirical_observations.get(case.case_id, {})
                    ),
                    _non_negative_int(
                        empirical_observations.get(case.case_id, {}).get(
                            "baseline_success_count"
                        )
                    ),
                    _non_negative_int(
                        empirical_observations.get(case.case_id, {}).get(
                            "passed_count"
                        )
                    ),
                    -_candidate_screening_case_cost(
                        case,
                        context_requirement_count=(
                            context_requirement_count_by_case.get(
                                case.case_id, 0
                            )
                        ),
                        empirical_observation=empirical_observations.get(
                            case.case_id
                        ),
                    ),
                ),
            )
            ordered_candidates.insert(0, rescue_control)
            held_out_control_rescue_case_ids = (rescue_control.case_id,)
    intervention_exposure_case_ids = tuple(
        case.case_id
        for case in ordered_candidates
        if case.case_id in required_case_id_set
    )
    if required_case_id_set and intervention_exposure_case_ids:
        ordered_candidates = [
            case
            for case in ordered_candidates
            if case.case_id in required_case_id_set
        ]
    known_feasible_control_case_ids = tuple(
        case.case_id
        for case in ordered_candidates
        if _screening_case_has_feasible_baseline(
            (
                empirical_observations.get(case.case_id, {})
                if empirical_observations is not None
                else {}
            )
        )
    )
    quarantined_control_case_ids = tuple(
        case.case_id
        for case in ordered_candidates
        if _screening_case_has_only_invalid_baselines(
            (
                empirical_observations.get(case.case_id, {})
                if empirical_observations is not None
                else {}
            )
        )
    )
    # Target-only history may order the panel but cannot remove a case: the
    # candidate support surface has not been compiled or qualified yet.
    ordered_candidates.sort(
        key=lambda case: case.case_id in quarantined_control_case_ids
    )
    case_index = {
        case.case_id: index for index, case in enumerate(ordered_candidates)
    }
    selected: list[EvalCase] = []
    covered_requirements: set[str] = set()
    covered_strata: set[str] = set()
    while ordered_candidates and len(selected) < min(
        max_cases,
        len(ordered_candidates),
    ):
        remaining = [case for case in ordered_candidates if case not in selected]
        # A target-only success is the best available evidence that screening
        # can produce a baseline/candidate pair on the current task surface.
        # Anchor the panel with such a control before optimizing capability
        # coverage; otherwise requirement-rich, persistently timing-out cases
        # can consume every bounded qualification slot.
        if not selected and known_feasible_control_case_ids:
            remaining = [
                case
                for case in remaining
                if case.case_id in known_feasible_control_case_ids
            ]
        representative = max(
            remaining,
            key=lambda case: (
                case.case_id not in quarantined_control_case_ids,
                len(
                    requirement_ids_by_case.get(case.case_id, set())
                    - covered_requirements
                ),
                case.case_id in known_feasible_control_case_ids,
                _non_negative_int(
                    (
                        empirical_observations.get(case.case_id, {})
                        if empirical_observations is not None
                        else {}
                    ).get("authoritative_failure_count")
                ),
                -_candidate_screening_case_cost(
                    case,
                    context_requirement_count=(
                        context_requirement_count_by_case.get(case.case_id, 0)
                    ),
                    empirical_observation=(
                        empirical_observations.get(case.case_id)
                        if empirical_observations is not None
                        else None
                    ),
                ),
                len(dataset_case_strata(case) - covered_strata),
                _candidate_screening_case_distance(
                    case_index[case.case_id],
                    selected_indices=tuple(
                        case_index[item.case_id] for item in selected
                    ),
                    case_count=len(ordered_candidates),
                ),
                -case_index[case.case_id],
            ),
        )
        selected.append(representative)
        covered_requirements.update(
            requirement_ids_by_case.get(representative.case_id, set())
        )
        covered_strata.update(dataset_case_strata(representative))
    representative_ids = tuple(case.case_id for case in selected)
    return SelfEvolveDataset(
        cases=tuple(selected),
        recipe=replace(
            dataset.recipe,
            source={
                **dict(dataset.recipe.source),
                "candidate_screening": True,
                "screening_case_id": representative_ids[0],
                "screening_case_ids": list(representative_ids),
                "screening_case_count": len(representative_ids),
                "screening_case_costs": {
                    case.case_id: _candidate_screening_case_cost(
                        case,
                        context_requirement_count=(
                            context_requirement_count_by_case.get(case.case_id, 0)
                        ),
                        empirical_observation=(
                            empirical_observations.get(case.case_id)
                            if empirical_observations is not None
                            else None
                        ),
                    )
                    for case in selected
                },
                "screening_case_observations": {
                    case.case_id: dict(
                        empirical_observations.get(case.case_id, {})
                    )
                    for case in selected
                    if empirical_observations is not None
                },
                "known_feasible_control_case_ids": list(
                    known_feasible_control_case_ids
                ),
                "screening_anchor_case_id": (
                    representative_ids[0]
                    if representative_ids[0]
                    in known_feasible_control_case_ids
                    else None
                ),
                "held_out_control_rescue_case_ids": list(
                    held_out_control_rescue_case_ids
                ),
                "required_intervention_case_ids": list(required_case_ids),
                "intervention_exposure_case_ids": list(
                    intervention_exposure_case_ids
                ),
                "quarantined_control_case_ids": list(
                    quarantined_control_case_ids
                ),
                "control_case_retry_suppressed_count": len(
                    quarantined_control_case_ids
                ),
                "original_case_count": len(dataset.cases),
            },
            splits={
                "train": list(representative_ids),
                "validation": [],
                "held_out": [],
            },
            trainable_case_ids=representative_ids,
            held_out_case_ids=(),
        ),
    )


def _screening_case_has_feasible_baseline(
    observation: Mapping[str, float | int],
) -> bool:
    return bool(
        _non_negative_int(observation.get("baseline_success_count")) > 0
        or _non_negative_int(observation.get("passed_count")) > 0
    )


def _screening_case_has_only_invalid_baselines(
    observation: Mapping[str, float | int],
) -> bool:
    baseline_attempts = _non_negative_int(
        observation.get("baseline_attempt_count")
    )
    baseline_successes = _non_negative_int(
        observation.get("baseline_success_count")
    )
    invalid_controls = _non_negative_int(
        observation.get("invalid_control_count")
    )
    if baseline_attempts > 0:
        timeout_count = _non_negative_int(
            observation.get("baseline_timeout_count")
        )
        timeout_only_history = bool(
            baseline_attempts >= 2 and timeout_count >= baseline_attempts
        )
        reached_timeout_ceiling = bool(
            timeout_count > 0
            and _non_negative_screening_float(
                observation.get("baseline_timeout_max_seconds")
            )
            >= _MAX_CANDIDATE_SCREENING_TIMEOUT_SECONDS
        )
        mixed_control_regressed = bool(
            baseline_successes > 0
            and invalid_controls >= 2
            and timeout_count >= baseline_successes
        )
        return bool(
            (invalid_controls > 0 and baseline_successes == 0)
            or timeout_only_history
            or (reached_timeout_ceiling and invalid_controls >= 2)
            or mixed_control_regressed
        )
    # Compatibility for reports written before phase-aware control telemetry.
    return invalid_controls > 0


def _candidate_screening_dataset_for_case_ids(
    dataset: SelfEvolveDataset,
    *,
    case_ids: tuple[str, ...],
) -> SelfEvolveDataset:
    requested = tuple(dict.fromkeys(case_ids))
    selected_by_id = {case.case_id: case for case in dataset.cases}
    if not requested or any(case_id not in selected_by_id for case_id in requested):
        raise ValueError("screening fallback case ids must exist in the panel")
    selected = tuple(selected_by_id[case_id] for case_id in requested)
    return SelfEvolveDataset(
        cases=selected,
        recipe=replace(
            dataset.recipe,
            source={
                **dict(dataset.recipe.source),
                "candidate_screening": True,
                "screening_case_id": requested[0],
                "screening_case_ids": list(requested),
                "screening_case_count": len(requested),
                "screening_control_fallback": True,
            },
            splits={
                "train": list(requested),
                "validation": [],
                "held_out": [],
            },
            trainable_case_ids=requested,
            held_out_case_ids=(),
        ),
    )


def _candidate_screening_case_cost(
    case: EvalCase,
    *,
    context_requirement_count: int = 0,
    empirical_observation: Mapping[str, float | int] | None = None,
) -> int:
    """Estimate qualification cost from static shape and bounded observations.

    The static estimate is a cold-start prior.  Once a case has participated in
    screening, measured wall time and right-censor frequency become the stronger
    signal.  Capability coverage still precedes cost in panel selection, so an
    expensive case is retained when it is the only representative of a required
    behavior.
    """

    trace_depth = len(case.trace_pack.steps) if case.trace_pack is not None else 1
    try:
        input_bytes = len(
            json.dumps(
                case.input,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        )
    except (TypeError, ValueError):
        input_bytes = len(str(case.input).encode("utf-8"))
    input_kib = max(1, math.ceil(input_bytes / 1024))
    # Conversation reconstruction expands fixtures and prompt context but does
    # not directly exercise a candidate-owned data-plane requirement.  Penalize
    # it strongly enough that an equally representative executable case wins.
    static_cost = (
        max(0, context_requirement_count) * 100
        + trace_depth * 10
        + input_kib
    )
    if not empirical_observation:
        return static_cost
    attempt_count = _non_negative_int(
        empirical_observation.get("attempt_count")
    )
    total_wall_seconds = _non_negative_screening_float(
        empirical_observation.get("total_wall_seconds")
    )
    censored_count = _non_negative_int(
        empirical_observation.get("right_censored_count")
    )
    baseline_attempt_count = _non_negative_int(
        empirical_observation.get("baseline_attempt_count")
    )
    baseline_timeout_count = _non_negative_int(
        empirical_observation.get("baseline_timeout_count")
    )
    if attempt_count <= 0 and baseline_attempt_count > 0:
        attempt_count = baseline_attempt_count
        total_wall_seconds = _non_negative_screening_float(
            empirical_observation.get("baseline_total_wall_seconds")
        )
        censored_count = baseline_timeout_count
    if attempt_count <= 0:
        return static_cost
    average_wall_seconds = total_wall_seconds / attempt_count
    censor_penalty = math.ceil(
        min(1.0, censored_count / attempt_count) * 500
    )
    return static_cost + math.ceil(average_wall_seconds) + censor_penalty


def _record_candidate_screening_observation(
    observations: dict[str, dict[str, float | int]],
    *,
    case_ids: tuple[str, ...],
    attempt: Mapping[str, object],
) -> None:
    """Update campaign-local case cost without retaining replay payloads."""

    wall_seconds = _non_negative_screening_float(attempt.get("wall_seconds"))
    per_case_wall_seconds = wall_seconds / max(1, len(case_ids))
    right_censored = _screening_attempt_is_budget_censored(attempt)
    details = attempt.get("details")
    invalid_control = bool(
        isinstance(details, Mapping)
        and _screening_gate_has_invalid_control(
            GateResult(
                gate_name="candidate_replay",
                passed=attempt.get("passed") is True,
                reason="screening observation",
                details=details,
            )
        )
    )
    termination_axes = _screening_attempt_termination_axes(attempt)
    baseline_status = (
        str(details.get("baseline_status") or "")
        if isinstance(details, Mapping)
        else ""
    )
    candidate_status = (
        str(details.get("candidate_status") or "")
        if isinstance(details, Mapping)
        else ""
    )
    baseline_timeout = bool(
        isinstance(details, Mapping)
        and _framework_phase_timeout(details.get("baseline_failure"))
    )
    candidate_timeout = bool(
        isinstance(details, Mapping)
        and _framework_phase_timeout(details.get("candidate_failure"))
    )
    for case_id in case_ids:
        current = observations.setdefault(case_id, {})
        current["attempt_count"] = (
            _non_negative_int(current.get("attempt_count")) + 1
        )
        current["total_wall_seconds"] = (
            _non_negative_screening_float(
                current.get("total_wall_seconds")
            )
            + per_case_wall_seconds
        )
        current["right_censored_count"] = (
            _non_negative_int(current.get("right_censored_count"))
            + int(right_censored)
        )
        current["passed_count"] = (
            _non_negative_int(current.get("passed_count"))
            + int(attempt.get("passed") is True)
        )
        if baseline_status and baseline_status not in {"blocked", "not_run"}:
            current["baseline_attempt_count"] = (
                _non_negative_int(current.get("baseline_attempt_count")) + 1
            )
            current["baseline_success_count"] = (
                _non_negative_int(current.get("baseline_success_count"))
                + int(baseline_status == ReplayExecutionStatus.SUCCEEDED.value)
            )
            current["baseline_timeout_count"] = (
                _non_negative_int(current.get("baseline_timeout_count"))
                + int(baseline_timeout)
            )
        if candidate_status and candidate_status not in {"blocked", "not_run"}:
            current["candidate_attempt_count"] = (
                _non_negative_int(current.get("candidate_attempt_count")) + 1
            )
            current["candidate_success_count"] = (
                _non_negative_int(current.get("candidate_success_count"))
                + int(candidate_status == ReplayExecutionStatus.SUCCEEDED.value)
            )
            current["candidate_timeout_count"] = (
                _non_negative_int(current.get("candidate_timeout_count"))
                + int(candidate_timeout)
            )
        if invalid_control or "invalid_control_count" in current:
            current["invalid_control_count"] = (
                _non_negative_int(current.get("invalid_control_count"))
                + int(invalid_control)
            )
        for axis in termination_axes:
            field_name = f"termination_{axis}_count"
            current[field_name] = (
                _non_negative_int(current.get(field_name)) + 1
            )


def _screening_attempt_termination_axes(
    attempt: Mapping[str, object],
) -> tuple[str, ...]:
    axes: set[str] = set()
    stack: list[object] = [attempt.get("details")]
    inspected = 0
    while stack and inspected < 256:
        current = stack.pop()
        inspected += 1
        if isinstance(current, Mapping):
            axis = current.get("termination_budget_axis")
            if isinstance(axis, str) and axis.strip():
                axes.add(axis.strip())
            stack.extend(current.values())
        elif isinstance(current, (list, tuple)):
            stack.extend(current)
    return tuple(sorted(axes))


def _screening_termination_axis_counts(
    attempts: Iterable[Mapping[str, object]],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for attempt in attempts:
        for axis in _screening_attempt_termination_axes(attempt):
            counts[axis] = counts.get(axis, 0) + 1
    return counts


def _non_negative_screening_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return 0.0
    result = float(value)
    if not math.isfinite(result) or result < 0:
        return 0.0
    return result


def _candidate_screening_qualification_case_limit(
    *,
    candidate_count: int,
    configured_max_cases: int,
) -> int:
    """Bound early screening while preserving multi-case authoritative proof.

    Screening is a ranking stage, not acceptance.  Growing the qualification
    panel logarithmically avoids running every generated candidate over the
    complete representative panel; the promoted candidate must still pass the
    authoritative full-dataset replay later in the pipeline.
    """

    if candidate_count <= 0:
        raise ValueError("candidate screening requires a positive candidate count")
    if configured_max_cases <= 0:
        raise ValueError("candidate screening max cases must be positive")
    adaptive_limit = max(1, math.ceil(math.log2(candidate_count)))
    return min(configured_max_cases, adaptive_limit)


def dataset_case_strata(case: EvalCase) -> set[str]:
    """Return candidate-independent strata shared by screening and measurement."""

    strata: set[str] = set()
    for namespace, values in (("metadata", case.metadata), ("source", case.source)):
        for key in ("task_type", "category", "cluster", "domain", "kind", "role"):
            value = values.get(key)
            if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                strata.add(f"{namespace}:{key}:{value}")
    if case.verification_command:
        strata.add("verification:command")
    if case.expected_output is not None:
        strata.add("verification:expected_output")
    if case.trace_pack is not None:
        strata.add("evidence:trace_pack")
    if not strata:
        strata.add("task:unclassified")
    return strata


def _candidate_screening_case_distance(
    index: int,
    *,
    selected_indices: tuple[int, ...],
    case_count: int,
) -> float:
    if not selected_indices:
        return 0.0
    denominator = max(1, case_count - 1)
    return min(abs(index - selected) / denominator for selected in selected_indices)
