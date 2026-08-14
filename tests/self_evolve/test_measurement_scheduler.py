from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from pathlib import Path

import pytest

from aworld.core.tool.replay_policy import (
    ArtifactPolicy,
    compile_evidence_policy_profile_v2,
)
from aworld.self_evolve.measurement import (
    ComponentIdentity,
    ControlledExperimentSpec,
    ExperimentBudget,
    FrozenIdentities,
    MeasurementPolicyMode,
    OutcomePlan,
    SamplingPlan,
    SwapAxis,
)
from aworld.self_evolve.measurement_control import (
    AdaptiveMeasurementPolicy,
    DeadlinePolicy,
    MeasurementPlanV2,
    MeasurementWorkUnitState,
    SamplingStage,
    SamplingStageKind,
    stable_control_fingerprint,
)
from aworld.self_evolve.measurement_execution import MeasurementExecutionJournal
from aworld.self_evolve.measurement_scheduler import (
    FrameworkFilesystemLaneMaterializer,
    LaneExecutionContext,
    LaneMaterializationResult,
    PairLaneStopKind,
    PairLaneWorkItem,
    ResolvedControl,
    load_measurement_schedule_bundle,
    schedule_pair_lanes,
)
from aworld.self_evolve.replay_adaptation import (
    IsolationDecision,
    IsolationExclusiveFallback,
    IsolationGrant,
    ReplayIsolationTopology,
)
from aworld.self_evolve.store import FilesystemSelfEvolveStore


def _fp(label: str) -> str:
    return stable_control_fingerprint({"label": label})


def _exclusive_decision() -> IsolationDecision:
    return IsolationDecision.exclusive_fallback(
        requested_lane_count=2,
        fallback=IsolationExclusiveFallback(
            code="binding_requires_exclusive",
            limiting_resource="browser_profile",
            detail="fixture has no isolated materialization",
        ),
    )


def _grant(lane: int, root: Path) -> IsolationGrant:
    lane_root = root / f"lane-{lane}"
    topology = ReplayIsolationTopology.create(
        materializer_id="test-materializer",
        materializer_fingerprint=(
            "sha256:" + hashlib.sha256(b"test-materializer:v1").hexdigest()
        ),
        workspace_identity=str(lane_root / "workspace"),
        runtime_identity=str(lane_root / "runtime"),
        browser_profile_identity=str(lane_root / "browser-profile"),
        endpoint_namespace_identity=str(lane_root / "endpoint-namespace"),
        evidence_directory_identity=str(lane_root / "evidence"),
        services=(),
        resources=(),
        binding_coverage=(),
        cleanup_owner=f"cleanup-owner:lane-{lane}",
    )
    return IsolationGrant.create(topology=topology, binding_fingerprints=())


def _isolated_decision(root: Path) -> IsolationDecision:
    return IsolationDecision.create(
        requested_lane_count=2,
        grants=(_grant(1, root), _grant(2, root)),
    )


def _bundle(
    tmp_path: Path,
    *,
    decision: IsolationDecision,
    case_count: int,
) -> tuple[FilesystemSelfEvolveStore, object]:
    run_id = "run-scheduler"
    case_ids = tuple(f"case-{index}" for index in range(1, case_count + 1))
    experiment = ControlledExperimentSpec.create(
        run_id=run_id,
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
        sampling=SamplingPlan(independent_case_ids=case_ids),
        outcomes=OutcomePlan(
            primary_metric="task_success",
            minimum_independent_cases=1,
        ),
        budgets=ExperimentBudget(),
    )
    profile = compile_evidence_policy_profile_v2(
        artifact_policies=(
            ArtifactPolicy(
                artifact_type="browser.snapshot",
                registered_producers=("browser.snapshotter",),
                max_files=1,
                max_items=1,
                max_bytes=1_000_000,
            ),
        ),
    )
    plan = MeasurementPlanV2.create(
        experiment_id=experiment.experiment_id,
        plan_revision=1,
        candidate_fingerprint=experiment.treatment.fingerprint or _fp("candidate"),
        control_fingerprint=experiment.control.fingerprint or _fp("control"),
        dataset_fingerprint=_fp("dataset"),
        execution_contract_fingerprint=_fp("execution"),
        isolation_decision=decision,
        evidence_policy_profile=profile,
        stages=(
            SamplingStage(
                stage_id="sentinel",
                kind=SamplingStageKind.SENTINEL,
                case_ids=case_ids,
                minimum_case_count=1,
            ),
        ),
        repetitions_per_case=1,
        deadlines=DeadlinePolicy(
            attempt_timeout_seconds=10,
            member_hard_deadline_seconds=20,
            checkpoint_quantum_seconds=30,
        ),
        decision_policy=AdaptiveMeasurementPolicy(
            minimum_effect=0.01,
            minimum_independent_cases=1,
            maximum_invalid_controls=1,
            zero_yield_window=1,
            require_regression_transfer=False,
        ),
        estimator_version="estimator-v1",
        decision_policy_version="policy-v1",
    )
    store = FilesystemSelfEvolveStore(tmp_path)
    store.write_measurement_experiment(experiment)
    store.write_measurement_control_plan(
        run_id,
        plan,
        isolation_decision=decision,
        evidence_policy_profile=profile,
    )
    return store, load_measurement_schedule_bundle(
        store,
        run_id=run_id,
        measurement_plan_fingerprint=plan.measurement_plan_fingerprint,
    )


@pytest.mark.asyncio
async def test_pair_scheduler_preserves_control_treatment_order(tmp_path: Path) -> None:
    store, bundle = _bundle(
        tmp_path, decision=_exclusive_decision(), case_count=2
    )
    events: list[str] = []

    async def control(item: PairLaneWorkItem, context: LaneExecutionContext) -> str:
        events.append(f"{item.case_id}:control")
        return "ok"

    async def treatment(
        item: PairLaneWorkItem, context: LaneExecutionContext, baseline: str
    ) -> str:
        assert baseline == "ok"
        events.append(f"{item.case_id}:treatment")
        return "candidate"

    result = await schedule_pair_lanes(
        store,
        run_id="run-scheduler",
        measurement_plan_fingerprint=bundle.plan.measurement_plan_fingerprint,
        run_control=control,
        run_treatment=treatment,
        lane_materializer=FrameworkFilesystemLaneMaterializer(tmp_path / "lanes"),
    )

    assert result.stop_kind is PairLaneStopKind.COMPLETED
    assert result.safe_lane_count == 1
    assert events == [
        "case-1:control",
        "case-1:treatment",
        "case-2:control",
        "case-2:treatment",
    ]


@pytest.mark.asyncio
async def test_verified_grants_are_injected_into_two_pair_lanes(tmp_path: Path) -> None:
    store, bundle = _bundle(
        tmp_path, decision=_isolated_decision(tmp_path / "lanes"), case_count=4
    )
    active = 0
    peak = 0
    seen: dict[int, LaneExecutionContext] = {}
    lock = asyncio.Lock()

    async def control(item: PairLaneWorkItem, context: LaneExecutionContext) -> str:
        nonlocal active, peak
        seen[context.lane_id] = context
        async with lock:
            active += 1
            peak = max(peak, active)
        await asyncio.sleep(0.02)
        async with lock:
            active -= 1
        return f"control-{context.lane_id}"

    async def treatment(
        item: PairLaneWorkItem, context: LaneExecutionContext, baseline: str
    ) -> str:
        return baseline

    result = await schedule_pair_lanes(
        store,
        run_id="run-scheduler",
        measurement_plan_fingerprint=bundle.plan.measurement_plan_fingerprint,
        run_control=control,
        run_treatment=treatment,
        lane_materializer=FrameworkFilesystemLaneMaterializer(tmp_path / "lanes"),
    )

    assert result.safe_lane_count == 2
    assert peak == 2
    assert len(result.completed) == 4
    assert set(seen) == {1, 2}
    assert seen[1].grant is not None and seen[2].grant is not None
    assert seen[1].grant.workspace_identity != seen[2].grant.workspace_identity
    assert seen[1].grant.browser_profile_identity != seen[2].grant.browser_profile_identity
    assert seen[1].grant.endpoint_namespace_identity != seen[2].grant.endpoint_namespace_identity
    assert seen[1].grant.evidence_directory_identity != seen[2].grant.evidence_directory_identity
    for context in seen.values():
        assert context.lane_attestation is not None
        assert context.lane_materialization_fingerprint == (
            context.lane_attestation.attestation_fingerprint
        )
        assert all(
            Path(claim.declared_identity).is_dir()
            for claim in context.lane_attestation.claims
        )
        assert store.read_lane_materialization_attestation(
            "run-scheduler",
            bundle.plan.measurement_plan_fingerprint,
            context.lane_attestation.attestation_fingerprint,
        ) == context.lane_attestation


@pytest.mark.asyncio
async def test_invalid_control_skips_treatment_without_losing_pair_result(
    tmp_path: Path,
) -> None:
    store, bundle = _bundle(
        tmp_path, decision=_exclusive_decision(), case_count=2
    )
    treatment_cases: list[str] = []

    async def control(item: PairLaneWorkItem, context: LaneExecutionContext) -> bool:
        return item.case_id != "case-1"

    async def treatment(
        item: PairLaneWorkItem, context: LaneExecutionContext, baseline: bool
    ) -> str:
        treatment_cases.append(item.case_id)
        return "ran"

    result = await schedule_pair_lanes(
        store,
        run_id="run-scheduler",
        measurement_plan_fingerprint=bundle.plan.measurement_plan_fingerprint,
        run_control=control,
        run_treatment=treatment,
        lane_materializer=FrameworkFilesystemLaneMaterializer(tmp_path / "lanes"),
        control_allows_treatment=lambda _item, baseline: baseline,
    )

    assert [item.treatment_admitted for item in result.completed] == [False, True]
    assert treatment_cases == ["case-2"]


@pytest.mark.asyncio
async def test_decisive_stop_leaves_unadmitted_pairs_pending(tmp_path: Path) -> None:
    store, bundle = _bundle(
        tmp_path, decision=_exclusive_decision(), case_count=4
    )

    async def control(item: PairLaneWorkItem, context: LaneExecutionContext) -> str:
        return "ok"

    async def treatment(
        item: PairLaneWorkItem, context: LaneExecutionContext, baseline: str
    ) -> str:
        return "negative"

    result = await schedule_pair_lanes(
        store,
        run_id="run-scheduler",
        measurement_plan_fingerprint=bundle.plan.measurement_plan_fingerprint,
        run_control=control,
        run_treatment=treatment,
        lane_materializer=FrameworkFilesystemLaneMaterializer(tmp_path / "lanes"),
        should_stop=lambda completed: (
            "decisive negative effect" if len(completed) == 1 else None
        ),
    )

    assert result.stop_kind is PairLaneStopKind.DECISIVE_STOP
    assert len(result.completed) == 1
    assert len(result.pending) == 3


@pytest.mark.asyncio
async def test_two_lane_decision_does_not_admit_an_extra_pair(tmp_path: Path) -> None:
    store, bundle = _bundle(
        tmp_path, decision=_isolated_decision(tmp_path / "lanes"), case_count=4
    )
    started: list[str] = []

    async def control(item: PairLaneWorkItem, context: LaneExecutionContext) -> str:
        started.append(item.case_id)
        if item.case_id == "case-2":
            await asyncio.sleep(0.03)
        return "ok"

    async def treatment(
        item: PairLaneWorkItem, context: LaneExecutionContext, baseline: str
    ) -> str:
        return "negative"

    result = await schedule_pair_lanes(
        store,
        run_id="run-scheduler",
        measurement_plan_fingerprint=bundle.plan.measurement_plan_fingerprint,
        run_control=control,
        run_treatment=treatment,
        lane_materializer=FrameworkFilesystemLaneMaterializer(tmp_path / "lanes"),
        should_stop=lambda completed: (
            "decisive negative effect" if completed else None
        ),
    )

    assert result.stop_kind is PairLaneStopKind.DECISIVE_STOP
    assert started == ["case-1", "case-2"]
    assert len(result.completed) == 2
    assert [item.case_id for item in result.pending] == ["case-3", "case-4"]


@pytest.mark.asyncio
async def test_checkpoint_quantum_stops_at_pair_boundary(tmp_path: Path) -> None:
    store, bundle = _bundle(
        tmp_path, decision=_exclusive_decision(), case_count=2
    )
    now = 0.0

    def clock() -> float:
        return now

    async def control(item: PairLaneWorkItem, context: LaneExecutionContext) -> str:
        nonlocal now
        now += 3.0
        return "ok"

    async def treatment(
        item: PairLaneWorkItem, context: LaneExecutionContext, baseline: str
    ) -> str:
        raise AssertionError("treatment must not start beyond checkpoint quantum")

    result = await schedule_pair_lanes(
        store,
        run_id="run-scheduler",
        measurement_plan_fingerprint=bundle.plan.measurement_plan_fingerprint,
        run_control=control,
        run_treatment=treatment,
        lane_materializer=FrameworkFilesystemLaneMaterializer(tmp_path / "lanes"),
        checkpoint_quantum_seconds=2.0,
        clock=clock,
    )

    assert result.stop_kind is PairLaneStopKind.CHECKPOINT_QUANTUM
    assert len(result.completed) == 1
    assert result.completed[0].treatment_admitted is False
    assert [item.case_id for item in result.pending] == ["case-1", "case-2"]


@pytest.mark.asyncio
async def test_different_valid_isolation_decision_cannot_replace_plan_proof(
    tmp_path: Path,
) -> None:
    store, bundle = _bundle(
        tmp_path, decision=_exclusive_decision(), case_count=1
    )
    root = store.measurement_control_plan_path(
        "run-scheduler", bundle.plan.measurement_plan_fingerprint
    )
    (root / "isolation_decision.json").write_text(
        json.dumps(_isolated_decision(tmp_path / "lanes").to_dict()), encoding="utf-8"
    )
    calls = 0

    async def control(item: PairLaneWorkItem, context: LaneExecutionContext) -> str:
        nonlocal calls
        calls += 1
        return "unexpected"

    with pytest.raises(ValueError):
        await schedule_pair_lanes(
            store,
            run_id="run-scheduler",
            measurement_plan_fingerprint=bundle.plan.measurement_plan_fingerprint,
            run_control=control,
            run_treatment=lambda _item, _context, _control: asyncio.sleep(
                0, result="unexpected"
            ),
            lane_materializer=FrameworkFilesystemLaneMaterializer(tmp_path / "lanes"),
        )
    assert calls == 0


@pytest.mark.asyncio
async def test_lane_identity_mismatch_fails_before_executor_call(tmp_path: Path) -> None:
    store, bundle = _bundle(
        tmp_path, decision=_isolated_decision(tmp_path / "planned-lanes"), case_count=1
    )
    calls = 0

    async def control(item: PairLaneWorkItem, context: LaneExecutionContext) -> str:
        nonlocal calls
        calls += 1
        return "unexpected"

    with pytest.raises(ValueError, match="escapes materialization root"):
        await schedule_pair_lanes(
            store,
            run_id="run-scheduler",
            measurement_plan_fingerprint=bundle.plan.measurement_plan_fingerprint,
            run_control=control,
            run_treatment=lambda _item, _context, _control: asyncio.sleep(
                0, result="unexpected"
            ),
            lane_materializer=FrameworkFilesystemLaneMaterializer(
                tmp_path / "actual-lanes"
            ),
        )
    assert calls == 0


@pytest.mark.asyncio
async def test_store_verified_control_is_reused_without_execution(tmp_path: Path) -> None:
    store, bundle = _bundle(
        tmp_path, decision=_exclusive_decision(), case_count=1
    )
    control_unit = next(
        unit for unit in bundle.plan.work_units if unit.arm.value == "control"
    )
    journal = MeasurementExecutionJournal(
        store=store,
        run_id="run-scheduler",
        plan=bundle.plan,
    )
    handle = journal.begin(
        work_unit_id=control_unit.work_unit_id,
        attempt_id="attempt-control-1",
        now="2026-08-14T00:00:00Z",
    )
    priming = await schedule_pair_lanes(
        store,
        run_id="run-scheduler",
        measurement_plan_fingerprint=bundle.plan.measurement_plan_fingerprint,
        run_control=lambda _item, _context: asyncio.sleep(0, result="control"),
        run_treatment=lambda _item, _context, _control: asyncio.sleep(
            0, result="treatment"
        ),
        lane_materializer=FrameworkFilesystemLaneMaterializer(tmp_path / "lanes"),
    )
    lane_attestation = priming.completed[0].context.lane_attestation
    assert lane_attestation is not None
    canonical_control = ResolvedControl.from_value("stored-control")
    journal.terminal(
        handle,
        terminal_state=MeasurementWorkUnitState.SUCCEEDED,
        result_fingerprint=canonical_control.result_fingerprint,
        lane_attestation=lane_attestation,
        now="2026-08-14T00:00:01Z",
        attempt_cost_seconds=1.0,
    )
    bundle = load_measurement_schedule_bundle(
        store,
        run_id="run-scheduler",
        measurement_plan_fingerprint=bundle.plan.measurement_plan_fingerprint,
    )
    control_calls = 0

    async def control(item: PairLaneWorkItem, context: LaneExecutionContext) -> str:
        nonlocal control_calls
        control_calls += 1
        return "new"

    async def resolve(observation, context) -> ResolvedControl[str]:
        return ResolvedControl.from_value("stored-control")

    result = await schedule_pair_lanes(
        store,
        run_id="run-scheduler",
        measurement_plan_fingerprint=bundle.plan.measurement_plan_fingerprint,
        run_control=control,
        run_treatment=lambda _item, _context, baseline: asyncio.sleep(
            0, result=baseline
        ),
        lane_materializer=FrameworkFilesystemLaneMaterializer(tmp_path / "lanes"),
        resolve_reused_control=resolve,
    )

    assert control_calls == 0
    assert result.completed[0].treatment == "stored-control"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("configured_lane_limit", "2"),
        ("configured_lane_limit", 1.5),
        ("checkpoint_quantum_seconds", math.nan),
        ("checkpoint_quantum_seconds", math.inf),
        ("campaign_deadline_monotonic", math.nan),
        ("campaign_deadline_monotonic", math.inf),
    ),
)
async def test_invalid_scheduler_numbers_fail_before_execution(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    store, bundle = _bundle(
        tmp_path, decision=_exclusive_decision(), case_count=1
    )
    calls = 0

    async def control(item: PairLaneWorkItem, context: LaneExecutionContext) -> str:
        nonlocal calls
        calls += 1
        return "unexpected"

    with pytest.raises(ValueError):
        await schedule_pair_lanes(
            store,
            run_id="run-scheduler",
            measurement_plan_fingerprint=bundle.plan.measurement_plan_fingerprint,
            run_control=control,
            run_treatment=lambda _item, _context, _control: asyncio.sleep(
                0, result="unexpected"
            ),
            lane_materializer=FrameworkFilesystemLaneMaterializer(tmp_path / "lanes"),
            **{field: value},
        )
    assert calls == 0


def test_caller_cannot_forge_framework_lane_materialization_result() -> None:
    topology = ReplayIsolationTopology.create(
        materializer_id="caller-materializer",
        materializer_fingerprint=_fp("caller-materializer"),
        workspace_identity="/tmp/caller/workspace",
        runtime_identity="/tmp/caller/runtime",
        browser_profile_identity="/tmp/caller/browser",
        endpoint_namespace_identity="/tmp/caller/endpoint",
        evidence_directory_identity="/tmp/caller/evidence",
        cleanup_owner="caller-cleanup",
    )
    with pytest.raises(ValueError, match="framework materializer issued"):
        LaneMaterializationResult(topology=topology)


@pytest.mark.asyncio
async def test_campaign_deadline_bounds_lane_materialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, bundle = _bundle(
        tmp_path, decision=_exclusive_decision(), case_count=1
    )
    materializer = FrameworkFilesystemLaneMaterializer(tmp_path / "lanes")
    calls = 0

    async def slow_materialize(context: LaneExecutionContext):
        await asyncio.sleep(10)

    monkeypatch.setattr(materializer, "materialize", slow_materialize)

    async def control(item: PairLaneWorkItem, context: LaneExecutionContext) -> str:
        nonlocal calls
        calls += 1
        return "unexpected"

    result = await schedule_pair_lanes(
        store,
        run_id="run-scheduler",
        measurement_plan_fingerprint=bundle.plan.measurement_plan_fingerprint,
        run_control=control,
        run_treatment=lambda _item, _context, _control: asyncio.sleep(
            0, result="unexpected"
        ),
        lane_materializer=materializer,
        campaign_deadline_monotonic=time.monotonic() + 0.02,
        materialization_timeout_seconds=10,
    )

    assert result.stop_kind is PairLaneStopKind.CAMPAIGN_DEADLINE
    assert len(result.pending) == 1
    assert calls == 0


@pytest.mark.asyncio
async def test_blocking_materializer_is_killed_at_hard_deadline(
    tmp_path: Path,
) -> None:
    store, bundle = _bundle(
        tmp_path, decision=_exclusive_decision(), case_count=1
    )
    materializer = FrameworkFilesystemLaneMaterializer(
        tmp_path / "lanes", _worker_delay_seconds=0.25
    )
    calls = 0

    async def control(item: PairLaneWorkItem, context: LaneExecutionContext) -> str:
        nonlocal calls
        calls += 1
        return "unexpected"

    started = time.monotonic()
    result = await schedule_pair_lanes(
        store,
        run_id="run-scheduler",
        measurement_plan_fingerprint=bundle.plan.measurement_plan_fingerprint,
        run_control=control,
        run_treatment=lambda _item, _context, _control: asyncio.sleep(
            0, result="unexpected"
        ),
        lane_materializer=materializer,
        campaign_deadline_monotonic=time.monotonic() + 0.03,
        materialization_timeout_seconds=10,
    )

    assert result.stop_kind is PairLaneStopKind.CAMPAIGN_DEADLINE
    assert time.monotonic() - started < 0.20
    assert calls == 0
    assert not (tmp_path / "lanes" / "exclusive-lane-1").exists()


@pytest.mark.asyncio
async def test_pending_work_resumes_after_store_process_restart(
    tmp_path: Path,
) -> None:
    original, bundle = _bundle(
        tmp_path, decision=_exclusive_decision(), case_count=1
    )
    root = original.measurement_control_plan_path(
        "run-scheduler", bundle.plan.measurement_plan_fingerprint
    )
    previous_authority = original._measurement_authority_public_key_fingerprint(root)
    restarted = FilesystemSelfEvolveStore(tmp_path)

    result = await schedule_pair_lanes(
        restarted,
        run_id="run-scheduler",
        measurement_plan_fingerprint=bundle.plan.measurement_plan_fingerprint,
        run_control=lambda _item, _context: asyncio.sleep(0, result="control"),
        run_treatment=lambda _item, _context, _control: asyncio.sleep(
            0, result="treatment"
        ),
        lane_materializer=FrameworkFilesystemLaneMaterializer(tmp_path / "lanes"),
    )

    assert result.stop_kind is PairLaneStopKind.COMPLETED
    assert len(result.completed) == 1
    attestation = result.completed[0].context.lane_attestation
    assert attestation is not None
    assert attestation.authority_public_key_fingerprint != previous_authority
    assert restarted.read_lane_materialization_attestation(
        "run-scheduler",
        bundle.plan.measurement_plan_fingerprint,
        attestation.attestation_fingerprint,
    ) == attestation
