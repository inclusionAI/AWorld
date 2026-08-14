from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from aworld.core.tool.replay_policy import (
    ArtifactPolicy,
    EvidencePolicyProfileV2,
    compile_evidence_policy_profile_v2,
)
from aworld.self_evolve.measurement_control import (
    AdaptiveDecision,
    AdaptiveDecisionKind,
    AdaptiveMeasurementPolicy,
    CaseVisibilityRole,
    DeadlinePolicy,
    FeasibilityStatus,
    IsolationRequirement,
    IsolationSummary,
    MeasurementArm,
    MeasurementPlanV2,
    MeasurementProgressSummary,
    MeasurementWorkUnitV1,
    SamplingStage,
    SamplingStageKind,
    classify_plan_amendment,
    decide_staged_measurement,
    estimate_measurement_feasibility,
    stable_control_fingerprint,
    validate_measurement_feasibility,
)
from aworld.self_evolve.replay_adaptation import (
    IsolationDecision,
    IsolationExclusiveFallback,
    IsolationGrant,
    ReplayIsolationTopology,
)


def _fp(label: str) -> str:
    return stable_control_fingerprint({"label": label})


def _evidence_profile(label: str = "default") -> EvidencePolicyProfileV2:
    return compile_evidence_policy_profile_v2(
        artifact_policies=(
            ArtifactPolicy(
                artifact_type=f"browser.snapshot.{label}",
                registered_producers=("browser.snapshotter",),
                max_files=2,
                max_items=4,
                max_bytes=1_000_000,
            ),
        )
    )


def _isolation_decision(label: str = "default") -> IsolationDecision:
    grants = []
    for lane in ("a", "b"):
        topology = ReplayIsolationTopology.create(
            materializer_id="test.materializer",
            materializer_fingerprint=_fp("materializer"),
            workspace_identity=f"workspace:{label}:{lane}",
            runtime_identity=f"runtime:{label}:{lane}",
            browser_profile_identity=f"browser:{label}:{lane}",
            endpoint_namespace_identity=f"endpoint:{label}:{lane}",
            evidence_directory_identity=f"evidence:{label}:{lane}",
            cleanup_owner=f"cleanup:{label}:{lane}",
        )
        grants.append(IsolationGrant.create(topology=topology, binding_fingerprints=()))
    return IsolationDecision.create(requested_lane_count=2, grants=grants)


def _zero_grant_decision() -> IsolationDecision:
    return IsolationDecision.exclusive_fallback(
        requested_lane_count=2,
        fallback=IsolationExclusiveFallback(
            code="binding_requires_exclusive",
            limiting_resource="browser:shared",
            detail="fixture has no isolated browser profile",
        ),
    )


def _stages() -> tuple[SamplingStage, ...]:
    return (
        SamplingStage(
            stage_id="sentinel",
            kind=SamplingStageKind.SENTINEL,
            case_ids=("case-1", "case-2"),
            minimum_case_count=2,
            batch_size=2,
        ),
        SamplingStage(
            stage_id="expansion",
            kind=SamplingStageKind.EXPANSION,
            case_ids=("case-3", "case-4"),
            minimum_case_count=0,
            batch_size=1,
            optional=True,
        ),
        SamplingStage(
            stage_id="regression",
            kind=SamplingStageKind.REGRESSION_TRANSFER,
            case_ids=("case-r1",),
            minimum_case_count=1,
            batch_size=1,
            requires_positive_effect=True,
            visibility_role=CaseVisibilityRole.REGRESSION_TRANSFER,
        ),
    )


def _plan(
    *,
    control_fingerprint: str | None = None,
    candidate_fingerprint: str | None = None,
    evidence_policy_profile: EvidencePolicyProfileV2 | None = None,
    execution_contract_fingerprint: str | None = None,
    deadlines: DeadlinePolicy | None = None,
    revision: int = 1,
    policy: AdaptiveMeasurementPolicy | None = None,
    isolation_decision: IsolationDecision | None = None,
    stages: tuple[SamplingStage, ...] | None = None,
) -> MeasurementPlanV2:
    return MeasurementPlanV2.create(
        experiment_id="experiment-123",
        plan_revision=revision,
        candidate_fingerprint=candidate_fingerprint or _fp("candidate"),
        control_fingerprint=control_fingerprint or _fp("control"),
        dataset_fingerprint=_fp("dataset"),
        execution_contract_fingerprint=(
            execution_contract_fingerprint or _fp("execution")
        ),
        isolation_decision=isolation_decision or _isolation_decision(),
        evidence_policy_profile=evidence_policy_profile or _evidence_profile(),
        stages=stages or _stages(),
        repetitions_per_case=1,
        deadlines=deadlines or DeadlinePolicy(
            attempt_timeout_seconds=30,
            member_hard_deadline_seconds=600,
            checkpoint_quantum_seconds=900,
        ),
        decision_policy=policy
        or AdaptiveMeasurementPolicy(
            minimum_effect=0.1,
            minimum_independent_cases=2,
            maximum_invalid_controls=2,
            zero_yield_window=2,
            require_regression_transfer=True,
        ),
        estimator_version="paired-estimator-v1",
        decision_policy_version="decision-policy-v1",
    )


def _progress(**overrides: object) -> MeasurementProgressSummary:
    values: dict[str, object] = {
        "current_stage_id": "sentinel",
        "completed_case_ids": ("case-1", "case-2"),
        "comparable_case_ids": ("case-1",),
        "invalid_control_case_ids": (),
        "confidence_lower_bound": None,
        "point_estimate": None,
        "regression_detected": False,
        "negative_effect_detected": False,
        "futility_proven": False,
        "new_comparable_pairs_in_window": 1,
        "uncertainty_reduction_in_window": 0.1,
        "current_stage_exhausted": True,
        "completed_stage_ids": ("sentinel",),
        "checkpoint_quantum_expired": False,
        "campaign_wall_deadline_expired": False,
        "resume_safe": True,
    }
    values.update(overrides)
    return MeasurementProgressSummary(**values)


def test_plan_round_trip_is_frozen_and_has_stable_canonical_fingerprint() -> None:
    plan = _plan()

    loaded = MeasurementPlanV2.from_dict(
        plan.to_dict(),
        isolation_decision=_isolation_decision(),
        evidence_policy_profile=_evidence_profile(),
    )
    independently_created = _plan()

    assert loaded == plan
    assert independently_created.measurement_plan_fingerprint == (
        plan.measurement_plan_fingerprint
    )
    assert all(
        unit.measurement_plan_fingerprint == plan.measurement_plan_fingerprint
        for unit in plan.work_units
    )
    with pytest.raises(FrozenInstanceError):
        plan.plan_revision = 9  # type: ignore[misc]


def test_plan_rejects_bare_fingerprints_and_zero_grant_lane_elevation() -> None:
    plan = _plan(isolation_decision=_zero_grant_decision())
    assert plan.isolation_summary.safe_lane_count == 1
    assert plan.isolation_summary.isolation_proven is False

    payload = plan.to_dict()
    payload["evidence_policy_fingerprint"] = _fp("caller-supplied-policy")
    with pytest.raises(ValueError, match="fingerprint|artifact drifted"):
        MeasurementPlanV2.from_dict(
            payload,
            isolation_decision=_zero_grant_decision(),
            evidence_policy_profile=_evidence_profile(),
        )

    payload = plan.to_dict()
    payload["isolation_summary"]["safe_lane_count"] = 2  # type: ignore[index]
    payload["isolation_summary"]["isolation_proven"] = True  # type: ignore[index]
    payload["isolation_summary"]["isolation_grant_fingerprint"] = _fp("fake")  # type: ignore[index]
    with pytest.raises(ValueError, match="isolation summary"):
        MeasurementPlanV2.from_dict(
            payload,
            isolation_decision=_zero_grant_decision(),
            evidence_policy_profile=_evidence_profile(),
        )


def test_work_unit_identity_survives_restart_and_drift_changes_key() -> None:
    plan = _plan()
    unit = plan.work_units[0]

    assert MeasurementWorkUnitV1.from_dict(unit.to_dict()) == unit
    assert unit.work_unit_id == plan.work_units[0].work_unit_id

    drifted = MeasurementWorkUnitV1.create(
        measurement_plan_fingerprint=plan.measurement_plan_fingerprint,
        experiment_id=unit.experiment_id,
        artifact_fingerprint=unit.artifact_fingerprint,
        pairing_control_fingerprint=unit.pairing_control_fingerprint,
        dataset_fingerprint=unit.dataset_fingerprint,
        case_id=unit.case_id,
        arm=unit.arm,
        repetition_id=unit.repetition_id,
        execution_contract_fingerprint=unit.execution_contract_fingerprint,
        evidence_policy_fingerprint=_fp("new-policy"),
        sampling_contract_fingerprint=unit.sampling_contract_fingerprint,
        isolation_decision_fingerprint=unit.isolation_decision_fingerprint,
        stage_id=unit.stage_id,
        depends_on_work_unit_id=unit.depends_on_work_unit_id,
    )
    assert drifted.work_unit_id != unit.work_unit_id


def test_treatment_units_depend_on_matching_control() -> None:
    plan = _plan()
    by_id = {unit.work_unit_id: unit for unit in plan.work_units}

    for treatment in (
        unit for unit in plan.work_units if unit.arm is MeasurementArm.TREATMENT
    ):
        control = by_id[treatment.depends_on_work_unit_id]
        assert control.arm is MeasurementArm.CONTROL
        assert control.case_id == treatment.case_id
        assert control.repetition_id == treatment.repetition_id


def test_deadline_policy_has_no_hidden_campaign_deadline() -> None:
    deadlines = DeadlinePolicy(
        attempt_timeout_seconds=30,
        member_hard_deadline_seconds=600,
        checkpoint_quantum_seconds=900,
    )

    assert deadlines.campaign_wall_deadline_seconds is None
    assert deadlines.to_dict()["campaign_wall_deadline_seconds"] is None


def test_deadline_layers_reject_attempt_larger_than_member() -> None:
    with pytest.raises(ValueError, match="attempt timeout"):
        DeadlinePolicy(
            attempt_timeout_seconds=601,
            member_hard_deadline_seconds=600,
            checkpoint_quantum_seconds=900,
        )


def test_isolation_summary_falls_back_deterministically() -> None:
    requirement = IsolationRequirement(
        requested_lane_ceiling=2,
        resource_dimensions=("workspace_root", "browser_profile"),
    )

    summary = IsolationSummary.exclusive_fallback(
        requirement=requirement,
        reason="browser_profile_shared",
    )

    assert summary.safe_lane_count == 1
    assert summary.isolation_proven is False
    assert summary.limiting_reason == "browser_profile_shared"


def test_multiple_lanes_require_a_verified_grant() -> None:
    with pytest.raises(ValueError, match="isolation grant"):
        IsolationSummary(
            requested_lane_ceiling=2,
            safe_lane_count=2,
            isolation_proven=True,
            isolation_grant_fingerprint=None,
        )


def test_feasibility_reports_work_counts_reuse_and_lane_schedule() -> None:
    plan = _plan()
    reused = tuple(unit.work_unit_id for unit in plan.work_units[:2])

    result = estimate_measurement_feasibility(
        plan,
        reusable_work_unit_ids=reused,
        minimum_member_seconds=20,
        p50_member_seconds=40,
        p90_member_seconds=80,
        cold_start_seconds=10,
        estimate_source="compatible_history",
        estimate_confidence="high",
    )

    assert result.status is FeasibilityStatus.FEASIBLE
    assert result.total_work_units == 10
    assert result.reused_work_units == 2
    assert result.pending_work_units == 8
    assert result.decision_required_work_units == 4
    assert result.safe_lane_count == 2
    assert result.minimum_feasible_wall_seconds == 50
    assert result.p50_time_to_decision_seconds == 90
    assert result.p90_time_to_decision_seconds == 170


def test_feasibility_rejects_unknown_reuse_unit() -> None:
    with pytest.raises(ValueError, match="unknown work units"):
        estimate_measurement_feasibility(
            _plan(), reusable_work_unit_ids=("measurement-unit-unknown",)
        )


def test_infeasible_explicit_deadline_fails_before_rollout() -> None:
    plan = _plan(
        deadlines=DeadlinePolicy(
            attempt_timeout_seconds=10,
            member_hard_deadline_seconds=100,
            checkpoint_quantum_seconds=30,
            campaign_wall_deadline_seconds=25,
        )
    )
    result = estimate_measurement_feasibility(
        plan,
        minimum_member_seconds=20,
        p50_member_seconds=40,
        p90_member_seconds=80,
        cold_start_seconds=10,
    )

    assert result.status is FeasibilityStatus.INFEASIBLE_DEADLINE
    with pytest.raises(ValueError, match="explicit Campaign deadline"):
        validate_measurement_feasibility(plan, result)


def test_chunked_plan_accepts_deadline_below_completion_bound() -> None:
    plan = _plan(
        deadlines=DeadlinePolicy(
            attempt_timeout_seconds=10,
            member_hard_deadline_seconds=100,
            checkpoint_quantum_seconds=20,
            campaign_wall_deadline_seconds=25,
            resumable_chunked=True,
        )
    )
    result = estimate_measurement_feasibility(
        plan,
        minimum_member_seconds=20,
        p50_member_seconds=40,
        p90_member_seconds=80,
        cold_start_seconds=10,
    )

    assert result.status is FeasibilityStatus.RESUMABLE_CHUNKED
    validate_measurement_feasibility(plan, result)


def test_cold_feasibility_uses_declared_member_bound() -> None:
    result = estimate_measurement_feasibility(_plan())

    assert result.estimate_source == "declared_member_hard_deadline"
    assert result.estimate_confidence == "low"
    assert result.p50_time_to_decision_seconds == 1200
    assert result.p90_time_to_decision_seconds == 1200


def test_plan_amendment_reuses_units_when_only_deadline_or_threshold_changes() -> None:
    old = _plan()
    amended = _plan(
        revision=2,
        deadlines=replace(old.deadlines, checkpoint_quantum_seconds=300),
        policy=replace(old.decision_policy, minimum_effect=0.15),
    )

    compatibility = classify_plan_amendment(old, amended)

    assert compatibility.compatible is True
    assert set(compatibility.reusable_work_unit_ids) == {
        unit.work_unit_id for unit in old.work_units
    }
    assert compatibility.changed_fields == ("deadlines", "decision_policy")


def test_plan_amendment_fails_closed_on_evidence_policy_drift() -> None:
    old = _plan()
    amended = _plan(revision=2, evidence_policy_profile=_evidence_profile("changed"))

    compatibility = classify_plan_amendment(old, amended)

    assert compatibility.compatible is False
    assert compatibility.reusable_work_unit_ids == ()
    assert compatibility.reason_codes == ("evidence_policy_fingerprint_changed",)


def test_plan_amendment_fails_closed_on_control_identity_drift() -> None:
    old = _plan()
    amended = _plan(revision=2, control_fingerprint=_fp("new-control"))

    compatibility = classify_plan_amendment(old, amended)

    assert compatibility.compatible is False
    assert compatibility.reusable_work_unit_ids == ()
    assert compatibility.reason_codes == (
        "artifact_fingerprint_changed",
        "pairing_control_fingerprint_changed",
    )


def test_candidate_amendment_reuses_only_arm_specific_controls() -> None:
    old = _plan()
    amended = _plan(revision=2, candidate_fingerprint=_fp("candidate-v2"))

    compatibility = classify_plan_amendment(old, amended)
    old_controls = {
        unit.work_unit_id
        for unit in old.work_units
        if unit.arm is MeasurementArm.CONTROL
    }

    assert compatibility.compatible is True
    assert set(compatibility.reusable_work_unit_ids) == old_controls
    assert compatibility.reason_codes == ("artifact_fingerprint_changed",)


def test_visibility_amendment_invalidates_units_from_the_changed_stage() -> None:
    old = _plan()
    changed_sentinel = replace(
        old.stages[0], visibility_role=CaseVisibilityRole.REPAIR_SCREENING
    )
    amended = _plan(
        revision=2,
        stages=(changed_sentinel, *old.stages[1:]),
    )

    compatibility = classify_plan_amendment(old, amended)
    changed_ids = {
        unit.work_unit_id for unit in old.work_units if unit.stage_id == "sentinel"
    }

    assert changed_ids.isdisjoint(compatibility.reusable_work_unit_ids)
    assert "sampling_contract_fingerprint_changed" in compatibility.reason_codes


def test_isolation_decision_amendment_invalidates_all_work_units() -> None:
    old = _plan()
    amended = _plan(
        revision=2,
        isolation_decision=_isolation_decision("changed"),
    )

    compatibility = classify_plan_amendment(old, amended)

    assert compatibility.compatible is False
    assert compatibility.reusable_work_unit_ids == ()
    assert compatibility.reason_codes == (
        "isolation_decision_fingerprint_changed",
    )


@pytest.mark.parametrize(
    ("path", "bad_value", "field_name"),
    (
        (("deadlines", "resumable_chunked"), "false", "resumable_chunked"),
        (("stages", 0, "optional"), 1, "optional"),
        (("isolation_summary", "isolation_proven"), "true", "isolation_proven"),
        (
            ("decision_policy", "futility_enabled"),
            0,
            "futility_enabled",
        ),
    ),
)
def test_plan_reader_rejects_non_boolean_persisted_values(
    path: tuple[object, ...], bad_value: object, field_name: str
) -> None:
    payload = _plan().to_dict()
    target: object = payload
    for part in path[:-1]:
        target = target[part]  # type: ignore[index]
    target[path[-1]] = bad_value  # type: ignore[index]

    with pytest.raises(ValueError, match=field_name):
        MeasurementPlanV2.from_dict(
            payload,
            isolation_decision=_isolation_decision(),
            evidence_policy_profile=_evidence_profile(),
        )


@pytest.mark.parametrize(
    ("progress", "expected"),
    (
        (
            _progress(regression_detected=True),
            AdaptiveDecisionKind.STOP_REGRESSION,
        ),
        (
            _progress(negative_effect_detected=True),
            AdaptiveDecisionKind.STOP_NEGATIVE,
        ),
        (
            _progress(futility_proven=True),
            AdaptiveDecisionKind.STOP_FUTILITY,
        ),
        (
            _progress(invalid_control_case_ids=("case-1", "case-2")),
            AdaptiveDecisionKind.STOP_INVALID_CONTROL,
        ),
        (
            _progress(
                new_comparable_pairs_in_window=0,
                uncertainty_reduction_in_window=0,
            ),
            AdaptiveDecisionKind.STOP_ZERO_YIELD,
        ),
        (
            _progress(checkpoint_quantum_expired=True),
            AdaptiveDecisionKind.MEASUREMENT_INCOMPLETE_CHECKPOINT,
        ),
        (
            _progress(campaign_wall_deadline_expired=True),
            AdaptiveDecisionKind.MEASUREMENT_INCOMPLETE_CAMPAIGN_DEADLINE,
        ),
    ),
)
def test_staged_decision_typed_stops(
    progress: MeasurementProgressSummary,
    expected: AdaptiveDecisionKind,
) -> None:
    assert decide_staged_measurement(_plan(), progress).kind is expected


def test_positive_sentinel_admits_required_regression_stage() -> None:
    progress = _progress(
        comparable_case_ids=("case-1", "case-2"),
        confidence_lower_bound=0.2,
    )

    decision = decide_staged_measurement(_plan(), progress)

    assert decision == AdaptiveDecision(
        kind=AdaptiveDecisionKind.ADMIT_REQUIRED_REGRESSION_TRANSFER,
        reason_code="positive_effect_requires_independent_regression_transfer",
        next_stage_id="regression",
        admit_case_ids=("case-r1",),
        resume_safe=False,
    )


def test_positive_after_regression_stops_with_confidence() -> None:
    progress = _progress(
        current_stage_id="regression",
        completed_case_ids=("case-1", "case-2", "case-r1"),
        comparable_case_ids=("case-1", "case-2", "case-r1"),
        confidence_lower_bound=0.2,
        completed_stage_ids=("sentinel", "regression"),
    )

    assert (
        decide_staged_measurement(_plan(), progress).kind
        is AdaptiveDecisionKind.STOP_CONFIDENT_POSITIVE
    )


def test_inconclusive_sentinel_admits_bounded_expansion_batch() -> None:
    decision = decide_staged_measurement(_plan(), _progress())

    assert decision.kind is AdaptiveDecisionKind.ADMIT_EXPANSION
    assert decision.next_stage_id == "expansion"
    assert decision.admit_case_ids == ("case-3",)


def test_progress_case_ids_must_belong_to_plan() -> None:
    with pytest.raises(ValueError, match="outside the measurement plan"):
        decide_staged_measurement(
            _plan(),
            _progress(
                completed_case_ids=("case-secret",),
                comparable_case_ids=(),
            ),
        )
