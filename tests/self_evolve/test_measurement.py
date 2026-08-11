from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from aworld.self_evolve.measurement import (
    ATTRIBUTION_REPORT_SCHEMA_VERSION,
    CONTROLLED_EXPERIMENT_SCHEMA_VERSION,
    MEASUREMENT_SUMMARY_SCHEMA_VERSION,
    ArmRole,
    AttributionReport,
    ComparabilityStatus,
    ComponentIdentity,
    ControlledExperimentSpec,
    EffectDirection,
    ExperimentBudget,
    ExperimentValidityStatus,
    FrozenIdentities,
    MeasurementObservation,
    MeasurementEarlyStopPolicy,
    MeasurementPolicyMode,
    MeasurementReadiness,
    MeasurementUsage,
    MeasurementStopTrigger,
    ObservationExecutionStatus,
    OutcomePlan,
    SamplingPlan,
    SearchCandidateResult,
    SwapAxis,
    TargetResolutionConfidence,
    TransferAudit,
    TransferPanel,
    TransferPanelRole,
    VisibilityClass,
    assess_experiment_validity,
    build_attribution_report,
    build_search_performance,
    compare_search_performance,
    estimate_paired_effect,
    evaluate_measurement_stopping,
    measurement_summary_from_report,
    observations_from_evaluation,
    observations_from_replay,
    validate_transfer_panel,
)
from aworld.self_evolve.datasets import EvalCase, SelfEvolveDataset
from aworld.self_evolve.failure_events import (
    FailureOwner,
    FailureScope,
    FailureStage,
    ReplayFailureEvent,
)
from aworld.self_evolve.replay import (
    CandidateReplayMemberResult,
    CandidateReplayRequest,
    CandidateReplayResult,
    ReplayVariantResult,
)
from aworld.self_evolve.types import DatasetRecipe, SelfEvolveTargetRef


def _fp(label: str) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(label.encode()).hexdigest()


def test_measurement_runtime_has_no_external_reference_dependency() -> None:
    source = Path(__file__).parents[2] / "aworld" / "self_evolve" / "measurement.py"

    assert "openrsi" not in source.read_text(encoding="utf-8").lower()


def _identities(**overrides: str | None) -> FrozenIdentities:
    values = {
        "task_model": _fp("task-model"),
        "generator": _fp("generator"),
        "scheduler": _fp("scheduler"),
        "evaluator": _fp("evaluator"),
        "dataset": _fp("dataset"),
        "environment": _fp("environment"),
        "runtime": _fp("runtime"),
        "prompt_context": _fp("prompt-context"),
        "budget": _fp("budget"),
    }
    values.update(overrides)
    return FrozenIdentities(**values)


def _spec(
    *,
    mode: MeasurementPolicyMode = MeasurementPolicyMode.REQUIRED,
    case_ids: tuple[str, ...] = ("case-1", "case-2", "case-3"),
    repetitions: int = 1,
    minimum_cases: int = 2,
    identities: FrozenIdentities | None = None,
    primary_metric: str = "task_success",
) -> ControlledExperimentSpec:
    return ControlledExperimentSpec.create(
        run_id="run-measurement-001",
        mode=mode,
        swap_axis=SwapAxis.ARTIFACT,
        control=ComponentIdentity("skill:demo", _fp("baseline")),
        treatment=ComponentIdentity("candidate:demo", _fp("candidate")),
        frozen_identities=identities or _identities(),
        sampling=SamplingPlan(
            independent_case_ids=case_ids,
            repetitions_per_case=repetitions,
            seeds=tuple(range(1, repetitions + 1)),
        ),
        outcomes=OutcomePlan(
            primary_metric=primary_metric,
            secondary_metrics=tuple(
                metric
                for metric in ("score", "latency_ms", "total_tokens")
                if metric != primary_metric
            ),
            minimum_effect=0.1,
            confidence_level=0.95,
            minimum_independent_cases=minimum_cases,
        ),
        budgets=ExperimentBudget(
            search=MeasurementUsage(tokens=100_000, wall_seconds=600.0),
            measurement=MeasurementUsage(tokens=50_000, wall_seconds=300.0),
        ),
    )


def _observation(
    spec: ControlledExperimentSpec,
    *,
    case_id: str,
    arm: ArmRole,
    success: bool,
    repetition: int = 1,
    comparability: ComparabilityStatus = ComparabilityStatus.COMPARABLE,
    score: float | None = None,
    failure_owner: str | None = None,
) -> MeasurementObservation:
    component = spec.control if arm is ArmRole.CONTROL else spec.treatment
    return MeasurementObservation.create(
        experiment=spec,
        arm=arm,
        case_id=case_id,
        case_fingerprint=_fp(case_id),
        split="validation",
        repetition_index=repetition,
        seed=repetition,
        component_fingerprint=component.fingerprint,
        execution_status=(
            ObservationExecutionStatus.SUCCEEDED
            if success
            else ObservationExecutionStatus.FAILED
        ),
        comparability=comparability,
        task_success=success,
        metrics={"score": score} if score is not None else {},
        usage=MeasurementUsage(tokens=100, wall_seconds=1.0),
        failure_owner=failure_owner,
        failure_code=("synthetic_failure" if not success else None),
    )


def test_controlled_experiment_round_trip_preserves_additive_fields() -> None:
    spec = _spec()
    payload = spec.to_dict()
    payload["future_additive_field"] = {"enabled": True}

    loaded = ControlledExperimentSpec.from_dict(payload)

    assert loaded.schema_version == CONTROLLED_EXPERIMENT_SCHEMA_VERSION
    assert loaded.experiment_id == spec.experiment_id
    assert loaded.to_dict()["future_additive_field"] == {"enabled": True}


def test_controlled_experiment_rejects_multiple_swap_axes() -> None:
    with pytest.raises(ValueError, match="exactly one swap axis"):
        ControlledExperimentSpec.create(
            run_id="run-measurement-001",
            mode=MeasurementPolicyMode.SHADOW,
            swap_axis=SwapAxis.ARTIFACT,
            changed_axes=(SwapAxis.ARTIFACT, SwapAxis.TASK_MODEL),
            control=ComponentIdentity("skill:demo", _fp("baseline")),
            treatment=ComponentIdentity("candidate:demo", _fp("candidate")),
            frozen_identities=_identities(),
            sampling=SamplingPlan(independent_case_ids=("case-1",)),
            outcomes=OutcomePlan(primary_metric="task_success"),
            budgets=ExperimentBudget(),
        )


@pytest.mark.parametrize("axis", tuple(SwapAxis))
def test_controlled_experiment_supports_each_single_swap_axis(
    axis: SwapAxis,
) -> None:
    spec = ControlledExperimentSpec.create(
        run_id=f"run-{axis.value.replace('_', '-')}",
        mode=MeasurementPolicyMode.SHADOW,
        swap_axis=axis,
        control=ComponentIdentity(f"{axis.value}:control", _fp("control")),
        treatment=ComponentIdentity(
            f"{axis.value}:treatment",
            _fp("treatment"),
        ),
        frozen_identities=_identities(),
        sampling=SamplingPlan(independent_case_ids=("case-1",)),
        outcomes=OutcomePlan(primary_metric="task_success"),
        budgets=ExperimentBudget(),
    )

    assert spec.swap_axis is axis
    assert spec.changed_axes == (axis,)


@pytest.mark.parametrize("axis", (SwapAxis.ARTIFACT, SwapAxis.TASK_MODEL))
def test_artifact_and_task_model_effects_keep_distinct_attribution_axes(
    axis: SwapAxis,
) -> None:
    spec = ControlledExperimentSpec.create(
        run_id=f"run-effect-{axis.value.replace('_', '-')}",
        mode=MeasurementPolicyMode.REQUIRED,
        swap_axis=axis,
        control=ComponentIdentity(f"{axis.value}:control", _fp("control")),
        treatment=ComponentIdentity(
            f"{axis.value}:treatment",
            _fp("treatment"),
        ),
        frozen_identities=_identities(),
        sampling=SamplingPlan(independent_case_ids=("case-1", "case-2")),
        outcomes=OutcomePlan(
            primary_metric="task_success",
            minimum_effect=0.1,
            minimum_independent_cases=2,
        ),
        budgets=ExperimentBudget(),
    )
    observations = tuple(
        _observation(
            spec,
            case_id=case_id,
            arm=arm,
            success=(arm is ArmRole.TREATMENT),
        )
        for case_id in spec.sampling.independent_case_ids
        for arm in (ArmRole.CONTROL, ArmRole.TREATMENT)
    )

    effect = estimate_paired_effect(spec, observations)

    assert effect is not None
    assert effect.direction is EffectDirection.POSITIVE
    assert spec.swap_axis is axis


def test_experiment_id_changes_when_a_frozen_identity_changes() -> None:
    original = _spec()
    changed = _spec(identities=_identities(task_model=_fp("new-model")))

    assert original.experiment_id != changed.experiment_id


def test_experiment_id_freezes_early_stop_policy() -> None:
    original = _spec()
    changed = ControlledExperimentSpec.create(
        run_id=original.run_id,
        mode=original.mode,
        swap_axis=original.swap_axis,
        control=original.control,
        treatment=original.treatment,
        frozen_identities=original.frozen_identities,
        sampling=original.sampling,
        outcomes=original.outcomes,
        budgets=original.budgets,
        stopping_policy=MeasurementEarlyStopPolicy(zero_yield_patience=3),
    )

    assert original.experiment_id != changed.experiment_id


def test_required_mode_fails_closed_on_missing_identity() -> None:
    spec = _spec(identities=_identities(environment=None))

    validity = assess_experiment_validity(spec, ())

    assert validity.status is ExperimentValidityStatus.INVALID
    assert "missing_frozen_identity" in validity.reason_codes


def test_shadow_mode_marks_missing_identity_non_conclusive() -> None:
    spec = _spec(
        mode=MeasurementPolicyMode.SHADOW,
        identities=_identities(environment=None),
    )

    validity = assess_experiment_validity(spec, ())

    assert validity.status is ExperimentValidityStatus.INCONCLUSIVE
    assert "missing_frozen_identity" in validity.reason_codes


def test_invalid_control_has_null_effect_not_zero() -> None:
    spec = _spec(case_ids=("case-1",), minimum_cases=1)
    observations = (
        _observation(
            spec,
            case_id="case-1",
            arm=ArmRole.CONTROL,
            success=False,
            comparability=ComparabilityStatus.INCOMPARABLE,
            failure_owner="infrastructure",
        ),
        _observation(
            spec,
            case_id="case-1",
            arm=ArmRole.TREATMENT,
            success=False,
            comparability=ComparabilityStatus.INCOMPARABLE,
            failure_owner="infrastructure",
        ),
    )

    report = build_attribution_report(
        spec,
        observations,
        target_resolution=TargetResolutionConfidence(
            confidence=1.0,
            origin="operator_explicit",
            inference_bypassed=True,
            causal_confidence=None,
        ),
        readiness=MeasurementReadiness(
            previous_stage="capability_compile",
            current_stage="task_rollout",
        ),
    )

    assert report.validity.status is ExperimentValidityStatus.INVALID
    assert report.validity.failed_arm_count == 2
    assert report.validity.missing_arm_count == 0
    assert report.effect is None
    assert report.summary().effect_estimate is None
    assert report.summary().effect_direction is EffectDirection.UNMEASURED
    assert report.target_resolution.confidence == 1.0
    assert report.target_resolution.causal_confidence is None
    assert report.decision.promotion_eligible is False
    assert report.decision.next_action.value == "repair_measurement"


def test_repetitions_do_not_inflate_independent_case_count() -> None:
    spec = _spec(case_ids=("case-1", "case-2"), repetitions=3)
    observations = tuple(
        _observation(
            spec,
            case_id=case_id,
            arm=arm,
            success=(arm is ArmRole.TREATMENT),
            repetition=repetition,
        )
        for case_id in spec.sampling.independent_case_ids
        for repetition in range(1, 4)
        for arm in (ArmRole.CONTROL, ArmRole.TREATMENT)
    )

    validity = assess_experiment_validity(spec, observations)
    effect = estimate_paired_effect(spec, observations, validity=validity)

    assert validity.independent_case_count == 2
    assert validity.repetition_count == 6
    assert validity.comparable_pair_count == 6
    assert effect is not None
    assert effect.independent_case_count == 2
    assert effect.repetition_count == 6
    assert effect.point_estimate == 1.0
    assert effect.direction is EffectDirection.POSITIVE


def test_exact_zero_effect_is_neutral_at_zero_minimum() -> None:
    original = _spec(minimum_cases=2)
    spec = ControlledExperimentSpec.create(
        run_id=original.run_id,
        mode=original.mode,
        swap_axis=original.swap_axis,
        control=original.control,
        treatment=original.treatment,
        frozen_identities=original.frozen_identities,
        sampling=original.sampling,
        outcomes=replace(original.outcomes, minimum_effect=0.0),
        budgets=original.budgets,
    )
    observations = tuple(
        _observation(spec, case_id=case_id, arm=arm, success=True)
        for case_id in spec.sampling.independent_case_ids
        for arm in (ArmRole.CONTROL, ArmRole.TREATMENT)
    )

    effect = estimate_paired_effect(spec, observations)

    assert effect is not None
    assert effect.point_estimate == 0.0
    assert effect.direction is EffectDirection.NEUTRAL


def test_conclusive_negative_effect_stops_candidate() -> None:
    spec = _spec(minimum_cases=2)
    observations = tuple(
        _observation(
            spec,
            case_id=case_id,
            arm=arm,
            success=(arm is ArmRole.CONTROL),
        )
        for case_id in spec.sampling.independent_case_ids
        for arm in (ArmRole.CONTROL, ArmRole.TREATMENT)
    )

    report = build_attribution_report(
        spec,
        observations,
        target_resolution=TargetResolutionConfidence(
            confidence=1.0,
            origin="operator_explicit",
            inference_bypassed=True,
        ),
        total_usage=MeasurementUsage(tokens=1_000, wall_seconds=10.0),
    )

    assert report.effect is not None
    assert report.effect.direction is EffectDirection.NEGATIVE
    assert report.decision.next_action.value == "stop_negative_effect"
    assert report.decision.promotion_eligible is False


def test_small_independent_sample_is_valid_limited_and_inconclusive() -> None:
    spec = _spec(case_ids=("case-1",), minimum_cases=2)
    observations = (
        _observation(spec, case_id="case-1", arm=ArmRole.CONTROL, success=False),
        _observation(spec, case_id="case-1", arm=ArmRole.TREATMENT, success=True),
    )

    validity = assess_experiment_validity(spec, observations)
    effect = estimate_paired_effect(spec, observations, validity=validity)

    assert validity.status is ExperimentValidityStatus.VALID_LIMITED
    assert "insufficient_independent_cases" in validity.reason_codes
    assert effect is not None
    assert effect.direction is EffectDirection.INCONCLUSIVE
    assert effect.confidence_lower_bound is None


def test_candidate_failure_routes_to_candidate_only_after_valid_control() -> None:
    spec = _spec(
        case_ids=("case-1",),
        minimum_cases=1,
        primary_metric="score",
    )
    control = _observation(
        spec,
        case_id="case-1",
        arm=ArmRole.CONTROL,
        success=True,
        score=0.5,
    )
    treatment = _observation(
        spec,
        case_id="case-1",
        arm=ArmRole.TREATMENT,
        success=False,
        failure_owner="candidate",
    )

    report = build_attribution_report(
        spec,
        (control, treatment),
        target_resolution=TargetResolutionConfidence(
            confidence=1.0,
            origin="operator_explicit",
            inference_bypassed=True,
        ),
        total_usage=MeasurementUsage(tokens=200, wall_seconds=2.0),
    )

    assert report.validity.status is ExperimentValidityStatus.VALID
    assert report.effect is None
    assert report.decision.next_action.value == "continue_candidate_repair"
    assert report.decision.owner == "candidate"


def test_infrastructure_failure_under_valid_control_repairs_measurement() -> None:
    spec = _spec(
        case_ids=("case-1",),
        minimum_cases=1,
        primary_metric="score",
    )
    control = _observation(
        spec,
        case_id="case-1",
        arm=ArmRole.CONTROL,
        success=True,
        score=0.5,
    )
    treatment = _observation(
        spec,
        case_id="case-1",
        arm=ArmRole.TREATMENT,
        success=False,
        failure_owner="infrastructure",
    )

    report = build_attribution_report(
        spec,
        (control, treatment),
        target_resolution=TargetResolutionConfidence(
            confidence=1.0,
            origin="operator_explicit",
            inference_bypassed=True,
        ),
        total_usage=MeasurementUsage(tokens=200, wall_seconds=2.0),
    )

    assert report.effect is None
    assert report.decision.next_action.value == "repair_measurement"
    assert report.decision.owner == "evaluation_harness"


def test_case_bootstrap_is_deterministic_and_reports_positive_effect() -> None:
    spec = _spec(case_ids=("case-1", "case-2", "case-3", "case-4"))
    observations = tuple(
        _observation(
            spec,
            case_id=case_id,
            arm=arm,
            success=(arm is ArmRole.TREATMENT),
        )
        for case_id in spec.sampling.independent_case_ids
        for arm in (ArmRole.CONTROL, ArmRole.TREATMENT)
    )
    validity = assess_experiment_validity(spec, observations)

    first = estimate_paired_effect(spec, observations, validity=validity)
    second = estimate_paired_effect(spec, observations, validity=validity)

    assert first == second
    assert first is not None
    assert first.direction is EffectDirection.POSITIVE
    assert first.confidence_lower_bound == 1.0


def test_observation_identity_drift_invalidates_experiment() -> None:
    spec = _spec(case_ids=("case-1",), minimum_cases=1)
    control = _observation(
        spec, case_id="case-1", arm=ArmRole.CONTROL, success=False
    )
    treatment = replace(
        _observation(
            spec, case_id="case-1", arm=ArmRole.TREATMENT, success=True
        ),
        frozen_identity_fingerprint=_fp("drift"),
    )

    validity = assess_experiment_validity(spec, (control, treatment))

    assert validity.status is ExperimentValidityStatus.INVALID
    assert "identity_mismatch" in validity.reason_codes


def test_search_performance_reports_actual_k_and_budget_curves() -> None:
    results = (
        SearchCandidateResult(
            "candidate-1",
            score=0.1,
            passed=False,
            tokens=100,
            wall_seconds=1.0,
            cost_usd=0.01,
            regression_passed=True,
        ),
        SearchCandidateResult(
            "candidate-2",
            score=0.8,
            passed=True,
            tokens=200,
            wall_seconds=2.0,
            cost_usd=0.02,
            regression_passed=True,
        ),
        SearchCandidateResult(
            "candidate-3",
            score=0.6,
            passed=True,
            valid=False,
            tokens=300,
            wall_seconds=3.0,
            cost_usd=0.03,
            regression_passed=False,
        ),
    )

    performance = build_search_performance(
        results,
        k_values=(1, 2, 4),
        token_budget_points=(100, 300, 1_000),
        wall_time_budget_points=(1.0, 3.0, 10.0),
        selection_protocol="highest_valid_score",
        quality_threshold=0.75,
    )

    assert performance.candidate_count == 3
    assert [point.actual_k for point in performance.k_points] == [1, 2, 3]
    assert performance.k_points[0].best_score == 0.1
    assert performance.k_points[1].pass_probability == 1.0
    assert performance.validity_rate == pytest.approx(2 / 3)
    assert performance.token_curve[-1].actual_budget == 600
    assert performance.token_curve[-1].candidate_count == 3
    assert performance.token_curve[-1].cumulative_cost_usd == pytest.approx(0.06)
    assert performance.token_curve[-1].regression_pass_rate == pytest.approx(2 / 3)
    assert performance.tokens_to_threshold == 300
    assert performance.wall_seconds_to_threshold == pytest.approx(3.0)


def test_unequal_search_opportunity_is_descriptive() -> None:
    control = build_search_performance(
        (SearchCandidateResult("c1", score=0.1, passed=False, tokens=100),),
        k_values=(1, 2),
        selection_protocol="score",
    )
    treatment = build_search_performance(
        (
            SearchCandidateResult("t1", score=0.2, passed=False, tokens=100),
            SearchCandidateResult("t2", score=0.9, passed=True, tokens=100),
        ),
        k_values=(1, 2),
        selection_protocol="score",
    )

    comparison = compare_search_performance(control, treatment)

    assert comparison.opportunity_matched is False
    assert comparison.attribution_allowed is False
    assert comparison.shared_k == (1,)


@pytest.mark.parametrize("axis", (SwapAxis.GENERATOR, SwapAxis.SCHEDULER))
def test_component_search_swap_allows_only_equal_opportunity_attribution(
    axis: SwapAxis,
) -> None:
    control = build_search_performance(
        (
            SearchCandidateResult("c1", score=0.2, passed=False, tokens=100),
            SearchCandidateResult("c2", score=0.4, passed=False, tokens=100),
        ),
        k_values=(1, 2),
        token_budget_points=(100, 200),
        selection_protocol=f"{axis.value}-equal-budget",
    )
    treatment = build_search_performance(
        (
            SearchCandidateResult("t1", score=0.3, passed=False, tokens=100),
            SearchCandidateResult("t2", score=0.8, passed=True, tokens=100),
        ),
        k_values=(1, 2),
        token_budget_points=(100, 200),
        selection_protocol=f"{axis.value}-equal-budget",
    )

    comparison = compare_search_performance(control, treatment)

    assert comparison.opportunity_matched is True
    assert comparison.attribution_allowed is True
    assert comparison.shared_k == (1, 2)


def test_temporal_panel_rejects_leakage_and_pre_cutoff_cases() -> None:
    panel = TransferPanel.create(
        panel_id="temporal-2026q3",
        role=TransferPanelRole.TEMPORAL,
        case_ids=("future-case",),
        visibility=VisibilityClass.FINAL_ONLY,
        optimization_cutoff_at="2026-08-01T00:00:00Z",
        sealed_at="2026-08-02T00:00:00Z",
        case_sealed_at={"future-case": "2026-07-31T23:59:00Z"},
        exposed_to_search=True,
    )

    audit = validate_transfer_panel(panel)

    assert audit.passed is False
    assert set(audit.reason_codes) == {"held_out_leakage", "temporal_cutoff_violation"}


def test_transfer_panel_rejects_fingerprint_drift() -> None:
    panel = TransferPanel.create(
        panel_id="cross-task",
        role=TransferPanelRole.CROSS_TASK,
        case_ids=("case-a",),
    )
    payload = panel.to_dict()
    payload["case_ids"] = ["case-b"]

    with pytest.raises(ValueError, match="fingerprint"):
        TransferPanel.from_dict(payload)


def test_hidden_transfer_case_cannot_enter_search_context() -> None:
    panel = TransferPanel.create(
        panel_id="hidden-cross-task",
        role=TransferPanelRole.CROSS_TASK,
        case_ids=("hidden-case",),
        visibility=VisibilityClass.HIDDEN,
    )

    with pytest.raises(ValueError, match="held_out_leakage"):
        ControlledExperimentSpec.create(
            run_id="run-hidden-leakage",
            mode=MeasurementPolicyMode.SHADOW,
            swap_axis=SwapAxis.ARTIFACT,
            control=ComponentIdentity("artifact:control", _fp("control")),
            treatment=ComponentIdentity(
                "artifact:treatment",
                _fp("treatment"),
            ),
            frozen_identities=_identities(),
            sampling=SamplingPlan(independent_case_ids=("case-1",)),
            outcomes=OutcomePlan(primary_metric="task_success"),
            budgets=ExperimentBudget(),
            transfer_panels=(panel,),
            search_visible_case_ids=("hidden-case",),
        )


def test_optional_transfer_audit_does_not_block_positive_effect() -> None:
    spec = _spec(case_ids=("case-1", "case-2"))
    observations = tuple(
        _observation(
            spec,
            case_id=case_id,
            arm=arm,
            success=(arm is ArmRole.TREATMENT),
        )
        for case_id in spec.sampling.independent_case_ids
        for arm in (ArmRole.CONTROL, ArmRole.TREATMENT)
    )

    report = build_attribution_report(
        spec,
        observations,
        target_resolution=TargetResolutionConfidence(
            confidence=1.0,
            origin="operator_explicit",
            inference_bypassed=True,
        ),
        total_usage=MeasurementUsage(tokens=1_000, wall_seconds=10.0),
        transfer_audits=(
            TransferAudit(
                panel_id="optional-canary",
                role=TransferPanelRole.REGRESSION_CANARY,
                passed=False,
                required=False,
                reason_codes=("canary_regression",),
            ),
        ),
    )

    assert report.effect is not None
    assert report.effect.direction is EffectDirection.POSITIVE
    assert report.decision.promotion_eligible is True


def test_required_transfer_leakage_routes_to_measurement_repair() -> None:
    spec = _spec(case_ids=("case-1", "case-2"))
    observations = tuple(
        _observation(
            spec,
            case_id=case_id,
            arm=arm,
            success=(arm is ArmRole.TREATMENT),
        )
        for case_id in spec.sampling.independent_case_ids
        for arm in (ArmRole.CONTROL, ArmRole.TREATMENT)
    )

    report = build_attribution_report(
        spec,
        observations,
        target_resolution=TargetResolutionConfidence(
            confidence=1.0,
            origin="operator_explicit",
            inference_bypassed=True,
        ),
        total_usage=MeasurementUsage(tokens=1_000, wall_seconds=10.0),
        transfer_audits=(
            TransferAudit(
                panel_id="leaked-panel",
                role=TransferPanelRole.CROSS_TASK,
                passed=False,
                reason_codes=("held_out_leakage",),
            ),
        ),
    )

    assert report.decision.next_action.value == "repair_measurement"
    assert report.decision.owner == "evaluation_harness"


def test_required_transfer_regression_blocks_in_domain_improvement() -> None:
    spec = _spec(case_ids=("case-1", "case-2"))
    observations = tuple(
        _observation(
            spec,
            case_id=case_id,
            arm=arm,
            success=(arm is ArmRole.TREATMENT),
        )
        for case_id in spec.sampling.independent_case_ids
        for arm in (ArmRole.CONTROL, ArmRole.TREATMENT)
    )
    report = build_attribution_report(
        spec,
        observations,
        target_resolution=TargetResolutionConfidence(
            confidence=1.0,
            origin="operator_explicit",
            inference_bypassed=True,
        ),
        total_usage=MeasurementUsage(tokens=1_000, wall_seconds=10.0),
        transfer_audits=(
            TransferAudit(
                panel_id="required-cross-skill",
                role=TransferPanelRole.CROSS_SKILL_FAMILY,
                passed=False,
                reason_codes=("transfer_regression",),
            ),
        ),
    )

    assert report.effect is not None
    assert report.effect.direction is EffectDirection.POSITIVE
    assert report.decision.next_action.value == "stop_negative_effect"
    assert report.summary().required_transfer_failure_count == 1


def test_attribution_round_trip_and_bounded_summary() -> None:
    spec = _spec(case_ids=("case-1", "case-2"))
    observations = tuple(
        _observation(
            spec,
            case_id=case_id,
            arm=arm,
            success=(arm is ArmRole.TREATMENT),
        )
        for case_id in spec.sampling.independent_case_ids
        for arm in (ArmRole.CONTROL, ArmRole.TREATMENT)
    )
    report = build_attribution_report(
        spec,
        observations,
        target_resolution=TargetResolutionConfidence(
            confidence=1.0,
            origin="operator_explicit",
            inference_bypassed=True,
        ),
        total_usage=MeasurementUsage(tokens=1_000, wall_seconds=10.0),
        search_usage=MeasurementUsage(tokens=2_000, wall_seconds=20.0),
    )

    loaded = AttributionReport.from_dict(report.to_dict())
    summary = loaded.summary(attribution_report_path="experiments/x/attribution_report.json")
    payload = summary.to_dict()

    assert loaded.schema_version == ATTRIBUTION_REPORT_SCHEMA_VERSION
    assert loaded.budget_ledger.total.tokens == 3_000
    assert loaded.budget_ledger.dominant_use == "search"
    assert loaded.measurement_yield.search_tokens == 2_000
    assert payload["schema_version"] == MEASUREMENT_SUMMARY_SCHEMA_VERSION
    assert "observations" not in payload
    assert payload["effect_direction"] == "positive"
    assert payload["promotion_eligible"] is True
    assert measurement_summary_from_report({"measurement": payload}) == summary


def test_historical_report_defaults_measurement_to_off() -> None:
    assert measurement_summary_from_report({"status": "succeeded"}) is None


def test_synthetic_agent_browser_shape_keeps_effect_unmeasured() -> None:
    spec = _spec(
        mode=MeasurementPolicyMode.ADVISORY,
        case_ids=("train-1", "validation-1", "held-out-1"),
    )
    observations = tuple(
        _observation(
            spec,
            case_id=case_id,
            arm=arm,
            success=False,
            comparability=ComparabilityStatus.INCOMPARABLE,
            failure_owner="infrastructure",
        )
        for case_id in spec.sampling.independent_case_ids
        for arm in (ArmRole.CONTROL, ArmRole.TREATMENT)
    )

    report = build_attribution_report(
        spec,
        observations,
        target_resolution=TargetResolutionConfidence(
            confidence=1.0,
            origin="operator_explicit",
            inference_bypassed=True,
            causal_confidence=None,
        ),
        readiness=MeasurementReadiness(
            previous_stage="capability_compile",
            current_stage="task_rollout",
        ),
        generated_candidate_count=11,
        total_usage=MeasurementUsage(tokens=465_993, wall_seconds=2_820.82),
    )

    assert report.validity.comparable_pair_count == 0
    assert report.validity.incomparable_pair_count == 3
    assert report.effect is None
    assert report.measurement_yield.comparable_pairs_per_100k_tokens == 0.0
    assert report.measurement_readiness.progressed is True
    assert report.decision.next_action.value == "repair_measurement"
    assert report.decision.owner == "evaluation_harness"

    stopping = evaluate_measurement_stopping(
        (report, report),
        policy=MeasurementEarlyStopPolicy(
            zero_yield_patience=2,
            invalid_control_patience=2,
        ),
        unused_budget=MeasurementUsage(tokens=10_000, wall_seconds=60.0),
    )
    assert stopping.triggered is True
    assert stopping.trigger is MeasurementStopTrigger.REPEATED_CONTROL_INVALIDITY
    assert stopping.resume_safe is True
    assert stopping.unused_budget.tokens == 10_000


def test_replay_adapter_preserves_case_and_repetition_coordinates(tmp_path) -> None:
    spec = _spec(case_ids=("case-1",), repetitions=2, minimum_cases=1)
    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="case-1", input={"task": "demo"}),),
        recipe=DatasetRecipe(
            source={"kind": "jsonl"},
            split_seed="seed",
            splits={"validation": ["case-1"]},
        ),
    )
    request = CandidateReplayRequest(
        run_id=spec.run_id,
        task_id="case-1",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate_id="candidate:demo",
        overlay_skill_root=str(tmp_path / "overlay"),
        task_input={"task": "demo"},
        baseline_repetitions=2,
        candidate_repetitions=2,
    )

    def aggregate(variant_id: str, scores: tuple[float, float]) -> ReplayVariantResult:
        repetitions = tuple(
            ReplayVariantResult(
                variant_id=f"{variant_id}-{index}",
                status="succeeded",
                trajectory=[],
                metrics={
                    "score": score,
                    "latency_ms": 10.0 + index,
                    "total_tokens": 100 + index,
                },
                stdout_path=str(tmp_path / "run" / f"{variant_id}-{index}.log"),
            )
            for index, score in enumerate(scores, start=1)
        )
        return ReplayVariantResult(
            variant_id=variant_id,
            status="succeeded",
            trajectory=[],
            metrics={"repetition_count": 2},
            repetition_results=repetitions,
        )

    result = CandidateReplayResult(
        request=request,
        baseline=aggregate("baseline", (0.1, 0.2)),
        candidate=aggregate("candidate", (0.8, 0.9)),
        member_results=(
            CandidateReplayMemberResult(
                case_id="case-1",
                request=request,
                baseline=aggregate("baseline", (0.1, 0.2)),
                candidate=aggregate("candidate", (0.8, 0.9)),
            ),
        ),
    )

    observations = observations_from_replay(
        spec,
        dataset=dataset,
        replay_result=result,
        run_root=tmp_path / "run",
    )

    assert len(observations) == 4
    assert {item.repetition_index for item in observations} == {1, 2}
    assert {item.case_id for item in observations} == {"case-1"}
    assert all(item.comparability is ComparabilityStatus.COMPARABLE for item in observations)
    assert all(
        not ref.startswith("/")
        for item in observations
        for ref in item.artifact_refs
    )
    validity = assess_experiment_validity(spec, observations)
    assert validity.independent_case_count == 1
    assert validity.repetition_count == 2


def test_replay_adapter_keeps_infrastructure_timeout_incomparable(tmp_path) -> None:
    spec = _spec(case_ids=("case-1",), minimum_cases=1)
    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="case-1", input="demo"),),
        recipe=DatasetRecipe(
            source={"kind": "jsonl"},
            split_seed="seed",
            splits={"held_out": ["case-1"]},
            held_out_case_ids=("case-1",),
        ),
    )
    request = CandidateReplayRequest(
        run_id=spec.run_id,
        task_id="case-1",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate_id="candidate:demo",
        overlay_skill_root=str(tmp_path / "overlay"),
        task_input="demo",
    )
    failure = ReplayFailureEvent(
        code="replay_timeout",
        owner=FailureOwner.INFRASTRUCTURE,
        stage=FailureStage.TASK_ROLLOUT,
        scope=FailureScope.SHARED_RUN,
        repairable=True,
    )
    failed = ReplayVariantResult(
        variant_id="failed",
        status="failed",
        trajectory=[],
        failure=failure,
    )
    result = CandidateReplayResult(
        request=request,
        baseline=failed,
        candidate=replace(failed, variant_id="candidate"),
    )

    observations = observations_from_replay(
        spec, dataset=dataset, replay_result=result
    )
    report = build_attribution_report(
        spec,
        observations,
        target_resolution=TargetResolutionConfidence(
            confidence=1.0,
            origin="operator_explicit",
            inference_bypassed=True,
        ),
    )

    assert len(observations) == 2
    assert all(item.split == "held_out" for item in observations)
    assert all(item.execution_status is ObservationExecutionStatus.TIMEOUT for item in observations)
    assert report.validity.timed_out_arm_count == 2
    assert all(item.comparability is ComparabilityStatus.INCOMPARABLE for item in observations)
    assert report.effect is None


def test_evaluation_adapter_preserves_repetition_major_case_coordinates() -> None:
    spec = _spec(
        case_ids=("case-a", "case-b"),
        repetitions=2,
        minimum_cases=2,
        primary_metric="score",
    )
    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(case_id="case-a", input={"task": "a"}),
            EvalCase(case_id="case-b", input={"task": "b"}),
        ),
        recipe=DatasetRecipe(
            source={"kind": "jsonl"},
            split_seed="seed",
            splits={"validation": ["case-a", "case-b"]},
        ),
    )
    baseline = SimpleNamespace(
        metrics={"score_samples": [1.0, 2.0, 3.0, 4.0]}
    )
    candidate = SimpleNamespace(
        metrics={"score_samples": [2.0, 4.0, 6.0, 8.0]}
    )

    observations = observations_from_evaluation(
        spec,
        dataset=dataset,
        baseline_summary=baseline,
        candidate_summary=candidate,
    )

    treatment = [item for item in observations if item.arm is ArmRole.TREATMENT]
    assert [item.case_id for item in treatment] == [
        "case-a",
        "case-b",
        "case-a",
        "case-b",
    ]
    assert [item.metrics["score"] for item in treatment] == [
        2.0,
        4.0,
        6.0,
        8.0,
    ]
    assert all(
        item.comparability is ComparabilityStatus.COMPARABLE
        for item in observations
    )
