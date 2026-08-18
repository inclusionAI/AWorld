from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from aworld.core.tool.replay_policy import (
    ArtifactPolicy,
    compile_evidence_policy_profile_v2,
    issue_framework_evidence_writer_attestation_v2,
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
    LaneMaterializationAttestationV1,
    LaneMaterializationClaim,
    MeasurementPlanV2,
    MeasurementWorkUnitState,
    SamplingStage,
    SamplingStageKind,
    stable_control_fingerprint,
)
from aworld.self_evolve.measurement_execution import MeasurementExecutionJournal
from aworld.self_evolve.replay_adaptation import (
    IsolationDecision,
    IsolationExclusiveFallback,
    ReplayIsolationTopology,
)
from aworld.self_evolve.store import FilesystemSelfEvolveStore


def _fp(label: str) -> str:
    return stable_control_fingerprint({"label": label})


def _journal(tmp_path: Path) -> MeasurementExecutionJournal:
    run_id = "run-execution-journal"
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
        sampling=SamplingPlan(independent_case_ids=("case-1",)),
        outcomes=OutcomePlan(primary_metric="task_success", minimum_independent_cases=1),
        budgets=ExperimentBudget(),
    )
    plan = MeasurementPlanV2.create(
        experiment_id=experiment.experiment_id,
        plan_revision=1,
        candidate_fingerprint=experiment.treatment.fingerprint or _fp("candidate"),
        control_fingerprint=experiment.control.fingerprint or _fp("control"),
        dataset_fingerprint=_fp("dataset"),
        execution_contract_fingerprint=_fp("execution"),
        isolation_decision=IsolationDecision.exclusive_fallback(
            requested_lane_count=1,
            fallback=IsolationExclusiveFallback(
                code="binding_requires_exclusive",
                limiting_resource="workspace:fixture",
                detail="fixture uses one exclusive workspace",
            ),
        ),
        evidence_policy_profile=compile_evidence_policy_profile_v2(
            artifact_policies=(
                ArtifactPolicy(
                    artifact_type="browser.snapshot",
                    registered_producers=("browser.snapshotter",),
                    max_files=1,
                    max_items=1,
                    max_bytes=1_000_000,
                ),
            ),
        ),
        stages=(
            SamplingStage(
                stage_id="sentinel",
                kind=SamplingStageKind.SENTINEL,
                case_ids=("case-1",),
                minimum_case_count=1,
            ),
        ),
        repetitions_per_case=1,
        deadlines=DeadlinePolicy(
            attempt_timeout_seconds=5,
            member_hard_deadline_seconds=10,
            checkpoint_quantum_seconds=20,
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
        isolation_decision=IsolationDecision.exclusive_fallback(
            requested_lane_count=1,
            fallback=IsolationExclusiveFallback(
                code="binding_requires_exclusive",
                limiting_resource="workspace:fixture",
                detail="fixture uses one exclusive workspace",
            ),
        ),
        evidence_policy_profile=compile_evidence_policy_profile_v2(
            artifact_policies=(
                ArtifactPolicy(
                    artifact_type="browser.snapshot",
                    registered_producers=("browser.snapshotter",),
                    max_files=1,
                    max_items=1,
                    max_bytes=1_000_000,
                ),
            ),
        ),
    )
    return MeasurementExecutionJournal(store=store, run_id=run_id, plan=plan)


def _lane_attestation(
    journal: MeasurementExecutionJournal, tmp_path: Path
) -> LaneMaterializationAttestationV1:
    marker_payload = stable_control_fingerprint(
        {
            "measurement_plan_fingerprint": journal.plan.measurement_plan_fingerprint,
            "isolation_decision_fingerprint": journal.plan.isolation_decision_fingerprint,
            "lane_id": 1,
            "cleanup_owner": "framework-lane-1",
        }
    )
    marker_bytes = (marker_payload + "\n").encode("ascii")
    claims: list[LaneMaterializationClaim] = []
    for dimension in (
        "workspace_root",
        "runtime_root",
        "browser_profile",
        "endpoint_namespace",
        "evidence_directory",
    ):
        path = tmp_path / "lane" / dimension
        path.mkdir(parents=True, exist_ok=True)
        (path / ".aworld-lane-owner").write_bytes(marker_bytes)
        stat = path.stat()
        claims.append(
            LaneMaterializationClaim(
                dimension=dimension,
                declared_identity=str(path.absolute()),
                observed_device=stat.st_dev,
                observed_inode=stat.st_ino,
                ownership_marker_fingerprint=(
                    "sha256:" + hashlib.sha256(marker_bytes).hexdigest()
                ),
            )
        )
    claim_paths = {item.dimension: item.declared_identity for item in claims}
    topology = ReplayIsolationTopology.create(
        materializer_id="framework-filesystem-lane-materializer",
        materializer_fingerprint=stable_control_fingerprint(
            {
                "schema_version": "aworld.framework_lane_materializer.v1",
                "kind": "filesystem",
            }
        ),
        workspace_identity=claim_paths["workspace_root"],
        runtime_identity=claim_paths["runtime_root"],
        browser_profile_identity=claim_paths["browser_profile"],
        endpoint_namespace_identity=claim_paths["endpoint_namespace"],
        evidence_directory_identity=claim_paths["evidence_directory"],
        cleanup_owner="framework-lane-1",
    )
    decision, profile = journal._store.read_measurement_control_contracts(
        journal._run_id, journal.plan.measurement_plan_fingerprint
    )
    writer = issue_framework_evidence_writer_attestation_v2(
        profile,
        writer_identity="measurement-writer-lane-1",
        isolation_identity=decision.fingerprint,
        resource_identity=stable_control_fingerprint(
            {"topology": topology.to_dict(), "bindings": []}
        ),
    )
    return journal._store._issue_lane_materialization_attestation(
        journal._run_id,
        journal.plan.measurement_plan_fingerprint,
        lane_id=1,
        isolation_grant_fingerprint=None,
        topology=topology,
        binding_fingerprints=(),
        writer_attestation=writer,
        claims=claims,
        recorded_at="2026-08-14T00:00:00Z",
    )


def test_execution_journal_records_exactly_once_terminal_observation(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    unit = journal.plan.work_units[0]
    handle = journal.begin(
        work_unit_id=unit.work_unit_id,
        attempt_id="attempt-control-1",
        now="2026-08-14T00:00:00Z",
    )
    observation = journal.terminal(
        handle,
        terminal_state=MeasurementWorkUnitState.SUCCEEDED,
        result_fingerprint=_fp("result"),
        lane_attestation=_lane_attestation(journal, tmp_path),
        now="2026-08-14T00:00:04Z",
        attempt_cost_seconds=4.0,
    )

    terminal = journal.terminal_observations()
    assert terminal == {unit.work_unit_id: observation}
    entry = journal.index_entries()[0]
    assert entry.actual_attempt_cost_seconds == 4.0
    with pytest.raises(ValueError, match="does not own"):
        journal.terminal(
            handle,
            terminal_state=MeasurementWorkUnitState.SUCCEEDED,
            result_fingerprint=_fp("result"),
            lane_attestation=_lane_attestation(journal, tmp_path),
            now="2026-08-14T00:00:04Z",
            attempt_cost_seconds=4.0,
        )


def test_infrastructure_retry_reopens_only_timed_out_unit(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    control = journal.plan.work_units[0]
    handle = journal.begin(
        work_unit_id=control.work_unit_id,
        attempt_id="attempt-timeout-1",
        now="2026-08-14T00:00:00Z",
    )
    journal.terminal(
        handle,
        terminal_state=MeasurementWorkUnitState.MEMBER_TIMED_OUT,
        result_fingerprint=_fp("timeout-result"),
        lane_attestation=_lane_attestation(journal, tmp_path),
        now="2026-08-14T00:00:10Z",
        attempt_cost_seconds=10.0,
        reason_code="replay_member_failed",
    )

    scheduled = journal.schedule_infrastructure_retries(
        now="2026-08-14T00:00:11Z",
        maximum_attempts=2,
    )

    assert scheduled == (control.work_unit_id,)
    entry = journal.index_entries()[0]
    assert entry.state is MeasurementWorkUnitState.CHECKPOINTED
    assert entry.attempt_count == 1
    assert entry.actual_attempt_cost_seconds == 10.0
    assert entry.observation_fingerprint is None
    assert journal.terminal_observations() == {}
    assert journal.schedule_infrastructure_retries(
        now="2026-08-14T00:00:12Z",
        maximum_attempts=2,
    ) == ()


def test_checkpoint_is_resumable_and_attempt_cost_is_not_lost(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    unit = journal.plan.work_units[0]
    first = journal.begin(
        work_unit_id=unit.work_unit_id,
        attempt_id="attempt-control-1",
        now="2026-08-14T00:00:00Z",
    )
    journal.checkpoint(
        first,
        now="2026-08-14T00:00:03Z",
        attempt_cost_seconds=3.0,
        reason_code="checkpoint_quantum",
    )
    second = journal.begin(
        work_unit_id=unit.work_unit_id,
        attempt_id="attempt-control-2",
        now="2026-08-14T00:01:00Z",
    )
    journal.terminal(
        second,
        terminal_state=MeasurementWorkUnitState.SUCCEEDED,
        result_fingerprint=_fp("resumed-result"),
        lane_attestation=_lane_attestation(journal, tmp_path),
        now="2026-08-14T00:01:02Z",
        attempt_cost_seconds=2.0,
    )

    entry = journal.index_entries()[0]
    assert entry.attempt_count == 2
    assert entry.actual_attempt_cost_seconds == 5.0
    assert [attempt.attempt_id for attempt in entry.finalized_attempts] == [
        "attempt-control-1",
        "attempt-control-2",
    ]


def test_expired_running_lease_becomes_checkpoint_not_failure(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    unit = journal.plan.work_units[0]
    journal.begin(
        work_unit_id=unit.work_unit_id,
        attempt_id="attempt-control-1",
        now="2026-08-14T00:00:00Z",
    )

    recovered = journal.recover_expired(now="2026-08-14T00:00:11Z")

    assert recovered == (unit.work_unit_id,)
    entry = journal.index_entries()[0]
    assert entry.state is MeasurementWorkUnitState.CHECKPOINTED
    assert entry.actual_attempt_cost_seconds == 10.0
