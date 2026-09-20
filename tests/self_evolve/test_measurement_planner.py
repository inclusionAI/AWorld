from __future__ import annotations

from aworld.core.tool.replay_policy import (
    ArtifactPolicy,
    compile_evidence_policy_profile_v2,
)
from aworld.self_evolve.measurement import (
    ComponentIdentity,
    ControlledExperimentSpec,
    ExperimentBudget,
    FrozenIdentities,
    MeasurementEarlyStopPolicy,
    MeasurementPolicyMode,
    OutcomePlan,
    SamplingPlan,
    SwapAxis,
    TransferPanel,
    TransferPanelRole,
)
from aworld.self_evolve.measurement_control import (
    CaseVisibilityRole,
    DeadlinePolicy,
    SamplingStageKind,
)
from aworld.self_evolve.measurement_planner import (
    MeasurementLatencyEstimate,
    compile_measurement_plan_v2,
    compile_screening_measurement_plan_v2,
    measurement_preflight_projection,
)
from aworld.self_evolve.replay_adaptation import (
    IsolationDecision,
    IsolationExclusiveFallback,
)
from aworld.self_evolve.measurement_control import stable_control_fingerprint


def _fp(label: str) -> str:
    return stable_control_fingerprint({"label": label})


def _experiment(
    *,
    transfer: bool = False,
    selection_protocol: str = "predeclared_candidate",
) -> ControlledExperimentSpec:
    cases = tuple(f"case-{index}" for index in range(1, 12))
    return ControlledExperimentSpec.create(
        run_id="run-planner",
        mode=MeasurementPolicyMode.SHADOW,
        swap_axis=SwapAxis.ARTIFACT,
        control=ComponentIdentity("skill:demo", _fp("control")),
        treatment=ComponentIdentity("candidate:demo", _fp("candidate")),
        frozen_identities=FrozenIdentities(
            task_model=_fp("task-model"),
            generator=_fp("generator"),
            scheduler=_fp("scheduler"),
            evaluator=_fp("evaluator"),
            dataset=_fp("dataset"),
            environment=_fp("environment"),
            runtime=_fp("runtime"),
            prompt_context=_fp("prompt"),
            budget=_fp("budget"),
        ),
        sampling=SamplingPlan(independent_case_ids=cases, repetitions_per_case=2),
        outcomes=OutcomePlan(
            primary_metric="task_success",
            minimum_effect=0.05,
            minimum_independent_cases=2,
        ),
        budgets=ExperimentBudget(),
        transfer_panels=(
            TransferPanel.create(
                panel_id="regression",
                role=TransferPanelRole.REGRESSION_CANARY,
                case_ids=("regression-1", "regression-2"),
            ),
        ) if transfer else (),
        stopping_policy=MeasurementEarlyStopPolicy(
            invalid_control_patience=2,
            zero_yield_patience=2,
        ),
        selection_protocol=selection_protocol,
    )


def _compile(experiment: ControlledExperimentSpec, **changes):
    return compile_measurement_plan_v2(
        experiment=experiment,
        dataset_fingerprint=_fp("dataset"),
        execution_contract_fingerprint=_fp("execution"),
        isolation_decision=IsolationDecision.exclusive_fallback(
            requested_lane_count=2,
            fallback=IsolationExclusiveFallback(
                code="binding_requires_exclusive",
                limiting_resource="browser",
                detail="fixture is exclusive",
            ),
        ),
        evidence_policy_profile=compile_evidence_policy_profile_v2(
            artifact_policies=(
                ArtifactPolicy(
                    artifact_type="browser.snapshot",
                    registered_producers=("browser.snapshotter",),
                    max_files=2,
                    max_items=2,
                    max_bytes=1_000_000,
                ),
            ),
        ),
        deadlines=DeadlinePolicy(
            attempt_timeout_seconds=30,
            member_hard_deadline_seconds=600,
            checkpoint_quantum_seconds=900,
            resumable_chunked=True,
        ),
        latency=MeasurementLatencyEstimate(
            minimum_member_seconds=5,
            p50_member_seconds=10,
            p90_member_seconds=20,
            source="fixture_history",
            confidence="high",
        ),
        **changes,
    )


def test_multi_case_plan_uses_small_sentinel_and_bounded_expansion() -> None:
    compiled = _compile(
        _experiment(),
        case_strata={
            **{f"case-{index}": "browser" for index in range(1, 7)},
            **{f"case-{index}": "filesystem" for index in range(7, 12)},
        },
    )

    sentinel, expansion = compiled.plan.stages
    assert sentinel.kind is SamplingStageKind.SENTINEL
    assert len(sentinel.case_ids) == 3
    assert len(expansion.case_ids) == 8
    assert len(compiled.plan.work_units) == 11 * 2 * 2
    assert compiled.feasibility.safe_lane_count == 1
    assert compiled.feasibility.decision_required_work_units == 2 * 2 * 2


def test_candidate_influencing_cases_are_excluded_from_authoritative_plan() -> None:
    compiled = _compile(
        _experiment(),
        repair_screening_case_ids=("case-1", "case-2"),
    )

    assert "case-1" not in compiled.plan.case_ids
    assert "case-2" not in compiled.plan.case_ids
    assert compiled.excluded_repair_screening_case_ids == ("case-1", "case-2")


def test_unstable_controls_are_deferred_out_of_sentinel_but_remain_optional() -> None:
    compiled = _compile(
        _experiment(),
        deferred_control_case_ids=("case-3", "case-4"),
        sentinel_case_count=2,
    )

    sentinel, expansion, deferred = compiled.plan.stages
    assert sentinel.kind is SamplingStageKind.SENTINEL
    assert set(sentinel.case_ids).isdisjoint({"case-3", "case-4"})
    assert expansion.optional is True
    assert deferred.stage_id == "deferred-controls"
    assert deferred.kind is SamplingStageKind.EXPANSION
    assert deferred.optional is True
    assert deferred.batch_size == 1
    assert set(deferred.case_ids) == {"case-3", "case-4"}
    assert set(compiled.deferred_unstable_case_ids) == {"case-3", "case-4"}


def test_screening_plan_is_frozen_as_non_authoritative_qualification() -> None:
    experiment = _experiment(
        selection_protocol="staged_qualification_candidate"
    )
    compiled = compile_screening_measurement_plan_v2(
        experiment=experiment,
        dataset_fingerprint=_fp("screening-dataset"),
        execution_contract_fingerprint=_fp("screening-execution"),
        isolation_decision=IsolationDecision.exclusive_fallback(
            requested_lane_count=2,
            fallback=IsolationExclusiveFallback(
                code="binding_requires_exclusive",
                limiting_resource="browser",
                detail="fixture is exclusive",
            ),
        ),
        evidence_policy_profile=compile_evidence_policy_profile_v2(
            artifact_policies=(
                ArtifactPolicy(
                    artifact_type="browser.snapshot",
                    registered_producers=("browser.snapshotter",),
                    max_files=2,
                    max_items=2,
                    max_bytes=1_000_000,
                ),
            ),
        ),
        deadlines=DeadlinePolicy(
            attempt_timeout_seconds=30,
            member_hard_deadline_seconds=600,
            checkpoint_quantum_seconds=900,
            resumable_chunked=True,
        ),
    )

    assert len(compiled.plan.stages) == 1
    assert compiled.plan.stages[0].kind is SamplingStageKind.SENTINEL
    assert (
        compiled.plan.stages[0].visibility_role
        is CaseVisibilityRole.REPAIR_SCREENING
    )
    assert compiled.plan.decision_policy.minimum_independent_cases == 1
    assert compiled.plan.estimator_version == "paired-screening-experiment-v2"
    assert compiled.excluded_repair_screening_case_ids == ()


def test_regression_transfer_is_frozen_but_requires_positive_effect() -> None:
    compiled = _compile(_experiment(transfer=True))

    transfer = compiled.plan.stages[-1]
    assert transfer.kind is SamplingStageKind.REGRESSION_TRANSFER
    assert transfer.requires_positive_effect is True
    assert transfer.case_ids == ("regression-1", "regression-2")
    assert compiled.plan.decision_policy.require_regression_transfer is True


def test_plan_compilation_is_deterministic_across_input_strata_order() -> None:
    first = _compile(
        _experiment(),
        case_strata={"case-1": "a", "case-2": "b"},
    )
    second = _compile(
        _experiment(),
        case_strata={"case-2": "b", "case-1": "a"},
    )

    assert first.plan == second.plan


def test_unreachable_positive_conclusion_fails_before_rollout() -> None:
    experiment = _experiment()
    try:
        _compile(
            experiment,
            repair_screening_case_ids=tuple(
                case_id
                for case_id in experiment.sampling.independent_case_ids
                if case_id not in {"case-10"}
            ),
        )
    except ValueError as exc:
        assert "positive conclusion is unreachable" in str(exc)
    else:
        raise AssertionError("unreachable plan must fail closed")


def test_preflight_projection_explains_scale_deadline_and_isolation() -> None:
    compiled = _compile(_experiment(transfer=True))
    decision = IsolationDecision.exclusive_fallback(
        requested_lane_count=2,
        fallback=IsolationExclusiveFallback(
            code="binding_requires_exclusive",
            limiting_resource="browser",
            detail="fixture is exclusive",
        ),
    )

    projection = measurement_preflight_projection(
        plan=compiled.plan,
        feasibility=compiled.feasibility,
        isolation_decision=decision,
    )

    assert projection["planned_work_units"] == len(compiled.plan.work_units)
    assert projection["pending_work_units"] == len(compiled.plan.work_units)
    assert projection["safe_lane_count"] == 1
    assert projection["isolation_fallback"] == {
        "code": "binding_requires_exclusive",
        "limiting_resource": "browser",
        "detail": "fixture is exclusive",
    }
    assert projection["p90_time_to_decision_seconds"] >= projection[
        "p50_time_to_decision_seconds"
    ]
    assert projection["sampling_stages"][-1]["kind"] == "regression_transfer"
    assert projection["stopping_policy"]["futility_enabled"] is True
