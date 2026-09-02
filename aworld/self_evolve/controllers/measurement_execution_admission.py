"""Failure attribution and admission diagnostics for paired replay."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Mapping

from aworld.self_evolve.controllers.screening import (
    SCREENING_BUDGET_CENSORED_CODE as _SCREENING_BUDGET_CENSORED_CODE,
)
from aworld.self_evolve.controllers.screening_execution import (
    _replay_artifact_path,
)
from aworld.self_evolve.controllers.screening_helpers import (
    _candidate_replay_has_repairable_capability_failure,
    _candidate_task_plane_intervention_observation,
)
from aworld.self_evolve.datasets import SelfEvolveDataset
from aworld.self_evolve.failure_events import (
    AggregatedReplayFailure,
    FailureOwner,
    FailureScope,
    FailureStage,
    ReplayExecutionStatus,
    ReplayFailureEvent,
    ReplayFailureObservation,
    aggregate_replay_failure_observations,
    aggregate_replay_failures,
)
from aworld.self_evolve.recovery_trace import replay_recovery_trace
from aworld.self_evolve.replay import (
    CandidateReplayResult,
    NormalizedReplayMembers,
    ReplayVariantResult,
    candidate_replay_pair_coverage,
    normalize_replay_members,
)
from aworld.self_evolve.replay_gates import _system_owned_repetition_failures


def _replay_gate_details(
    replay_result: CandidateReplayResult,
    *,
    dataset: SelfEvolveDataset,
    normalized: NormalizedReplayMembers | None = None,
    candidate_requires_intervention_exposure: bool = False,
    candidate_requires_service_intervention: bool = False,
    candidate_requires_skill_activation: bool = False,
    bounded_screening: bool = False,
) -> dict[str, object]:
    normalized = normalized or normalize_replay_members(
        dataset=dataset,
        replay_result=replay_result,
    )
    if (
        candidate_requires_intervention_exposure
        and not candidate_requires_service_intervention
        and not candidate_requires_skill_activation
    ):
        # Backward-compatible direct callers used the single boolean for the
        # original replay-service intervention contract.
        candidate_requires_service_intervention = True
    def compatibility_failure(variant: ReplayVariantResult) -> object:
        return (
            variant.failure.compatibility_dict()
            if isinstance(variant.failure, ReplayFailureEvent)
            else variant.failure
        )

    details: dict[str, object] = {
        "baseline_status": replay_result.baseline.status,
        "candidate_status": replay_result.candidate.status,
        "baseline_failure": compatibility_failure(replay_result.baseline),
        "candidate_failure": compatibility_failure(replay_result.candidate),
        "baseline_failure_event": (
            replay_result.baseline.failure.to_dict()
            if isinstance(replay_result.baseline.failure, ReplayFailureEvent)
            else None
        ),
        "candidate_failure_event": (
            replay_result.candidate.failure.to_dict()
            if isinstance(replay_result.candidate.failure, ReplayFailureEvent)
            else None
        ),
        **candidate_replay_pair_coverage(
            dataset=dataset,
            replay_result=replay_result,
            normalized=normalized,
        ),
        "adaptation_fingerprint": replay_result.request.adaptation_fingerprint,
        "support_fingerprint": replay_result.request.support_fingerprint,
        "timeout_envelope_fingerprint": (
            replay_result.request.timeout_envelope_fingerprint
        ),
        "workspace_seed_fingerprint": (
            replay_result.request.workspace_seed_fingerprint
        ),
        "dataset_fingerprint": replay_result.request.dataset_fingerprint,
        "baseline_skill_fingerprint": (
            replay_result.request.baseline_skill_fingerprint
        ),
        "diagnostic_refs": [
            str(Path(_replay_artifact_path(replay_result)) / "request.json"),
            str(
                Path(_replay_artifact_path(replay_result))
                / "members"
                / "manifest.json"
            ),
        ],
    }
    details["member_count"] = len(normalized.members) + len(
        normalized.missing_case_ids
    )
    if normalized.failure_events:
        details["normalization_failures"] = [
            event.to_dict() for event in normalized.failure_events
        ]
    causal_failures = aggregate_replay_failures(
        replay_result,
        normalized=normalized,
    )
    framework_blocker: ReplayFailureEvent | None = None
    if causal_failures:
        details["causal_failure_events"] = [
            event.to_dict() for event in causal_failures
        ]
        framework_blocker = next(
            (
                event
                for event in causal_failures
                if event.owner
                in {FailureOwner.FRAMEWORK, FailureOwner.INFRASTRUCTURE}
                and event.scope
                in {FailureScope.SHARED_RUN, FailureScope.MEMBER}
            ),
            None,
        )
        if framework_blocker is not None:
            details.update(
                {
                    "failure_class": (
                        "infrastructure"
                        if framework_blocker.owner is FailureOwner.INFRASTRUCTURE
                        else "framework"
                    ),
                    "failure_owner": framework_blocker.owner.value,
                    "failure_scope": framework_blocker.scope.value,
                    "failure_stage": framework_blocker.stage.value,
                    "repairable": framework_blocker.repairable,
                    "code": framework_blocker.code,
                }
            )
    screening_censor_basis = (
        _screening_budget_censor_basis(normalized)
        if bounded_screening
        else None
    )
    screening_budget_censored = screening_censor_basis is not None
    completion_failure = (
        None
        if screening_budget_censored
        else _paired_candidate_completion_failure(normalized)
    )
    if completion_failure is not None:
        completion_event, completion_evidence = completion_failure
        completion_observations = tuple(
            ReplayFailureObservation(
                event=completion_event,
                case_id=member.case_id,
                run_id=replay_result.request.run_id,
                task_id=member.request.task_id,
                candidate_id=replay_result.request.candidate_id,
            )
            for member in normalized.members
            if _variant_has_progressing_task_timeout(member.candidate)
        ) or (ReplayFailureObservation(event=completion_event),)
        completion_aggregate = aggregate_replay_failure_observations(
            completion_observations
        )[0]
        raw_events = details.get("causal_failure_events")
        event_payloads = list(raw_events) if isinstance(raw_events, list) else []
        event_payloads.append(completion_aggregate.to_dict())
        details["causal_failure_events"] = event_payloads
        details["paired_candidate_completion_evidence"] = completion_evidence
        details["paired_candidate_completion_failure_event"] = (
            completion_event.to_dict()
        )
    if screening_budget_censored:
        censor_event = ReplayFailureEvent(
            code=_SCREENING_BUDGET_CENSORED_CODE,
            owner=FailureOwner.FRAMEWORK,
            stage=FailureStage.TASK_ROLLOUT,
            scope=FailureScope.MEMBER,
            repairable=False,
            category="candidate_screening",
            summary=(
                "bounded screening ended before a directional candidate "
                "comparison could be observed"
            ),
            diagnostics={
                "member_count": len(normalized.members),
                "timeout_seconds": replay_result.request.timeout_seconds,
                "max_steps": replay_result.request.max_steps,
                "max_tool_calls": replay_result.request.max_tool_calls,
                "censor_basis": screening_censor_basis,
            },
            artifact_refs=tuple(details["diagnostic_refs"]),
        )
        raw_events = details.get("causal_failure_events")
        event_payloads = list(raw_events) if isinstance(raw_events, list) else []
        event_payloads.append(censor_event.to_dict())
        details["causal_failure_events"] = event_payloads
    candidate_execution_observed = any(
        member.candidate.executed for member in normalized.members
    )
    intervention_observation = _candidate_task_plane_intervention_observation(
        normalized,
        require_service_intervention=(
            candidate_requires_service_intervention
        ),
        require_skill_activation=candidate_requires_skill_activation,
        expected_skill_package_fingerprint=(
            replay_result.request.verified_candidate_package_fingerprint
        ),
    )
    intervention_observed = (
        (
            not candidate_requires_intervention_exposure
            or intervention_observation["observed"] is True
        )
        if candidate_execution_observed
        else None
    )
    details["candidate_execution_observed"] = candidate_execution_observed
    details["candidate_intervention_required"] = (
        candidate_requires_intervention_exposure
    )
    details["candidate_service_intervention_required"] = (
        candidate_requires_service_intervention
    )
    details["candidate_skill_activation_required"] = (
        candidate_requires_skill_activation
    )
    details["candidate_service_intervention_observed"] = (
        intervention_observation["service_observed"]
    )
    details["candidate_skill_activation_observed"] = (
        intervention_observation["skill_activation_observed"]
    )
    details["candidate_intervention_observed"] = intervention_observed
    recovery_trace = replay_recovery_trace(normalized.members)
    if recovery_trace is not None:
        recovery_trace["candidate_execution_observed"] = (
            candidate_execution_observed
        )
        recovery_trace["candidate_intervention_required"] = (
            candidate_requires_intervention_exposure
        )
        recovery_trace["candidate_intervention_observed"] = intervention_observed
        recovery_trace["candidate_service_intervention_observed"] = (
            intervention_observation["service_observed"]
        )
        recovery_trace["candidate_skill_activation_observed"] = (
            intervention_observation["skill_activation_observed"]
        )
        if intervention_observed is False:
            guidance = recovery_trace.get("guidance")
            recovery_trace["guidance"] = list(guidance or []) + [
                "repair_target_selection_or_replay_context_before_candidate"
            ]
        details["recovery_trace"] = recovery_trace
        candidate_system_failures = tuple(
            event
            for member in normalized.members
            for event in _system_owned_repetition_failures(
                member.candidate
            )
        )
        recovery_failure = (
            None
            if not candidate_execution_observed
            or candidate_system_failures
            or framework_blocker is not None
            or screening_budget_censored
            or completion_failure is not None
            else (
                _candidate_recovery_failure_event(recovery_trace)
                if intervention_observed
                else _candidate_intervention_unobserved_failure_event(
                    recovery_trace
                )
            )
        )
        if recovery_failure is not None:
            raw_events = details.get("causal_failure_events")
            event_payloads = (
                list(raw_events) if isinstance(raw_events, list) else []
            )
            event_payloads.append(recovery_failure.to_dict())
            details["causal_failure_events"] = event_payloads
            if recovery_failure.owner is FailureOwner.CANDIDATE:
                details.update(
                    {
                        "code": recovery_failure.code,
                        "failure_class": "candidate",
                        "failure_owner": recovery_failure.owner.value,
                        "failure_scope": recovery_failure.scope.value,
                        "failure_stage": recovery_failure.stage.value,
                        "repairable": recovery_failure.repairable,
                    }
                )
            else:
                details.update(
                    {
                        "code": recovery_failure.code,
                        "failure_class": "framework",
                        "failure_owner": recovery_failure.owner.value,
                        "failure_scope": recovery_failure.scope.value,
                        "failure_stage": recovery_failure.stage.value,
                        "repairable": recovery_failure.repairable,
                        "next_action": "repair_framework_control_selection",
                    }
                )
    elif (
        intervention_observed is False
        and framework_blocker is None
        and not screening_budget_censored
        and completion_failure is None
    ):
        recovery_failure = _candidate_intervention_unobserved_failure_event(
            {
                "member_count": len(normalized.members),
                "candidate_repetition_count": sum(
                    len(member.candidate.repetition_results) or 1
                    for member in normalized.members
                ),
                "candidate_service_intervention_observed": (
                    intervention_observation["service_observed"]
                ),
                "candidate_skill_activation_observed": (
                    intervention_observation["skill_activation_observed"]
                ),
            }
        )
        raw_events = details.get("causal_failure_events")
        event_payloads = (
            list(raw_events) if isinstance(raw_events, list) else []
        )
        event_payloads.append(recovery_failure.to_dict())
        details.update(
            {
                "causal_failure_events": event_payloads,
                "code": recovery_failure.code,
                "failure_class": "framework",
                "failure_owner": recovery_failure.owner.value,
                "failure_scope": recovery_failure.scope.value,
                "failure_stage": recovery_failure.stage.value,
                "repairable": recovery_failure.repairable,
                "next_action": "repair_framework_control_selection",
            }
        )
    if normalized.members:
        details["failed_members"] = [
            {
                "case_id": member.case_id,
                "baseline_status": member.baseline.status,
                "candidate_status": member.candidate.status,
                "baseline_failure": compatibility_failure(member.baseline),
                "candidate_failure": compatibility_failure(member.candidate),
                "baseline_failure_event": (
                    member.baseline.failure.to_dict()
                    if isinstance(member.baseline.failure, ReplayFailureEvent)
                    else None
                ),
                "candidate_failure_event": (
                    member.candidate.failure.to_dict()
                    if isinstance(member.candidate.failure, ReplayFailureEvent)
                    else None
                ),
            }
            for member in normalized.members
            if not member.succeeded
        ]
    candidate_screening_deadline = (
        _paired_candidate_screening_deadline_failure(normalized)
        if bounded_screening
        else None
    )
    if candidate_screening_deadline is not None:
        deadline_event, deadline_case_ids = candidate_screening_deadline
        deadline_observations = tuple(
            ReplayFailureObservation(
                event=deadline_event,
                case_id=case_id,
                run_id=replay_result.request.run_id,
                candidate_id=replay_result.request.candidate_id,
            )
            for case_id in deadline_case_ids
        )
        deadline_aggregate = aggregate_replay_failure_observations(
            deadline_observations
        )[0]
        raw_events = details.get("causal_failure_events")
        event_payloads = list(raw_events) if isinstance(raw_events, list) else []
        event_payloads.append(deadline_aggregate.to_dict())
        details.update(
            {
                "causal_failure_events": event_payloads,
                "code": deadline_event.code,
                "failure_class": "candidate",
                "failure_owner": deadline_event.owner.value,
                "failure_scope": deadline_event.scope.value,
                "failure_stage": deadline_event.stage.value,
                "repairable": True,
                "evaluator_skipped": True,
                "candidate_screening_deadline_case_ids": list(
                    deadline_case_ids
                ),
            }
        )
    recovery_trace_details = details.get("recovery_trace")
    intervention_unobserved = bool(
        isinstance(recovery_trace_details, Mapping)
        and recovery_trace_details.get("candidate_intervention_required") is True
        and recovery_trace_details.get("candidate_intervention_observed") is False
    )
    if (
        _candidate_replay_has_repairable_capability_failure(replay_result)
        and not intervention_unobserved
        and not screening_budget_censored
    ):
        capability_event = next(
            (
                event
                for event in causal_failures
                if event.owner is FailureOwner.CANDIDATE and event.repairable
            ),
            None,
        )
        if capability_event is not None:
            details.update(
                {
                    "code": capability_event.code,
                    "failure_class": "candidate",
                    "failure_owner": capability_event.owner.value,
                    "failure_scope": capability_event.scope.value,
                    "failure_stage": capability_event.stage.value,
                    "repairable": True,
                }
            )
    if screening_budget_censored:
        details.update(
            {
                "code": _SCREENING_BUDGET_CENSORED_CODE,
                "failure_class": "framework",
                "failure_owner": FailureOwner.FRAMEWORK.value,
                "failure_scope": FailureScope.MEMBER.value,
                "failure_stage": FailureStage.TASK_ROLLOUT.value,
                "repairable": False,
                "screening_outcome": "right_censored",
                "screening_budget_censored": True,
                "screening_censor_basis": screening_censor_basis,
            }
        )
    invalid_control_events = [
        event
        for event in _replay_decision_failure_events(
            replay_result,
            normalized=normalized,
        )
        if event.code in {
            "authoritative_replay_invalid_control",
            "trusted_measurement_invalid_control_frontier",
        }
        and event.owner is FailureOwner.FRAMEWORK
    ]
    candidate_repair_observed = (
        _candidate_replay_has_repairable_capability_failure(replay_result)
        and not intervention_unobserved
    ) or candidate_screening_deadline is not None
    if (
        invalid_control_events
        and not screening_budget_censored
        and not candidate_repair_observed
    ):
        details.update(
            {
                "code": "control_not_comparable",
                "failure_class": "measurement",
                "failure_owner": FailureOwner.FRAMEWORK.value,
                "failure_scope": FailureScope.SHARED_RUN.value,
                "failure_stage": FailureStage.EVALUATION.value,
                "repairable": True,
                "next_action": "repair_measurement",
                "effect": None,
            }
        )
    elif invalid_control_events and candidate_repair_observed:
        # A sick baseline makes the pair unsuitable for effect measurement,
        # but it cannot erase an independently observed candidate-owned runtime
        # contract violation.  Repair the candidate first; measurement can be
        # retried after the counterexample is removed.
        details["invalid_control_secondary"] = True
        details["invalid_control_event_ids"] = [
            event.event_id for event in invalid_control_events
        ]
    return details


def _paired_candidate_screening_deadline_failure(
    normalized: NormalizedReplayMembers,
) -> tuple[ReplayFailureEvent, tuple[str, ...]] | None:
    """Promote candidate-only screening deadlines to candidate repair evidence."""

    affected_case_ids: list[str] = []
    timeout_seconds: list[float] = []
    for member in normalized.members:
        if not member.baseline.succeeded:
            continue
        failure = member.candidate.failure
        if (
            member.candidate.status is not ReplayExecutionStatus.FAILED
            or not isinstance(failure, ReplayFailureEvent)
            or failure.code != "replay_member_phase_timeout"
            or failure.diagnostics.get("phase") != "candidate"
        ):
            continue
        affected_case_ids.append(member.case_id)
        timeout = failure.diagnostics.get("timeout_seconds")
        if (
            isinstance(timeout, (int, float))
            and not isinstance(timeout, bool)
            and math.isfinite(float(timeout))
            and float(timeout) > 0
        ):
            timeout_seconds.append(float(timeout))
    if not affected_case_ids:
        return None
    return (
        ReplayFailureEvent(
            code="candidate_screening_deadline_exceeded",
            owner=FailureOwner.CANDIDATE,
            stage=FailureStage.TASK_ROLLOUT,
            scope=FailureScope.CANDIDATE,
            repairable=True,
            category="candidate_screening",
            summary=(
                "candidate exceeded the screening execution envelope after a "
                "healthy baseline completed"
            ),
            diagnostics={
                "case_ids": affected_case_ids[:32],
                "timeout_seconds": sorted(set(timeout_seconds))[:8],
                "baseline_control_valid": True,
                "candidate_execution_observed": True,
            },
        ),
        tuple(dict.fromkeys(affected_case_ids)),
    )


def _variant_is_screening_timeout(variant: ReplayVariantResult) -> bool:
    repetitions = variant.repetition_results or (variant,)
    if not repetitions:
        return False
    for repetition in repetitions:
        failure = repetition.failure
        if not isinstance(failure, ReplayFailureEvent):
            return False
        if (
            repetition.status is not ReplayExecutionStatus.FAILED
            or failure.stage
            not in {FailureStage.TASK_ROLLOUT, FailureStage.LEGACY_IMPORT}
            or failure.code
            not in {
                "timeoutexpired",
                "task_rollout_timeout",
                "replay_task_timeout_with_recoverable_evidence",
                "replay_evidence_finalization_timeout",
            }
        ):
            return False
        if failure.owner is FailureOwner.TASK:
            continue
        # Compatibility for persisted artifacts written before paired causal
        # attribution: a candidate-role timeout with proven data-plane progress
        # is still a physical timeout, not a capability failure.
        if not (
            failure.owner is FailureOwner.CANDIDATE
            and _failure_completed_data_plane_operations(failure)
        ):
            return False
    return True


def _failure_completed_data_plane_operations(
    failure: ReplayFailureEvent,
) -> tuple[str, ...]:
    raw = failure.diagnostics.get("completed_data_plane_operations")
    if not isinstance(raw, (list, tuple)):
        compatibility = failure.compatibility_dict()
        raw = compatibility.get("completed_data_plane_operations")
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(
        dict.fromkeys(
            text
            for item in raw[:32]
            if (text := str(item or "").strip())
        )
    )


def _variant_has_progressing_task_timeout(variant: ReplayVariantResult) -> bool:
    repetitions = variant.repetition_results or (variant,)
    return any(
        repetition.status is ReplayExecutionStatus.FAILED
        and isinstance(repetition.failure, ReplayFailureEvent)
        and repetition.failure.stage is FailureStage.TASK_ROLLOUT
        and repetition.failure.code
        in {
            "timeoutexpired",
            "task_rollout_timeout",
            "replay_task_timeout_with_recoverable_evidence",
            "replay_evidence_finalization_timeout",
        }
        and bool(_failure_completed_data_plane_operations(repetition.failure))
        for repetition in repetitions
    )


def _paired_candidate_completion_failure(
    normalized: NormalizedReplayMembers,
) -> tuple[ReplayFailureEvent, dict[str, object]] | None:
    """Derive task-completion repair only after paired executions exist."""

    operations: list[str] = []
    termination_axes: list[str] = []
    candidate_timeout_count = 0
    baseline_timeout_count = 0
    terminal_synthesis_attempted = False
    for member in normalized.members:
        if _variant_is_screening_timeout(member.baseline):
            baseline_timeout_count += 1
        for repetition in member.candidate.repetition_results or (member.candidate,):
            failure = repetition.failure
            if (
                repetition.status is not ReplayExecutionStatus.FAILED
                or not isinstance(failure, ReplayFailureEvent)
                or failure.stage is not FailureStage.TASK_ROLLOUT
                or failure.code
                not in {
                    "timeoutexpired",
                    "task_rollout_timeout",
                    "replay_task_timeout_with_recoverable_evidence",
                    "replay_evidence_finalization_timeout",
                }
            ):
                continue
            completed = _failure_completed_data_plane_operations(failure)
            if not completed:
                continue
            candidate_timeout_count += 1
            for operation in completed:
                if operation not in operations:
                    operations.append(operation)
            axis = failure.diagnostics.get("termination_budget_axis")
            if isinstance(axis, str) and axis and axis not in termination_axes:
                termination_axes.append(axis)
            terminal_synthesis_attempted = bool(
                terminal_synthesis_attempted
                or failure.diagnostics.get("terminal_synthesis_attempted") is True
            )
    if not operations:
        return None
    evidence: dict[str, object] = {
        "completed_data_plane_operations": operations[:32],
        "candidate_timeout_count": candidate_timeout_count,
        "baseline_timeout_count": baseline_timeout_count,
        "termination_budget_axes": termination_axes,
        "terminal_synthesis_attempted": terminal_synthesis_attempted,
        "causal_attribution_stage": "paired_replay",
    }
    return (
        ReplayFailureEvent(
            code="target_behavior_completion_missing",
            owner=FailureOwner.CANDIDATE,
            stage=FailureStage.TASK_ROLLOUT,
            scope=FailureScope.CANDIDATE,
            repairable=True,
            category="target_behavior",
            summary=(
                "candidate completed data-plane interactions but did not produce "
                "a terminal task result within the execution envelope"
            ),
            diagnostics=evidence,
        ),
        evidence,
    )


def _screening_budget_censor_basis(
    normalized: NormalizedReplayMembers,
) -> str | None:
    """Classify an inconclusive bounded-screening measurement.

    A screening run can reach its deliberately small horizon in two ways:
    both variants can time out after executing, or the baseline can hit the
    per-member hard deadline before the candidate is allowed to run.  The
    latter is not an invalid control: authoritative replay has a larger
    envelope and is the plane that should decide the candidate.
    """

    if not normalized.valid or not normalized.members:
        return None
    if all(
        _variant_is_screening_timeout(member.baseline)
        and _variant_is_screening_timeout(member.candidate)
        for member in normalized.members
    ):
        return "paired_horizon"
    if all(
        _variant_is_screening_baseline_deadline(member.baseline)
        and _variant_blocked_by_invalid_control(member.candidate)
        for member in normalized.members
    ):
        return "baseline_horizon"
    return None


def _variant_is_screening_baseline_deadline(
    variant: ReplayVariantResult,
) -> bool:
    repetitions = variant.repetition_results or (variant,)
    if not repetitions:
        return False
    for repetition in repetitions:
        failure = repetition.failure
        if not isinstance(
            failure,
            (ReplayFailureEvent, AggregatedReplayFailure),
        ):
            return False
        phase = repetition.metrics.get("member_phase")
        if isinstance(failure, ReplayFailureEvent):
            phase = failure.diagnostics.get("phase") or phase
        if not (
            repetition.status is ReplayExecutionStatus.FAILED
            and failure.code == "replay_member_phase_timeout"
            and failure.owner is FailureOwner.FRAMEWORK
            and failure.stage is FailureStage.EVALUATION
            and failure.scope is FailureScope.MEMBER
            and phase == "baseline"
        ):
            return False
    return True


def _variant_blocked_by_invalid_control(
    variant: ReplayVariantResult,
) -> bool:
    return bool(
        variant.status is ReplayExecutionStatus.BLOCKED
        and any(
            event.code == "authoritative_replay_invalid_control"
            and event.owner is FailureOwner.FRAMEWORK
            for event in variant.blocked_by
        )
    )


def _replay_decision_failure_events(
    replay_result: CandidateReplayResult,
    *,
    normalized: NormalizedReplayMembers,
) -> tuple[ReplayFailureEvent, ...]:
    """Return leaf and wrapper events used to make replay decisions.

    Causal aggregation intentionally collapses wrapper events to their leaf
    causes.  Admission policy also needs wrappers such as
    ``authoritative_replay_invalid_control`` because they carry the shared-run
    ownership and repair action.  Keep this projection local to decision
    making so aggregate telemetry remains unchanged.
    """

    result: list[ReplayFailureEvent] = []
    seen: set[str] = set()

    def add_variant(variant: ReplayVariantResult) -> None:
        repetitions = variant.repetition_results or (variant,)
        for item in (*repetitions, variant):
            events = (
                *(
                    (item.failure,)
                    if isinstance(item.failure, ReplayFailureEvent)
                    else ()
                ),
                *item.blocked_by,
            )
            for event in events:
                if event.event_id in seen:
                    continue
                seen.add(event.event_id)
                result.append(event)

    add_variant(replay_result.baseline)
    add_variant(replay_result.candidate)
    for member in normalized.members:
        add_variant(member.baseline)
        add_variant(member.candidate)
    return tuple(result)


def _candidate_recovery_failure_event(
    recovery_trace: Mapping[str, object],
) -> ReplayFailureEvent | None:
    candidate_repetitions = recovery_trace.get("candidate_repetition_count")
    candidate_success_rate = recovery_trace.get("candidate_success_rate")
    if (
        isinstance(candidate_repetitions, bool)
        or not isinstance(candidate_repetitions, (int, float))
        or int(candidate_repetitions) <= 0
        or isinstance(candidate_success_rate, bool)
        or not isinstance(candidate_success_rate, (int, float))
        or float(candidate_success_rate) >= 1.0
    ):
        return None
    return ReplayFailureEvent(
        code="candidate_recovery_incomplete",
        owner=FailureOwner.CANDIDATE,
        stage=FailureStage.TASK_ROLLOUT,
        scope=FailureScope.CANDIDATE,
        repairable=True,
        category="recovery_trace",
        summary=(
            "candidate did not produce stable recovery across all executed "
            "trajectory members and repetitions"
        ),
        diagnostics={
            "member_count": recovery_trace.get("member_count"),
            "candidate_repetition_count": candidate_repetitions,
            "candidate_success_rate": candidate_success_rate,
            "recovered_member_count": recovery_trace.get(
                "recovered_member_count"
            ),
            "stable_recovery_member_count": recovery_trace.get(
                "stable_recovery_member_count"
            ),
        },
    )


def _candidate_intervention_unobserved_failure_event(
    recovery_trace: Mapping[str, object],
) -> ReplayFailureEvent:
    return ReplayFailureEvent(
        code="candidate_intervention_unobserved",
        owner=FailureOwner.FRAMEWORK,
        stage=FailureStage.ADAPTATION,
        scope=FailureScope.SHARED_RUN,
        repairable=True,
        category="recovery_trace",
        summary=(
            "task rollout did not exercise the candidate-owned replay intervention; "
            "repair target selection or replay context before generating "
            "another candidate"
        ),
        diagnostics={
            "member_count": recovery_trace.get("member_count"),
            "candidate_repetition_count": recovery_trace.get(
                "candidate_repetition_count"
            ),
            "candidate_intervention_required": True,
            "candidate_intervention_observed": False,
            "candidate_service_intervention_observed": recovery_trace.get(
                "candidate_service_intervention_observed"
            ),
            "candidate_skill_activation_observed": recovery_trace.get(
                "candidate_skill_activation_observed"
            ),
        },
    )


def _candidate_intervention_unobserved(
    details: Mapping[str, object],
) -> bool:
    recovery_trace = details.get("recovery_trace")
    return bool(
        details.get("code") == "candidate_intervention_unobserved"
        or (
            details.get("candidate_intervention_required") is True
            and details.get("candidate_intervention_observed") is False
        )
        or (
            isinstance(recovery_trace, Mapping)
            and recovery_trace.get("candidate_intervention_required") is True
            and recovery_trace.get("candidate_intervention_observed") is False
        )
    )
