"""Compilation of trusted experiments into immutable MeasurementPlanV2 artifacts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Mapping, Sequence

from aworld.core.tool.replay_policy import EvidencePolicyProfileV2
from aworld.self_evolve.measurement import ControlledExperimentSpec
from aworld.self_evolve.measurement_control import (
    AdaptiveMeasurementPolicy,
    CaseVisibilityRole,
    DeadlinePolicy,
    FeasibilityStatus,
    MeasurementFeasibility,
    MeasurementPlanV2,
    SamplingStage,
    SamplingStageKind,
    estimate_measurement_feasibility,
    validate_measurement_feasibility,
)
from aworld.self_evolve.replay_adaptation import IsolationDecision
from aworld.self_evolve.store import FilesystemSelfEvolveStore


@dataclass(frozen=True)
class MeasurementLatencyEstimate:
    minimum_member_seconds: float | None = None
    p50_member_seconds: float | None = None
    p90_member_seconds: float | None = None
    cold_start_seconds: float = 0.0
    source: str | None = None
    confidence: str | None = None


@dataclass(frozen=True)
class CompiledMeasurementPlan:
    plan: MeasurementPlanV2
    feasibility: MeasurementFeasibility
    excluded_repair_screening_case_ids: tuple[str, ...]


def compile_measurement_plan_v2(
    *,
    experiment: ControlledExperimentSpec,
    dataset_fingerprint: str,
    execution_contract_fingerprint: str,
    isolation_decision: IsolationDecision,
    evidence_policy_profile: EvidencePolicyProfileV2,
    deadlines: DeadlinePolicy,
    case_strata: Mapping[str, str] | None = None,
    repair_screening_case_ids: Sequence[str] = (),
    sentinel_case_count: int | None = None,
    plan_revision: int = 1,
    reusable_work_unit_ids: Sequence[str] = (),
    latency: MeasurementLatencyEstimate | None = None,
) -> CompiledMeasurementPlan:
    """Compile one consistent plan without exposing repair cases to validation.

    Input order does not decide the sentinel panel. Cases are deterministically
    interleaved across declared strata, so the same frozen experiment compiles
    to the same work topology across Campaign cycles.
    """

    control_fingerprint = experiment.control.fingerprint
    candidate_fingerprint = experiment.treatment.fingerprint
    if control_fingerprint is None or candidate_fingerprint is None:
        raise ValueError("measurement plan requires frozen control and candidate artifacts")
    all_primary = tuple(experiment.sampling.independent_case_ids)
    screening = tuple(dict.fromkeys(repair_screening_case_ids))
    unknown_screening = set(screening) - set(all_primary)
    if unknown_screening:
        raise ValueError("repair-screening case is outside the experiment sampling plan")
    transfer_case_ids = tuple(
        dict.fromkeys(
            case_id
            for panel in experiment.transfer_panels
            for case_id in panel.case_ids
        )
    )
    hidden_primary = set(screening) | set(experiment.search_visible_case_ids)
    declared_strata = dict(case_strata or {})
    unknown_strata = set(declared_strata) - set(all_primary)
    if unknown_strata:
        raise ValueError("case strata references cases outside experiment sampling")
    authoritative_primary = tuple(
        case_id
        for case_id in all_primary
        if case_id not in hidden_primary and case_id not in transfer_case_ids
    )
    minimum_cases = experiment.outcomes.minimum_independent_cases
    if len(authoritative_primary) < minimum_cases:
        raise ValueError(
            "positive conclusion is unreachable after excluding candidate-influencing cases"
        )
    if sentinel_case_count is None:
        requested_sentinel = min(3, len(authoritative_primary))
    elif (
        isinstance(sentinel_case_count, bool)
        or not isinstance(sentinel_case_count, int)
        or sentinel_case_count <= 0
    ):
        raise ValueError("sentinel_case_count must be positive")
    else:
        requested_sentinel = sentinel_case_count
    sentinel_count = min(
        len(authoritative_primary), max(minimum_cases, requested_sentinel)
    )
    ordered_primary = _stratified_case_order(
        authoritative_primary,
        case_strata={
            case_id: declared_strata[case_id]
            for case_id in authoritative_primary
            if case_id in declared_strata
        },
        salt=dataset_fingerprint,
    )
    sentinel_ids = ordered_primary[:sentinel_count]
    expansion_ids = ordered_primary[sentinel_count:]
    stages: list[SamplingStage] = [
        SamplingStage(
            stage_id="sentinel",
            kind=SamplingStageKind.SENTINEL,
            case_ids=sentinel_ids,
            minimum_case_count=minimum_cases,
            batch_size=min(2, len(sentinel_ids)),
            visibility_role=CaseVisibilityRole.AUTHORITATIVE_VALIDATION,
        )
    ]
    if expansion_ids:
        stages.append(
            SamplingStage(
                stage_id="expansion",
                kind=SamplingStageKind.EXPANSION,
                case_ids=expansion_ids,
                minimum_case_count=0,
                batch_size=min(2, len(expansion_ids)),
                optional=True,
                visibility_role=CaseVisibilityRole.AUTHORITATIVE_VALIDATION,
            )
        )
    if transfer_case_ids:
        stages.append(
            SamplingStage(
                stage_id="regression-transfer",
                kind=SamplingStageKind.REGRESSION_TRANSFER,
                case_ids=tuple(sorted(transfer_case_ids)),
                minimum_case_count=len(transfer_case_ids),
                batch_size=min(2, len(transfer_case_ids)),
                requires_positive_effect=True,
                visibility_role=CaseVisibilityRole.REGRESSION_TRANSFER,
            )
        )
    decision_policy = AdaptiveMeasurementPolicy(
        minimum_effect=experiment.outcomes.minimum_effect,
        minimum_independent_cases=minimum_cases,
        maximum_invalid_controls=max(1, experiment.stopping_policy.invalid_control_patience),
        zero_yield_window=max(1, experiment.stopping_policy.zero_yield_patience),
        require_regression_transfer=any(
            panel.required for panel in experiment.transfer_panels
        ),
        futility_enabled=True,
    )
    plan = MeasurementPlanV2.create(
        experiment_id=experiment.experiment_id,
        plan_revision=plan_revision,
        candidate_fingerprint=candidate_fingerprint,
        control_fingerprint=control_fingerprint,
        dataset_fingerprint=dataset_fingerprint,
        execution_contract_fingerprint=execution_contract_fingerprint,
        isolation_decision=isolation_decision,
        evidence_policy_profile=evidence_policy_profile,
        stages=tuple(stages),
        repetitions_per_case=experiment.sampling.repetitions_per_case,
        deadlines=deadlines,
        decision_policy=decision_policy,
        estimator_version="paired-controlled-experiment-v2",
        decision_policy_version="adaptive-staged-v2",
    )
    estimate = latency or MeasurementLatencyEstimate()
    feasibility = estimate_measurement_feasibility(
        plan,
        reusable_work_unit_ids=reusable_work_unit_ids,
        minimum_member_seconds=estimate.minimum_member_seconds,
        p50_member_seconds=estimate.p50_member_seconds,
        p90_member_seconds=estimate.p90_member_seconds,
        cold_start_seconds=estimate.cold_start_seconds,
        estimate_source=estimate.source,
        estimate_confidence=estimate.confidence,
    )
    validate_measurement_feasibility(plan, feasibility)
    return CompiledMeasurementPlan(
        plan=plan,
        feasibility=feasibility,
        excluded_repair_screening_case_ids=screening,
    )


def persist_compiled_measurement_plan(
    store: FilesystemSelfEvolveStore,
    *,
    run_id: str,
    compiled: CompiledMeasurementPlan,
    isolation_decision: IsolationDecision,
    evidence_policy_profile: EvidencePolicyProfileV2,
) -> None:
    if compiled.plan.isolation_decision_fingerprint != isolation_decision.fingerprint:
        raise ValueError("persisted isolation decision differs from compiled plan")
    if compiled.plan.evidence_policy_fingerprint != evidence_policy_profile.fingerprint:
        raise ValueError("persisted evidence policy differs from compiled plan")
    store.write_measurement_control_plan(
        run_id,
        compiled.plan,
        isolation_decision=isolation_decision,
        evidence_policy_profile=evidence_policy_profile,
    )


def measurement_preflight_projection(
    *,
    plan: MeasurementPlanV2,
    feasibility: MeasurementFeasibility,
    isolation_decision: IsolationDecision,
) -> dict[str, object]:
    """Return the bounded operator contract shown before expensive rollout."""

    if plan.isolation_decision_fingerprint != isolation_decision.fingerprint:
        raise ValueError("preflight isolation decision differs from plan")
    if feasibility.total_work_units != len(plan.work_units):
        raise ValueError("preflight work-unit count differs from plan")
    fallback = isolation_decision.fallback
    return {
        "schema_version": "aworld.self_evolve.measurement_preflight.v2",
        "measurement_plan_fingerprint": plan.measurement_plan_fingerprint,
        "planned_work_units": feasibility.total_work_units,
        "reused_work_units": feasibility.reused_work_units,
        "pending_work_units": feasibility.pending_work_units,
        "decision_required_work_units": feasibility.decision_required_work_units,
        "sampling_stages": [
            {
                "stage_id": stage.stage_id,
                "kind": stage.kind.value,
                "case_count": len(stage.case_ids),
                "minimum_case_count": stage.minimum_case_count,
                "batch_size": stage.batch_size,
                "optional": stage.optional,
            }
            for stage in plan.stages
        ],
        "safe_lane_count": feasibility.safe_lane_count,
        "isolation_fallback": (
            {
                "code": fallback.code,
                "limiting_resource": fallback.limiting_resource,
                "detail": fallback.detail,
            }
            if fallback is not None
            else None
        ),
        "minimum_feasible_wall_seconds": feasibility.minimum_feasible_wall_seconds,
        "p50_time_to_decision_seconds": feasibility.p50_time_to_decision_seconds,
        "p90_time_to_decision_seconds": feasibility.p90_time_to_decision_seconds,
        "expected_checkpoint_quanta": feasibility.expected_checkpoint_quanta,
        "estimate_source": feasibility.estimate_source,
        "estimate_confidence": feasibility.estimate_confidence,
        "feasibility_status": feasibility.status.value,
        "feasibility_reason_code": feasibility.reason_code,
        "stopping_policy": {
            "minimum_effect": plan.decision_policy.minimum_effect,
            "minimum_independent_cases": (
                plan.decision_policy.minimum_independent_cases
            ),
            "maximum_invalid_controls": (
                plan.decision_policy.maximum_invalid_controls
            ),
            "zero_yield_window": plan.decision_policy.zero_yield_window,
            "require_regression_transfer": (
                plan.decision_policy.require_regression_transfer
            ),
            "futility_enabled": plan.decision_policy.futility_enabled,
        },
    }


def _stratified_case_order(
    case_ids: Sequence[str],
    *,
    case_strata: Mapping[str, str],
    salt: str,
) -> tuple[str, ...]:
    unknown = set(case_strata) - set(case_ids)
    if unknown:
        raise ValueError("case strata references cases outside authoritative sampling")
    groups: dict[str, list[str]] = {}
    for case_id in case_ids:
        stratum = case_strata.get(case_id, "default")
        if not isinstance(stratum, str) or not stratum.strip():
            raise ValueError("case stratum must be non-empty")
        groups.setdefault(stratum, []).append(case_id)
    for values in groups.values():
        values.sort(
            key=lambda case_id: hashlib.sha256(
                f"{salt}\0{case_id}".encode()
            ).hexdigest()
        )
    ordered: list[str] = []
    strata = sorted(groups)
    while any(groups.values()):
        for stratum in strata:
            if groups[stratum]:
                ordered.append(groups[stratum].pop(0))
    return tuple(ordered)
