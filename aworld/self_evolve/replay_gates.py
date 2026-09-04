"""Replay admission, confidence, and stability gates."""

from __future__ import annotations

from collections.abc import Mapping

from aworld.self_evolve.campaign_policy import (
    is_verified_apply_policy as _is_verified_apply_policy,
)
from aworld.self_evolve.datasets import SelfEvolveDataset
from aworld.self_evolve.evaluation_reporting import _metric_number
from aworld.self_evolve.failure_events import (
    FailureEventSource,
    FailureOwner,
    FailureScope,
    FailureStage,
    ReplayExecutionStatus,
    ReplayFailureEvent,
    aggregate_replay_failures,
)
from aworld.self_evolve.repair_conformance_diagnostics import (
    _gate_has_typed_shared_infrastructure_failure,
)
from aworld.self_evolve.replay import (
    CandidateReplayResult,
    ReplayVariantResult,
    candidate_replay_pair_coverage,
    normalize_replay_members,
)
from aworld.self_evolve.types import EvaluationSummary, GateResult


def _gate_has_typed_shared_measurement_failure(gate: GateResult) -> bool:
    """Return true only when the shared measurement experiment is invalid."""

    details = gate.details
    if not isinstance(details, Mapping):
        return False
    return bool(
        gate.gate_name
        in {
            "candidate_replay",
            "replay_confidence",
            "fresh_evaluator_rerun",
            "trusted_improvement_measurement",
            "cost_latency_regression",
        }
        and details.get("failure_class")
        in {"measurement", "framework", "infrastructure"}
        and details.get("failure_owner")
        in {FailureOwner.FRAMEWORK.value, FailureOwner.INFRASTRUCTURE.value}
        and details.get("failure_scope") == FailureScope.SHARED_RUN.value
        and details.get("repairable") is True
    )

def _gate_is_replay_execution_infrastructure_failure(gate: GateResult) -> bool:
    details = gate.details
    return bool(
        gate.gate_name == "candidate_replay"
        and isinstance(details, Mapping)
        and details.get("code") == "candidate_replay_infrastructure_error"
        and _gate_has_typed_shared_infrastructure_failure(gate)
    )

def _gate_blocks_measurement_materialization(gate: GateResult) -> bool:
    """Prevent derived statistical gates from hiding an execution blocker."""

    return bool(
        _gate_has_typed_shared_measurement_failure(gate)
        or _gate_is_replay_execution_infrastructure_failure(gate)
    )

def _system_owned_repetition_failures(
    *variants: ReplayVariantResult,
) -> tuple[ReplayFailureEvent, ...]:
    return tuple(
        result.failure
        for variant in variants
        for result in (variant.repetition_results or (variant,))
        if result.status is ReplayExecutionStatus.FAILED
        and isinstance(result.failure, ReplayFailureEvent)
        and result.failure.owner
        in {FailureOwner.FRAMEWORK, FailureOwner.INFRASTRUCTURE}
    )

def _environment_fingerprint_drift_gate(
    expected_fingerprint: str,
    observed_fingerprint: str,
) -> GateResult | None:
    if expected_fingerprint == observed_fingerprint:
        return None
    failure_event = ReplayFailureEvent(
        code="environment_fingerprint_drift",
        owner=FailureOwner.INFRASTRUCTURE,
        stage=FailureStage.ADAPTATION,
        scope=FailureScope.SHARED_RUN,
        repairable=False,
        category="environment_health",
        summary="replay environment changed during one self-evolve run",
        diagnostics={
            "expected_environment_fingerprint": expected_fingerprint,
            "observed_environment_fingerprint": observed_fingerprint,
        },
    )
    event_payload = failure_event.to_dict()
    return GateResult(
        gate_name="replay_environment_health",
        passed=False,
        reason="replay environment fingerprint changed during the active run",
        details={
            "failure_class": FailureOwner.INFRASTRUCTURE.value,
            "failure_owner": FailureOwner.INFRASTRUCTURE.value,
            "failure_scope": FailureScope.SHARED_RUN.value,
            "failure_source": FailureEventSource.NATIVE.value,
            "repairable": False,
            "code": "environment_fingerprint_drift",
            "expected_environment_fingerprint": expected_fingerprint,
            "observed_environment_fingerprint": observed_fingerprint,
            "failure_event": event_payload,
            "causal_failure_events": [event_payload],
        },
    )

def _replay_confidence_gate(
    replay_result: CandidateReplayResult | None,
    *,
    dataset: SelfEvolveDataset,
    apply_policy: str,
) -> GateResult | None:
    if replay_result is None or not _is_verified_apply_policy(apply_policy):
        return None
    normalized = normalize_replay_members(
        dataset=dataset,
        replay_result=replay_result,
    )
    coverage = candidate_replay_pair_coverage(
        dataset=dataset,
        replay_result=replay_result,
        normalized=normalized,
    )
    if coverage["candidate_executed_count"] == 0:
        return None
    baseline_source = replay_result.baseline.metrics.get("replay_source")
    candidate_repetitions = replay_result.candidate.metrics.get("repetition_count")
    candidate_successful_repetitions = replay_result.candidate.metrics.get(
        "successful_repetition_count"
    )
    candidate_failed_repetitions = replay_result.candidate.metrics.get(
        "failed_repetition_count"
    )
    base_details: dict[str, object] = {
        **coverage,
        "baseline_replay_source": baseline_source,
        "candidate_repetition_count": candidate_repetitions,
        "candidate_successful_repetition_count": candidate_successful_repetitions,
        "candidate_failed_repetition_count": candidate_failed_repetitions,
    }
    causal_failures = aggregate_replay_failures(
        replay_result,
        normalized=normalized,
    )
    invalid_control = any(
        event.owner is FailureOwner.FRAMEWORK
        and event.code
        in {
            "authoritative_replay_invalid_control",
            "trusted_measurement_invalid_control_frontier",
        }
        for event in causal_failures
    )
    zero_comparable_pairs = coverage["comparable_pair_count"] == 0
    if invalid_control or zero_comparable_pairs:
        measurement_event = ReplayFailureEvent(
            code="control_not_comparable",
            owner=FailureOwner.FRAMEWORK,
            stage=FailureStage.EVALUATION,
            scope=FailureScope.SHARED_RUN,
            repairable=True,
            category="measurement_validity",
            summary=(
                "paired replay produced no comparable task-level evidence"
                if zero_comparable_pairs
                else "paired replay control was not comparable"
            ),
            diagnostics={
                "comparable_pair_count": coverage["comparable_pair_count"],
                "incomparable_pair_count": coverage["incomparable_pair_count"],
                "candidate_executed_count": coverage["candidate_executed_count"],
            },
        )
        measurement_payload = measurement_event.to_dict()
        primary_failure = next(
            (
                event
                for event in causal_failures
                if event.stage is not FailureStage.EVALUATION
                and FailureEventSource.NATIVE.value
                in getattr(event, "source_kinds", ())
            ),
            measurement_event,
        )
        primary_payload = primary_failure.to_dict()
        primary_class = (
            "measurement"
            if primary_failure is measurement_event
            else primary_failure.owner.value
        )
        primary_scope = (
            FailureScope.SHARED_RUN
            if primary_failure.owner
            in {FailureOwner.FRAMEWORK, FailureOwner.INFRASTRUCTURE}
            else primary_failure.scope
        )
        primary_next_action = (
            "repair_measurement"
            if primary_failure.owner
            in {FailureOwner.FRAMEWORK, FailureOwner.INFRASTRUCTURE}
            else "repair_candidate"
            if primary_failure.owner is FailureOwner.CANDIDATE
            else "repair_task_completion"
        )
        base_details.update(
            {
                "code": primary_failure.code,
                "failure_class": primary_class,
                "failure_owner": primary_failure.owner.value,
                "failure_scope": primary_scope.value,
                "failure_stage": primary_failure.stage.value,
                "repairable": primary_failure.repairable,
                "next_action": primary_next_action,
                "effect": None,
                "failure_event": primary_payload,
                "derived_failure_event": measurement_payload,
                "causal_failure_events": [
                    *(event.to_dict() for event in causal_failures),
                    measurement_payload,
                ],
                "observed_replay_failure_events": [
                    event.to_dict() for event in causal_failures
                ],
            }
        )
    actionable_incomparable_pair_count = max(
        0,
        int(coverage["incomparable_pair_count"])
        - int(coverage.get("intentionally_unadmitted_member_count", 0)),
    )
    base_details["actionable_incomparable_pair_count"] = (
        actionable_incomparable_pair_count
    )
    if actionable_incomparable_pair_count > 0:
        return GateResult(
            gate_name="replay_confidence",
            passed=False,
            reason="replay comparison contains incomparable member outcomes",
            details=base_details,
        )
    if (
        baseline_source == "historical"
        and isinstance(candidate_repetitions, (int, float))
        and int(candidate_repetitions) <= 1
    ):
        return GateResult(
            gate_name="replay_confidence",
            passed=False,
            reason="fixed historical baseline plus one candidate rerun is limited confidence",
            details={
                **base_details,
                "candidate_repetition_count": int(candidate_repetitions),
            },
        )
    if (
        isinstance(candidate_repetitions, (int, float))
        and int(candidate_repetitions) >= 3
        and isinstance(candidate_successful_repetitions, (int, float))
        and int(candidate_successful_repetitions) < 3
    ):
        candidate_variants = tuple(
            member.candidate for member in normalized.members
        ) or (replay_result.candidate,)
        system_failures = _system_owned_repetition_failures(*candidate_variants)
        if system_failures:
            failure_owner = (
                FailureOwner.INFRASTRUCTURE
                if any(
                    event.owner is FailureOwner.INFRASTRUCTURE
                    for event in system_failures
                )
                else FailureOwner.FRAMEWORK
            )
            failure_scope = (
                FailureScope.SHARED_RUN
                if failure_owner is FailureOwner.INFRASTRUCTURE
                else FailureScope.MEMBER
            )
            event_payloads = [event.to_dict() for event in system_failures]
            return GateResult(
                gate_name="replay_confidence",
                passed=False,
                reason=(
                    "replay confidence is unavailable because system-owned "
                    "repetitions failed"
                ),
                details={
                    **base_details,
                    "candidate_repetition_count": int(candidate_repetitions),
                    "candidate_successful_repetition_count": int(
                        candidate_successful_repetitions
                    ),
                    "candidate_failed_repetition_count": (
                        int(candidate_failed_repetitions)
                        if isinstance(
                            candidate_failed_repetitions,
                            (int, float),
                        )
                        else None
                    ),
                    "failure_class": failure_owner.value,
                    "failure_owner": failure_owner.value,
                    "failure_scope": failure_scope.value,
                    "repairable": any(event.repairable for event in system_failures),
                    "failure_event": event_payloads[0],
                    "causal_failure_events": event_payloads,
                },
            )
        return GateResult(
            gate_name="replay_confidence",
            passed=False,
            reason="candidate replay successful repetitions are insufficient",
            details={
                **base_details,
                "candidate_repetition_count": int(candidate_repetitions),
                "candidate_successful_repetition_count": int(
                    candidate_successful_repetitions
                ),
                "candidate_failed_repetition_count": (
                    int(candidate_failed_repetitions)
                    if isinstance(candidate_failed_repetitions, (int, float))
                    else None
                ),
            },
        )
    return GateResult(
        gate_name="replay_confidence",
        passed=True,
        reason="replay comparison has sufficient confidence for policy",
        details=base_details,
    )

def _replay_stability_gate(
    *,
    baseline_summary: EvaluationSummary,
    candidate_summary: EvaluationSummary,
    min_score_delta: float,
    replay_stability_margin: float,
    replay_used: bool,
) -> GateResult | None:
    if not replay_used or replay_stability_margin <= 0:
        return None
    baseline_score = _metric_number(baseline_summary.metrics, "score")
    candidate_score = _metric_number(candidate_summary.metrics, "score")
    if baseline_score is None or candidate_score is None:
        return GateResult(
            gate_name="replay_stability_margin",
            passed=False,
            reason="score metric missing for replay stability margin",
        )
    delta = candidate_score - baseline_score
    required_delta = min_score_delta + replay_stability_margin
    return GateResult(
        gate_name="replay_stability_margin",
        passed=delta >= required_delta,
        reason=(
            "replay score delta clears stability margin"
            if delta >= required_delta
            else "replay score delta is below stability margin"
        ),
        details={
            "baseline": baseline_score,
            "candidate": candidate_score,
            "delta": round(delta, 10),
            "required_delta": round(required_delta, 10),
            "replay_stability_margin": replay_stability_margin,
        },
    )
