"""Pure policy projections shared by self-evolve orchestration surfaces.

This module keeps release-mode selection, gate ownership, replay repetition
selection, and Campaign measurement projection out of the stateful runner.  The
functions are intentionally side-effect free so policy changes can be tested
without constructing ``SelfEvolveRunner``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace

from aworld.self_evolve.failure_events import (
    FailureOwner,
    FailureScope,
    FailureStage,
    ReplayFailureEvent,
)
from aworld.self_evolve.measurement import (
    ControlledExperimentSpec,
    MeasurementPolicyMode,
)
from aworld.self_evolve.replay import CandidateReplayResult
from aworld.self_evolve.types import GateResult, SelfEvolveRunStatus


VERIFIED_APPLY_POLICIES = frozenset({"auto_verified", "verified_only"})

CANDIDATE_REPAIRABLE_GATE_STAGES = {
    "candidate_package": FailureStage.CANDIDATE_GENERATION,
    "skill_markdown": FailureStage.CANDIDATE_GENERATION,
    "skill_release_fidelity": FailureStage.CANDIDATE_GENERATION,
    "score_improvement": FailureStage.EVALUATION,
    "cost_latency_regression": FailureStage.EVALUATION,
    "replay_stability_margin": FailureStage.EVALUATION,
    "evidence_quality": FailureStage.EVALUATION,
    "replay_confidence": FailureStage.TASK_ROLLOUT,
    "replay_evaluator_admission": FailureStage.TASK_ROLLOUT,
    "target_behavior_delta": FailureStage.CANDIDATE_GENERATION,
}

FRAMEWORK_SHARED_GATE_STAGES = {
    "challenger_admission": FailureStage.EVALUATION,
    "handbook_locator_integrity": FailureStage.CANDIDATE_GENERATION,
    "evaluation_runtime_health": FailureStage.EVALUATION,
    "held_out_verification": FailureStage.EVALUATION,
    "judge_only_signal": FailureStage.EVALUATION,
    "global_regression_benchmark": FailureStage.EVALUATION,
}


def is_verified_apply_policy(apply_policy: str) -> bool:
    return apply_policy in VERIFIED_APPLY_POLICIES


def effective_replay_repetitions(
    *,
    apply_policy: str,
    repetitions_explicit: bool,
    replay_case_count: int,
    measurement_min_independent_cases: int,
    baseline_repetitions: int,
    candidate_repetitions: int,
) -> tuple[int, int, str]:
    """Resolve one replay repetition policy for planning and execution."""

    if (
        is_verified_apply_policy(apply_policy)
        and not repetitions_explicit
        and replay_case_count >= max(2, measurement_min_independent_cases)
    ):
        return 1, 1, "independent_case_adaptive"
    return baseline_repetitions, candidate_repetitions, "configured"


def effective_cli_measurement_mode(
    configured_mode: MeasurementPolicyMode | str | None,
    *,
    apply_policy: str,
    replay_enabled: bool,
) -> MeasurementPolicyMode:
    """Collect verified replay evidence in shadow mode unless explicitly selected.

    An omitted mode is distinct from an explicit ``off``. A verified skill replay
    must not silently retain the legacy batch executor, but uncalibrated
    measurement thresholds must not become release authority. Operators can
    explicitly select ``required`` after calibration or ``off`` for compatibility.
    """

    if (
        configured_mode is None
        and replay_enabled
        and is_verified_apply_policy(apply_policy)
    ):
        return MeasurementPolicyMode.SHADOW
    return MeasurementPolicyMode(configured_mode or MeasurementPolicyMode.OFF)


def rebase_measurement_experiment_for_materialization(
    experiment: ControlledExperimentSpec,
    *,
    run_id: str,
) -> ControlledExperimentSpec:
    """Give resumed results a cycle-local immutable observation namespace."""

    if experiment.run_id == run_id:
        return experiment
    rebased = ControlledExperimentSpec.create(
        run_id=run_id,
        mode=experiment.mode,
        swap_axis=experiment.swap_axis,
        changed_axes=experiment.changed_axes,
        control=experiment.control,
        treatment=experiment.treatment,
        frozen_identities=experiment.frozen_identities,
        sampling=experiment.sampling,
        outcomes=experiment.outcomes,
        budgets=experiment.budgets,
        transfer_panels=experiment.transfer_panels,
        search_visible_case_ids=experiment.search_visible_case_ids,
        selection_protocol=experiment.selection_protocol,
        stopping_policy=experiment.stopping_policy,
        created_at=experiment.created_at,
    )
    return replace(rebased, extensions=dict(experiment.extensions))


def gate_has_candidate_owned_repair(gate: GateResult) -> bool:
    """Return whether a failed gate authorizes another candidate repair."""

    details = gate.details if isinstance(gate.details, Mapping) else {}
    causal_events = details.get("causal_failure_events")
    if isinstance(causal_events, (list, tuple)):
        typed_events = [
            item for item in causal_events if isinstance(item, Mapping)
        ]
        if typed_events:
            return any(
                item.get("owner") == FailureOwner.CANDIDATE.value
                and item.get("repairable") is True
                for item in typed_events
            )
    failure_owner = details.get("failure_owner") or details.get("failure_class")
    if isinstance(failure_owner, str) and failure_owner:
        return (
            failure_owner == FailureOwner.CANDIDATE.value
            and details.get("repairable") is not False
        )
    return gate.gate_name in {
        *CANDIDATE_REPAIRABLE_GATE_STAGES,
        "candidate_repair_conformance",
        "candidate_replay",
        "required_verification",
    }


def campaign_measurement_outcome_for_replay(
    replay_result: CandidateReplayResult,
    *,
    final_status: SelfEvolveRunStatus,
    gate_results: Iterable[GateResult] = (),
) -> dict[str, object] | None:
    """Project the authoritative scheduler stop into Campaign causal state."""

    decision = replay_result.measurement_decision
    if not isinstance(decision, Mapping):
        return None
    kind = str(decision.get("kind") or "")
    from aworld.self_evolve.campaign import (
        CampaignMeasurementOutcomeV2,
        CandidateImprovementOutcome,
        MeasurementExecutionStatus,
    )

    retryable_framework_member_failure = any(
        event.owner in {FailureOwner.FRAMEWORK, FailureOwner.INFRASTRUCTURE}
        and event.scope is FailureScope.MEMBER
        and event.repairable
        and (
            event.code == "replay_member_phase_timeout"
            or event.stage is FailureStage.EVIDENCE_FINALIZATION
        )
        for member in (replay_result.member_results or ())
        for variant in (member.baseline, member.candidate)
        for event in (variant.failure, *variant.blocked_by)
        if isinstance(event, ReplayFailureEvent)
    )
    if retryable_framework_member_failure:
        outcome = CampaignMeasurementOutcomeV2(
            execution_status=MeasurementExecutionStatus.INVALID,
            improvement_outcome=CandidateImprovementOutcome.UNKNOWN,
            release_gates_passed=False,
            continuation_available=True,
            reason_code="measurement_infrastructure_retry",
        )
    elif kind == "stop_framework_blocked":
        outcome = CampaignMeasurementOutcomeV2(
            execution_status=MeasurementExecutionStatus.FRAMEWORK_BLOCKED,
            improvement_outcome=CandidateImprovementOutcome.UNKNOWN,
            release_gates_passed=False,
            continuation_available=True,
            reason_code=str(
                decision.get("reason_code") or "measurement_framework_blocked"
            ),
        )
    elif kind in {
        "measurement_incomplete_checkpoint",
        "measurement_incomplete_campaign_deadline",
    }:
        outcome = CampaignMeasurementOutcomeV2(
            execution_status=MeasurementExecutionStatus.CHECKPOINTED,
            improvement_outcome=CandidateImprovementOutcome.UNKNOWN,
            release_gates_passed=False,
            continuation_available=decision.get("resume_safe") is True,
            reason_code=kind,
        )
    elif kind == "stop_confident_positive":
        release_gates_passed = final_status is SelfEvolveRunStatus.SUCCEEDED
        outcome = CampaignMeasurementOutcomeV2(
            execution_status=MeasurementExecutionStatus.COMPLETED,
            improvement_outcome=CandidateImprovementOutcome.POSITIVE,
            release_gates_passed=release_gates_passed,
            continuation_available=not release_gates_passed,
            reason_code=(
                "verified_positive_effect"
                if release_gates_passed
                else "positive_effect_release_gates_failed"
            ),
        )
    elif kind in {"stop_negative", "stop_regression"}:
        outcome = CampaignMeasurementOutcomeV2(
            execution_status=MeasurementExecutionStatus.COMPLETED,
            improvement_outcome=CandidateImprovementOutcome.REGRESSION,
            release_gates_passed=False,
            continuation_available=False,
            reason_code=(
                "decisive_regression"
                if kind == "stop_regression"
                else "decisive_negative_effect"
            ),
        )
    elif kind == "stop_invalid_control":
        outcome = CampaignMeasurementOutcomeV2(
            execution_status=MeasurementExecutionStatus.INVALID,
            improvement_outcome=CandidateImprovementOutcome.UNKNOWN,
            release_gates_passed=False,
            continuation_available=True,
            reason_code="repeated_invalid_control",
        )
    else:
        candidate_repair_available = any(
            not gate.passed and gate_has_candidate_owned_repair(gate)
            for gate in gate_results
        )
        outcome = CampaignMeasurementOutcomeV2(
            execution_status=MeasurementExecutionStatus.COMPLETED,
            improvement_outcome=CandidateImprovementOutcome.NO_EFFECT,
            release_gates_passed=False,
            continuation_available=candidate_repair_available,
            reason_code=(
                "no_effect_candidate_repair_available"
                if candidate_repair_available
                else kind.removeprefix("stop_") + "_effect"
                if kind.startswith("stop_")
                else "measurement_no_effect"
            ),
        )
    return outcome.to_dict()
