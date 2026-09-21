from __future__ import annotations

import json
import hashlib
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from aworld.core.tool.replay_policy import (
    ArtifactPolicy,
    EvidencePolicyProfileV2,
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
    MeasurementControlCorruptionError,
    MeasurementControlEventKind,
    MeasurementControlIndex,
    MeasurementControlObservationRecord,
    MeasurementPlanV2,
    LaneMaterializationAttestationV1,
    LaneMaterializationClaim,
    MeasurementWorkUnitState,
    SamplingStage,
    SamplingStageKind,
    WorkUnitJournalEvent,
    classify_work_unit_reuse,
    describe_legacy_measurement_control,
    stable_control_fingerprint,
)
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.replay_adaptation import (
    IsolationDecision,
    IsolationExclusiveFallback,
    ReplayIsolationTopology,
)


def _fp(label: str) -> str:
    return stable_control_fingerprint({"label": label})


def test_screening_control_preflight_cache_round_trips(tmp_path: Path) -> None:
    store = FilesystemSelfEvolveStore(tmp_path)
    payload = {
        "schema_version": "aworld.self_evolve.screening_control_preflight.v1",
        "status": "feasible",
        "case_count": 1,
        "feasible_case_ids": ["case-1"],
        "infeasible_case_ids": [],
        "unknown_case_ids": [],
        "candidate_generation_allowed": True,
        "source": "historical_baseline_lifecycle",
        "case_observations": {"case-1": {"baseline_success_count": 1}},
    }

    path = store.write_screening_control_preflight("run-preflight", payload)

    assert path.name == "control_preflight.json"
    assert store.read_screening_control_preflight("run-preflight") == payload
    with pytest.raises(ValueError, match="unsupported screening"):
        store.write_screening_control_preflight(
            "run-preflight",
            {**payload, "schema_version": "wrong"},
        )


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


def _isolation_decision() -> IsolationDecision:
    return IsolationDecision.exclusive_fallback(
        requested_lane_count=1,
        fallback=IsolationExclusiveFallback(
            code="binding_requires_exclusive",
            limiting_resource="workspace:fixture",
            detail="test fixture uses one exclusive workspace",
        ),
    )


def _experiment(candidate_label: str = "candidate") -> ControlledExperimentSpec:
    return ControlledExperimentSpec.create(
        run_id="run-measurement-control",
        mode=MeasurementPolicyMode.SHADOW,
        swap_axis=SwapAxis.ARTIFACT,
        control=ComponentIdentity("skill:demo", _fp("control")),
        treatment=ComponentIdentity("candidate:demo", _fp(candidate_label)),
        frozen_identities=FrozenIdentities(
            task_model=_fp("task-model"),
            generator=_fp("generator"),
            scheduler=_fp("scheduler"),
            evaluator=_fp("evaluator"),
            dataset=_fp("dataset"),
            environment=_fp("environment"),
            runtime=_fp("runtime"),
            prompt_context=_fp("prompt-context"),
            budget=_fp("budget"),
        ),
        sampling=SamplingPlan(independent_case_ids=("case-1", "case-2")),
        outcomes=OutcomePlan(
            primary_metric="task_success",
            minimum_independent_cases=2,
        ),
        budgets=ExperimentBudget(),
    )


def _plan(
    experiment: ControlledExperimentSpec,
    *,
    revision: int = 1,
    candidate_fingerprint: str | None = None,
    evidence_policy_profile: EvidencePolicyProfileV2 | None = None,
) -> MeasurementPlanV2:
    return MeasurementPlanV2.create(
        experiment_id=experiment.experiment_id,
        plan_revision=revision,
        candidate_fingerprint=(
            candidate_fingerprint
            or experiment.treatment.fingerprint
            or _fp("candidate")
        ),
        control_fingerprint=experiment.control.fingerprint or _fp("control"),
        dataset_fingerprint=_fp("dataset"),
        execution_contract_fingerprint=_fp("execution"),
        isolation_decision=_isolation_decision(),
        evidence_policy_profile=evidence_policy_profile or _evidence_profile(),
        stages=(
            SamplingStage(
                stage_id="sentinel",
                kind=SamplingStageKind.SENTINEL,
                case_ids=("case-1", "case-2"),
                minimum_case_count=2,
                batch_size=2,
            ),
        ),
        repetitions_per_case=1,
        deadlines=DeadlinePolicy(
            attempt_timeout_seconds=30,
            member_hard_deadline_seconds=600,
            checkpoint_quantum_seconds=900,
        ),
        decision_policy=AdaptiveMeasurementPolicy(
            minimum_effect=0.1,
            minimum_independent_cases=2,
            maximum_invalid_controls=2,
            zero_yield_window=2,
            require_regression_transfer=False,
        ),
        estimator_version="paired-estimator-v1",
        decision_policy_version="decision-policy-v1",
    )


def _store_with_plan(tmp_path: Path) -> tuple[
    FilesystemSelfEvolveStore,
    ControlledExperimentSpec,
    MeasurementPlanV2,
]:
    store = FilesystemSelfEvolveStore(tmp_path)
    experiment = _experiment()
    plan = _plan(experiment)
    store.write_measurement_experiment(experiment)
    store.write_measurement_control_plan(
        experiment.run_id,
        plan,
        isolation_decision=_isolation_decision(),
        evidence_policy_profile=_evidence_profile(),
    )
    return store, experiment, plan


def _event(
    plan: MeasurementPlanV2,
    *,
    unit_index: int = 0,
    kind: MeasurementControlEventKind,
    previous_state: MeasurementWorkUnitState,
    new_state: MeasurementWorkUnitState,
    occurred_at: str,
    attempt_id: str = "attempt-1",
    lease_expires_at: str | None = None,
    observation_fingerprint: str | None = None,
    attempt_cost_seconds: float = 0.0,
    reason_code: str | None = None,
) -> WorkUnitJournalEvent:
    return WorkUnitJournalEvent.create(
        measurement_plan_fingerprint=plan.measurement_plan_fingerprint,
        work_unit_id=plan.work_units[unit_index].work_unit_id,
        kind=kind,
        previous_state=previous_state,
        new_state=new_state,
        occurred_at=occurred_at,
        attempt_id=attempt_id,
        lease_expires_at=lease_expires_at,
        observation_fingerprint=observation_fingerprint,
        attempt_cost_seconds=attempt_cost_seconds,
        reason_code=reason_code,
    )


def _observation(
    store: FilesystemSelfEvolveStore,
    plan: MeasurementPlanV2,
    *,
    unit_index: int = 0,
    terminal_state: MeasurementWorkUnitState,
    label: str,
    recorded_at: str,
) -> MeasurementControlObservationRecord:
    marker_payload = stable_control_fingerprint(
        {
            "measurement_plan_fingerprint": plan.measurement_plan_fingerprint,
            "isolation_decision_fingerprint": plan.isolation_decision_fingerprint,
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
        path = store.workspace_root / "test-lane" / dimension
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
    decision, profile = store.read_measurement_control_contracts(
        "run-measurement-control", plan.measurement_plan_fingerprint
    )
    writer = issue_framework_evidence_writer_attestation_v2(
        profile,
        writer_identity="measurement-writer-lane-1",
        isolation_identity=decision.fingerprint,
        resource_identity=stable_control_fingerprint(
            {"topology": topology.to_dict(), "bindings": []}
        ),
    )
    attestation = store._issue_lane_materialization_attestation(
        "run-measurement-control",
        plan.measurement_plan_fingerprint,
        lane_id=1,
        isolation_grant_fingerprint=None,
        topology=topology,
        binding_fingerprints=(),
        writer_attestation=writer,
        claims=claims,
        recorded_at=recorded_at,
    )
    return MeasurementControlObservationRecord.create(
        plan=plan,
        work_unit_id=plan.work_units[unit_index].work_unit_id,
        terminal_state=terminal_state,
        result_fingerprint=_fp(label),
        isolation_grant_fingerprint=None,
        lane_materialization_fingerprint=attestation.attestation_fingerprint,
        recorded_at=recorded_at,
    )


def test_store_round_trips_immutable_plan_and_initial_index(tmp_path: Path) -> None:
    store, experiment, plan = _store_with_plan(tmp_path)

    stored_decision, stored_profile = store.read_measurement_control_contracts(
        experiment.run_id, plan.measurement_plan_fingerprint
    )
    assert stored_decision == _isolation_decision()
    assert stored_profile == _evidence_profile()
    assert store.read_measurement_control_plan(
        experiment.run_id, plan.measurement_plan_fingerprint
    ) == plan
    assert store.read_measurement_control_journal(
        experiment.run_id, plan.measurement_plan_fingerprint
    ) == ()
    index = store.read_measurement_control_index(
        experiment.run_id, plan.measurement_plan_fingerprint
    )
    assert index.event_count == 0
    assert index.observation_count == 0
    assert {entry.state for entry in index.work_units} == {
        MeasurementWorkUnitState.PENDING
    }


@pytest.mark.parametrize("artifact_name", ("isolation_decision.json", "evidence_policy_profile.json"))
def test_store_resume_fails_closed_on_canonical_contract_artifact_drift(
    tmp_path: Path,
    artifact_name: str,
) -> None:
    store, experiment, plan = _store_with_plan(tmp_path)
    root = store.measurement_control_plan_path(
        experiment.run_id, plan.measurement_plan_fingerprint
    )
    replacement = (
        IsolationDecision.exclusive_fallback(
            requested_lane_count=1,
            fallback=IsolationExclusiveFallback(
                code="binding_requires_exclusive",
                limiting_resource="runtime:changed",
                detail="different canonical execution decision",
            ),
        ).to_dict()
        if artifact_name == "isolation_decision.json"
        else _evidence_profile("changed").to_dict()
    )
    (root / artifact_name).write_text(json.dumps(replacement), encoding="utf-8")

    with pytest.raises(ValueError, match="artifact drifted|fingerprint"):
        store.read_measurement_control_index(
            experiment.run_id, plan.measurement_plan_fingerprint
        )


def test_store_rejects_immutable_plan_content_conflict(tmp_path: Path) -> None:
    store, experiment, plan = _store_with_plan(tmp_path)
    plan_path = store.measurement_control_plan_path(
        experiment.run_id, plan.measurement_plan_fingerprint
    ) / "plan.json"
    plan_path.write_text(plan_path.read_text().replace("paired-estimator-v1", "bad"))

    with pytest.raises(ValueError, match="immutable measurement plan"):
        store.write_measurement_control_plan(
            experiment.run_id,
            plan,
            isolation_decision=_isolation_decision(),
            evidence_policy_profile=_evidence_profile(),
        )


def test_contract_bundle_publish_is_atomic_on_sidecar_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FilesystemSelfEvolveStore(tmp_path)
    experiment = _experiment()
    plan = _plan(experiment)
    store.write_measurement_experiment(experiment)
    root = store.measurement_control_plan_path(
        experiment.run_id, plan.measurement_plan_fingerprint
    )
    original_write = store._write_json_atomic

    def fail_evidence_sidecar(path: Path, payload) -> None:
        if path.name == "evidence_policy_profile.json":
            raise OSError("injected sidecar crash")
        original_write(path, payload)

    monkeypatch.setattr(store, "_write_json_atomic", fail_evidence_sidecar)
    with pytest.raises(OSError, match="sidecar crash"):
        store.write_measurement_control_plan(
            experiment.run_id,
            plan,
            isolation_decision=_isolation_decision(),
            evidence_policy_profile=_evidence_profile(),
        )
    assert not root.exists()


def test_journal_transitions_and_terminal_accounting_round_trip(
    tmp_path: Path,
) -> None:
    store, experiment, plan = _store_with_plan(tmp_path)
    leased = _event(
        plan,
        kind=MeasurementControlEventKind.LEASE_ACQUIRED,
        previous_state=MeasurementWorkUnitState.PENDING,
        new_state=MeasurementWorkUnitState.LEASED,
        occurred_at="2026-08-14T01:00:00Z",
        lease_expires_at="2026-08-14T01:05:00Z",
    )
    started = _event(
        plan,
        kind=MeasurementControlEventKind.EXECUTION_STARTED,
        previous_state=MeasurementWorkUnitState.LEASED,
        new_state=MeasurementWorkUnitState.RUNNING,
        occurred_at="2026-08-14T01:00:01Z",
        lease_expires_at="2026-08-14T01:05:00Z",
    )
    observation = _observation(
        store,
        plan,
        terminal_state=MeasurementWorkUnitState.SUCCEEDED,
        label="observation",
        recorded_at="2026-08-14T01:00:05Z",
    )
    store.write_measurement_control_observation(
        experiment.run_id, plan.measurement_plan_fingerprint, observation
    )
    completed = _event(
        plan,
        kind=MeasurementControlEventKind.TERMINAL_RECORDED,
        previous_state=MeasurementWorkUnitState.RUNNING,
        new_state=MeasurementWorkUnitState.SUCCEEDED,
        occurred_at="2026-08-14T01:00:05Z",
        observation_fingerprint=observation.observation_fingerprint,
        attempt_cost_seconds=4.0,
    )

    for event in (leased, started, completed):
        store.append_measurement_control_event(
            experiment.run_id, plan.measurement_plan_fingerprint, event
        )

    assert store.read_measurement_control_journal(
        experiment.run_id, plan.measurement_plan_fingerprint
    ) == (leased, started, completed)
    index = store.read_measurement_control_index(
        experiment.run_id, plan.measurement_plan_fingerprint
    )
    entry = index.entry(plan.work_units[0].work_unit_id)
    assert entry.state is MeasurementWorkUnitState.SUCCEEDED
    assert entry.attempt_count == 1
    assert entry.observation_fingerprint == observation.observation_fingerprint
    assert index.observation_count == 1
    assert index.actual_attempt_cost_seconds == 4.0


def test_duplicate_terminal_delivery_is_physically_and_logically_idempotent(
    tmp_path: Path,
) -> None:
    store, experiment, plan = _store_with_plan(tmp_path)
    leased = _event(
        plan,
        kind=MeasurementControlEventKind.LEASE_ACQUIRED,
        previous_state=MeasurementWorkUnitState.PENDING,
        new_state=MeasurementWorkUnitState.LEASED,
        occurred_at="2026-08-14T01:00:00Z",
        lease_expires_at="2026-08-14T01:05:00Z",
    )
    observation = _observation(
        store,
        plan,
        terminal_state=MeasurementWorkUnitState.TASK_FAILED,
        label="failure-observation",
        recorded_at="2026-08-14T01:00:05Z",
    )
    store.write_measurement_control_observation(
        experiment.run_id, plan.measurement_plan_fingerprint, observation
    )
    completed = _event(
        plan,
        kind=MeasurementControlEventKind.TERMINAL_RECORDED,
        previous_state=MeasurementWorkUnitState.LEASED,
        new_state=MeasurementWorkUnitState.TASK_FAILED,
        occurred_at="2026-08-14T01:00:05Z",
        observation_fingerprint=observation.observation_fingerprint,
        attempt_cost_seconds=5.0,
    )
    store.append_measurement_control_event(
        experiment.run_id, plan.measurement_plan_fingerprint, leased
    )
    store.append_measurement_control_event(
        experiment.run_id, plan.measurement_plan_fingerprint, completed
    )

    store.append_measurement_control_event(
        experiment.run_id, plan.measurement_plan_fingerprint, completed
    )

    journal = store.read_measurement_control_journal(
        experiment.run_id, plan.measurement_plan_fingerprint
    )
    index = store.read_measurement_control_index(
        experiment.run_id, plan.measurement_plan_fingerprint
    )
    assert journal == (leased, completed)
    assert index.event_count == 2
    assert index.observation_count == 1
    assert index.actual_attempt_cost_seconds == 5.0


def test_checkpointed_attempt_cost_is_preserved_across_resume(tmp_path: Path) -> None:
    store, experiment, plan = _store_with_plan(tmp_path)
    observation = _observation(
        store,
        plan,
        terminal_state=MeasurementWorkUnitState.SUCCEEDED,
        label="resumed-observation",
        recorded_at="2026-08-14T01:03:00Z",
    )
    store.write_measurement_control_observation(
        experiment.run_id, plan.measurement_plan_fingerprint, observation
    )
    events = (
        _event(
            plan,
            kind=MeasurementControlEventKind.LEASE_ACQUIRED,
            previous_state=MeasurementWorkUnitState.PENDING,
            new_state=MeasurementWorkUnitState.LEASED,
            occurred_at="2026-08-14T01:00:00Z",
            lease_expires_at="2026-08-14T01:05:00Z",
        ),
        _event(
            plan,
            kind=MeasurementControlEventKind.CHECKPOINT_RECORDED,
            previous_state=MeasurementWorkUnitState.LEASED,
            new_state=MeasurementWorkUnitState.CHECKPOINTED,
            occurred_at="2026-08-14T01:01:00Z",
            attempt_cost_seconds=2.0,
        ),
        _event(
            plan,
            kind=MeasurementControlEventKind.LEASE_ACQUIRED,
            previous_state=MeasurementWorkUnitState.CHECKPOINTED,
            new_state=MeasurementWorkUnitState.LEASED,
            occurred_at="2026-08-14T01:02:00Z",
            lease_expires_at="2026-08-14T01:07:00Z",
            attempt_id="attempt-2",
        ),
        _event(
            plan,
            kind=MeasurementControlEventKind.TERMINAL_RECORDED,
            previous_state=MeasurementWorkUnitState.LEASED,
            new_state=MeasurementWorkUnitState.SUCCEEDED,
            occurred_at="2026-08-14T01:03:00Z",
            attempt_id="attempt-2",
            observation_fingerprint=observation.observation_fingerprint,
            attempt_cost_seconds=3.0,
        ),
    )
    for event in events:
        store.append_measurement_control_event(
            experiment.run_id, plan.measurement_plan_fingerprint, event
        )

    index = store.read_measurement_control_index(
        experiment.run_id, plan.measurement_plan_fingerprint
    )
    assert index.entry(plan.work_units[0].work_unit_id).attempt_count == 2
    assert index.actual_attempt_cost_seconds == 5.0


def test_conflicting_transition_after_terminal_fails_closed(tmp_path: Path) -> None:
    store, experiment, plan = _store_with_plan(tmp_path)
    leased = _event(
        plan,
        kind=MeasurementControlEventKind.LEASE_ACQUIRED,
        previous_state=MeasurementWorkUnitState.PENDING,
        new_state=MeasurementWorkUnitState.LEASED,
        occurred_at="2026-08-14T01:00:00Z",
        lease_expires_at="2026-08-14T01:05:00Z",
    )
    observation = _observation(
        store,
        plan,
        terminal_state=MeasurementWorkUnitState.SUCCEEDED,
        label="observation",
        recorded_at="2026-08-14T01:00:05Z",
    )
    store.write_measurement_control_observation(
        experiment.run_id, plan.measurement_plan_fingerprint, observation
    )
    completed = _event(
        plan,
        kind=MeasurementControlEventKind.TERMINAL_RECORDED,
        previous_state=MeasurementWorkUnitState.LEASED,
        new_state=MeasurementWorkUnitState.SUCCEEDED,
        occurred_at="2026-08-14T01:00:05Z",
        observation_fingerprint=observation.observation_fingerprint,
    )
    store.append_measurement_control_event(
        experiment.run_id, plan.measurement_plan_fingerprint, leased
    )
    store.append_measurement_control_event(
        experiment.run_id, plan.measurement_plan_fingerprint, completed
    )

    with pytest.raises(MeasurementControlCorruptionError, match="terminal"):
        store.append_measurement_control_event(
            experiment.run_id,
            plan.measurement_plan_fingerprint,
            _event(
                plan,
                kind=MeasurementControlEventKind.LEASE_ACQUIRED,
                previous_state=MeasurementWorkUnitState.CHECKPOINTED,
                new_state=MeasurementWorkUnitState.LEASED,
                occurred_at="2026-08-14T01:00:06Z",
                lease_expires_at="2026-08-14T01:05:00Z",
            ),
        )


def test_read_recovers_valid_journal_tail_written_before_index_replace(
    tmp_path: Path,
) -> None:
    store, experiment, plan = _store_with_plan(tmp_path)
    event = _event(
        plan,
        kind=MeasurementControlEventKind.LEASE_ACQUIRED,
        previous_state=MeasurementWorkUnitState.PENDING,
        new_state=MeasurementWorkUnitState.LEASED,
        occurred_at="2026-08-14T01:00:00Z",
        lease_expires_at="2026-08-14T01:05:00Z",
    )
    journal_path = store.measurement_control_plan_path(
        experiment.run_id, plan.measurement_plan_fingerprint
    ) / "journal.jsonl"
    with journal_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event.to_dict(), sort_keys=True) + "\n")

    recovered = store.read_measurement_control_index(
        experiment.run_id, plan.measurement_plan_fingerprint
    )

    assert recovered.event_count == 1
    assert recovered.entry(plan.work_units[0].work_unit_id).state is (
        MeasurementWorkUnitState.LEASED
    )


def test_index_confirmed_journal_prefix_corruption_fails_closed(
    tmp_path: Path,
) -> None:
    store, experiment, plan = _store_with_plan(tmp_path)
    event = _event(
        plan,
        kind=MeasurementControlEventKind.LEASE_ACQUIRED,
        previous_state=MeasurementWorkUnitState.PENDING,
        new_state=MeasurementWorkUnitState.LEASED,
        occurred_at="2026-08-14T01:00:00Z",
        lease_expires_at="2026-08-14T01:05:00Z",
    )
    store.append_measurement_control_event(
        experiment.run_id, plan.measurement_plan_fingerprint, event
    )
    journal_path = store.measurement_control_plan_path(
        experiment.run_id, plan.measurement_plan_fingerprint
    ) / "journal.jsonl"
    journal_path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(MeasurementControlCorruptionError) as error:
        store.read_measurement_control_index(
            experiment.run_id, plan.measurement_plan_fingerprint
        )
    assert error.value.owner == "measurement"
    assert error.value.reason_code == "measurement_journal_prefix_corrupt"


def test_event_plan_identity_drift_fails_closed(tmp_path: Path) -> None:
    store, experiment, plan = _store_with_plan(tmp_path)
    changed_profile = _evidence_profile("new-policy")
    amended = _plan(
        experiment,
        revision=2,
        evidence_policy_profile=changed_profile,
    )
    drifted_event = _event(
        amended,
        kind=MeasurementControlEventKind.LEASE_ACQUIRED,
        previous_state=MeasurementWorkUnitState.PENDING,
        new_state=MeasurementWorkUnitState.LEASED,
        occurred_at="2026-08-14T01:00:00Z",
        lease_expires_at="2026-08-14T01:05:00Z",
    )

    with pytest.raises(ValueError, match="different measurement plan"):
        store.append_measurement_control_event(
            experiment.run_id, plan.measurement_plan_fingerprint, drifted_event
        )
    decision = classify_work_unit_reuse(
        expected_plan=amended,
        stored_plan=plan,
        stored_entry=store.read_measurement_control_index(
            experiment.run_id, plan.measurement_plan_fingerprint
        ).entry(plan.work_units[0].work_unit_id),
    )
    assert decision.compatible is False
    assert decision.reason_code == "evidence_policy_fingerprint_changed"


def test_terminal_control_is_reusable_across_candidate_amendment(
    tmp_path: Path,
) -> None:
    store, experiment, plan = _store_with_plan(tmp_path)
    leased = _event(
        plan,
        kind=MeasurementControlEventKind.LEASE_ACQUIRED,
        previous_state=MeasurementWorkUnitState.PENDING,
        new_state=MeasurementWorkUnitState.LEASED,
        occurred_at="2026-08-14T01:00:00Z",
        lease_expires_at="2026-08-14T01:05:00Z",
    )
    observation = _observation(
        store,
        plan,
        terminal_state=MeasurementWorkUnitState.SUCCEEDED,
        label="control-observation",
        recorded_at="2026-08-14T01:00:05Z",
    )
    store.write_measurement_control_observation(
        experiment.run_id, plan.measurement_plan_fingerprint, observation
    )
    completed = _event(
        plan,
        kind=MeasurementControlEventKind.TERMINAL_RECORDED,
        previous_state=MeasurementWorkUnitState.LEASED,
        new_state=MeasurementWorkUnitState.SUCCEEDED,
        occurred_at="2026-08-14T01:00:05Z",
        observation_fingerprint=observation.observation_fingerprint,
    )
    for event in (leased, completed):
        store.append_measurement_control_event(
            experiment.run_id, plan.measurement_plan_fingerprint, event
        )
    amended = _plan(
        experiment,
        revision=2,
        candidate_fingerprint=_fp("candidate-v2"),
    )

    decision = classify_work_unit_reuse(
        expected_plan=amended,
        stored_plan=plan,
        stored_entry=store.read_measurement_control_index(
            experiment.run_id, plan.measurement_plan_fingerprint
        ).entry(plan.work_units[0].work_unit_id),
    )

    assert decision.compatible is True
    assert decision.observation_fingerprint == observation.observation_fingerprint


def test_expired_lease_recovery_checkpoints_only_expired_units(
    tmp_path: Path,
) -> None:
    store, experiment, plan = _store_with_plan(tmp_path)
    expired = _event(
        plan,
        unit_index=0,
        kind=MeasurementControlEventKind.LEASE_ACQUIRED,
        previous_state=MeasurementWorkUnitState.PENDING,
        new_state=MeasurementWorkUnitState.LEASED,
        occurred_at="2026-08-14T01:00:00Z",
        lease_expires_at="2026-08-14T01:05:00Z",
        attempt_id="attempt-expired",
    )
    live = _event(
        plan,
        unit_index=2,
        kind=MeasurementControlEventKind.LEASE_ACQUIRED,
        previous_state=MeasurementWorkUnitState.PENDING,
        new_state=MeasurementWorkUnitState.LEASED,
        occurred_at="2026-08-14T01:00:00Z",
        lease_expires_at="2026-08-14T02:00:00Z",
        attempt_id="attempt-live",
    )
    for event in (expired, live):
        store.append_measurement_control_event(
            experiment.run_id, plan.measurement_plan_fingerprint, event
        )

    recovered_ids = store.recover_expired_measurement_control_leases(
        experiment.run_id,
        plan.measurement_plan_fingerprint,
        now="2026-08-14T01:30:00Z",
    )

    index = store.read_measurement_control_index(
        experiment.run_id, plan.measurement_plan_fingerprint
    )
    assert recovered_ids == (plan.work_units[0].work_unit_id,)
    assert index.entry(plan.work_units[0].work_unit_id).state is (
        MeasurementWorkUnitState.CHECKPOINTED
    )
    assert index.entry(plan.work_units[2].work_unit_id).state is (
        MeasurementWorkUnitState.LEASED
    )
    assert (
        index.entry(plan.work_units[0].work_unit_id).actual_attempt_cost_seconds
        == plan.deadlines.member_hard_deadline_seconds
    )


def test_store_rejects_symlinked_measurement_control_root(tmp_path: Path) -> None:
    store = FilesystemSelfEvolveStore(tmp_path)
    experiment = _experiment()
    plan = _plan(experiment)
    store.write_measurement_experiment(experiment)
    run_root = store.run_path(experiment.run_id)
    outside = tmp_path / "outside-control"
    outside.mkdir()
    (run_root / "measurement_control").symlink_to(
        outside, target_is_directory=True
    )

    with pytest.raises(ValueError, match="symlink"):
        store.write_measurement_control_plan(
            experiment.run_id,
            plan,
            isolation_decision=_isolation_decision(),
            evidence_policy_profile=_evidence_profile(),
        )


def test_cross_experiment_control_reuse_uses_typed_baseline_mapping(
    tmp_path: Path,
) -> None:
    store, experiment, plan = _store_with_plan(tmp_path)
    observation = _observation(
        store,
        plan,
        terminal_state=MeasurementWorkUnitState.SUCCEEDED,
        label="cross-experiment-control",
        recorded_at="2026-08-14T01:00:05Z",
    )
    store.write_measurement_control_observation(
        experiment.run_id, plan.measurement_plan_fingerprint, observation
    )
    for event in (
        _event(
            plan,
            kind=MeasurementControlEventKind.LEASE_ACQUIRED,
            previous_state=MeasurementWorkUnitState.PENDING,
            new_state=MeasurementWorkUnitState.LEASED,
            occurred_at="2026-08-14T01:00:00Z",
            lease_expires_at="2026-08-14T01:05:00Z",
        ),
        _event(
            plan,
            kind=MeasurementControlEventKind.TERMINAL_RECORDED,
            previous_state=MeasurementWorkUnitState.LEASED,
            new_state=MeasurementWorkUnitState.SUCCEEDED,
            occurred_at="2026-08-14T01:00:05Z",
            observation_fingerprint=observation.observation_fingerprint,
        ),
    ):
        store.append_measurement_control_event(
            experiment.run_id, plan.measurement_plan_fingerprint, event
        )
    next_experiment = _experiment("candidate-next-experiment")
    next_plan = _plan(next_experiment)
    store.write_measurement_experiment(next_experiment)
    store.write_measurement_control_plan(
        next_experiment.run_id,
        next_plan,
        isolation_decision=_isolation_decision(),
        evidence_policy_profile=_evidence_profile(),
    )

    assert plan.work_units[0].work_unit_id != next_plan.work_units[0].work_unit_id
    assert (
        plan.work_units[0].baseline_compatibility_key
        == next_plan.work_units[0].baseline_compatibility_key
    )
    resolved = store.resolve_compatible_measurement_control_observation(
        experiment.run_id,
        expected_plan=next_plan,
        stored_plan_fingerprint=plan.measurement_plan_fingerprint,
        stored_work_unit_id=plan.work_units[0].work_unit_id,
    )
    assert resolved == observation


def test_terminal_event_requires_existing_content_addressed_observation(
    tmp_path: Path,
) -> None:
    store, experiment, plan = _store_with_plan(tmp_path)
    store.append_measurement_control_event(
        experiment.run_id,
        plan.measurement_plan_fingerprint,
        _event(
            plan,
            kind=MeasurementControlEventKind.LEASE_ACQUIRED,
            previous_state=MeasurementWorkUnitState.PENDING,
            new_state=MeasurementWorkUnitState.LEASED,
            occurred_at="2026-08-14T01:00:00Z",
            lease_expires_at="2026-08-14T01:05:00Z",
        ),
    )
    with pytest.raises(MeasurementControlCorruptionError) as error:
        store.append_measurement_control_event(
            experiment.run_id,
            plan.measurement_plan_fingerprint,
            _event(
                plan,
                kind=MeasurementControlEventKind.TERMINAL_RECORDED,
                previous_state=MeasurementWorkUnitState.LEASED,
                new_state=MeasurementWorkUnitState.SUCCEEDED,
                occurred_at="2026-08-14T01:00:05Z",
                observation_fingerprint=_fp("not-persisted"),
            ),
        )
    assert error.value.reason_code == "measurement_observation_missing"


def test_tampered_observation_payload_cannot_authorize_terminal_state(
    tmp_path: Path,
) -> None:
    store, experiment, plan = _store_with_plan(tmp_path)
    observation = _observation(
        store,
        plan,
        terminal_state=MeasurementWorkUnitState.SUCCEEDED,
        label="authentic-result",
        recorded_at="2026-08-14T01:00:05Z",
    )
    path = store.write_measurement_control_observation(
        experiment.run_id, plan.measurement_plan_fingerprint, observation
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["result_fingerprint"] = _fp("tampered-result")
    path.write_text(json.dumps(payload), encoding="utf-8")
    store.append_measurement_control_event(
        experiment.run_id,
        plan.measurement_plan_fingerprint,
        _event(
            plan,
            kind=MeasurementControlEventKind.LEASE_ACQUIRED,
            previous_state=MeasurementWorkUnitState.PENDING,
            new_state=MeasurementWorkUnitState.LEASED,
            occurred_at="2026-08-14T01:00:00Z",
            lease_expires_at="2026-08-14T01:05:00Z",
        ),
    )
    with pytest.raises(MeasurementControlCorruptionError) as error:
        store.append_measurement_control_event(
            experiment.run_id,
            plan.measurement_plan_fingerprint,
            _event(
                plan,
                kind=MeasurementControlEventKind.TERMINAL_RECORDED,
                previous_state=MeasurementWorkUnitState.LEASED,
                new_state=MeasurementWorkUnitState.SUCCEEDED,
                occurred_at="2026-08-14T01:00:05Z",
                observation_fingerprint=observation.observation_fingerprint,
            ),
        )
    assert error.value.reason_code == "measurement_observation_invalid"


def test_torn_unconfirmed_tail_is_quarantined_without_state_promotion(
    tmp_path: Path,
) -> None:
    store, experiment, plan = _store_with_plan(tmp_path)
    event = _event(
        plan,
        kind=MeasurementControlEventKind.LEASE_ACQUIRED,
        previous_state=MeasurementWorkUnitState.PENDING,
        new_state=MeasurementWorkUnitState.LEASED,
        occurred_at="2026-08-14T01:00:00Z",
        lease_expires_at="2026-08-14T01:05:00Z",
    )
    store.append_measurement_control_event(
        experiment.run_id, plan.measurement_plan_fingerprint, event
    )
    root = store.measurement_control_plan_path(
        experiment.run_id, plan.measurement_plan_fingerprint
    )
    with (root / "journal.jsonl").open("ab") as handle:
        handle.write(b'{"schema_version":')

    recovered = store.read_measurement_control_index(
        experiment.run_id, plan.measurement_plan_fingerprint
    )

    assert recovered.event_count == 1
    assert (root / "journal.jsonl").stat().st_size == recovered.journal_byte_count
    assert len(tuple((root / "quarantine").glob("torn-tail-*.bin"))) == 1


def test_finalized_attempt_id_cannot_be_released_or_charged_twice(
    tmp_path: Path,
) -> None:
    store, experiment, plan = _store_with_plan(tmp_path)
    for event in (
        _event(
            plan,
            kind=MeasurementControlEventKind.LEASE_ACQUIRED,
            previous_state=MeasurementWorkUnitState.PENDING,
            new_state=MeasurementWorkUnitState.LEASED,
            occurred_at="2026-08-14T01:00:00Z",
            lease_expires_at="2026-08-14T01:05:00Z",
        ),
        _event(
            plan,
            kind=MeasurementControlEventKind.CHECKPOINT_RECORDED,
            previous_state=MeasurementWorkUnitState.LEASED,
            new_state=MeasurementWorkUnitState.CHECKPOINTED,
            occurred_at="2026-08-14T01:01:00Z",
            attempt_cost_seconds=60,
        ),
    ):
        store.append_measurement_control_event(
            experiment.run_id, plan.measurement_plan_fingerprint, event
        )
    with pytest.raises(MeasurementControlCorruptionError) as error:
        store.append_measurement_control_event(
            experiment.run_id,
            plan.measurement_plan_fingerprint,
            _event(
                plan,
                kind=MeasurementControlEventKind.LEASE_ACQUIRED,
                previous_state=MeasurementWorkUnitState.CHECKPOINTED,
                new_state=MeasurementWorkUnitState.LEASED,
                occurred_at="2026-08-14T01:02:00Z",
                lease_expires_at="2026-08-14T01:07:00Z",
                attempt_id="attempt-1",
            ),
        )
    assert error.value.reason_code == "measurement_attempt_already_finalized"


def test_verified_compaction_preserves_ledger_and_allows_incremental_append(
    tmp_path: Path,
) -> None:
    store, experiment, plan = _store_with_plan(tmp_path)
    for event in (
        _event(
            plan,
            kind=MeasurementControlEventKind.LEASE_ACQUIRED,
            previous_state=MeasurementWorkUnitState.PENDING,
            new_state=MeasurementWorkUnitState.LEASED,
            occurred_at="2026-08-14T01:00:00Z",
            lease_expires_at="2026-08-14T01:05:00Z",
        ),
        _event(
            plan,
            kind=MeasurementControlEventKind.CHECKPOINT_RECORDED,
            previous_state=MeasurementWorkUnitState.LEASED,
            new_state=MeasurementWorkUnitState.CHECKPOINTED,
            occurred_at="2026-08-14T01:01:00Z",
            attempt_cost_seconds=60,
        ),
    ):
        store.append_measurement_control_event(
            experiment.run_id, plan.measurement_plan_fingerprint, event
        )
    snapshot_path = store.compact_measurement_control_journal(
        experiment.run_id, plan.measurement_plan_fingerprint
    )
    store.append_measurement_control_event(
        experiment.run_id,
        plan.measurement_plan_fingerprint,
        _event(
            plan,
            kind=MeasurementControlEventKind.LEASE_ACQUIRED,
            previous_state=MeasurementWorkUnitState.CHECKPOINTED,
            new_state=MeasurementWorkUnitState.LEASED,
            occurred_at="2026-08-14T01:02:00Z",
            lease_expires_at="2026-08-14T01:07:00Z",
            attempt_id="attempt-2",
        ),
    )

    index = store.read_measurement_control_index(
        experiment.run_id, plan.measurement_plan_fingerprint
    )
    assert snapshot_path.is_file()
    assert index.compacted_event_count == 2
    assert index.event_count == 3
    assert index.actual_attempt_cost_seconds == 60


def test_legacy_measurement_reader_is_descriptive_and_fails_closed(
    tmp_path: Path,
) -> None:
    description = describe_legacy_measurement_control(
        {
            "schema_version": "legacy.v0",
            "experiment_id": "experiment-old",
            "event_count": 4,
            "trusted_reuse_allowed": True,
        }
    )
    assert description.declared_event_count == 4
    assert description.trusted_reuse_allowed is False
    assert description.reason_code == "legacy_identity_incomplete"
    store = FilesystemSelfEvolveStore(tmp_path)
    legacy_path = store.run_path("run-legacy") / "legacy" / "checkpoint.json"
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_text(
        json.dumps(
            {
                "schema_version": "legacy.v0",
                "experiment_id": "experiment-old",
                "event_count": 4,
                "trusted_reuse_allowed": True,
            }
        ),
        encoding="utf-8",
    )
    loaded = store.read_legacy_measurement_control_description(
        "run-legacy", "legacy/checkpoint.json"
    )
    assert loaded == description
    with pytest.raises(ValueError, match="safe relative path"):
        store.read_legacy_measurement_control_description(
            "run-legacy", "../checkpoint.json"
        )


def _checkpoint_one_attempt(
    store: FilesystemSelfEvolveStore,
    experiment: ControlledExperimentSpec,
    plan: MeasurementPlanV2,
) -> None:
    for event in (
        _event(
            plan,
            kind=MeasurementControlEventKind.LEASE_ACQUIRED,
            previous_state=MeasurementWorkUnitState.PENDING,
            new_state=MeasurementWorkUnitState.LEASED,
            occurred_at="2026-08-14T01:00:00Z",
            lease_expires_at="2026-08-14T01:05:00Z",
        ),
        _event(
            plan,
            kind=MeasurementControlEventKind.CHECKPOINT_RECORDED,
            previous_state=MeasurementWorkUnitState.LEASED,
            new_state=MeasurementWorkUnitState.CHECKPOINTED,
            occurred_at="2026-08-14T01:01:00Z",
            attempt_cost_seconds=60,
        ),
    ):
        store.append_measurement_control_event(
            experiment.run_id, plan.measurement_plan_fingerprint, event
        )


def test_plan_recomputes_canonical_identity_and_store_leaves_no_poisoned_root(
    tmp_path: Path,
) -> None:
    experiment = _experiment()
    plan = _plan(experiment)
    with pytest.raises(ValueError, match="canonical contract|fingerprint"):
        replace(plan, estimator_version="tampered-estimator")

    store = FilesystemSelfEvolveStore(tmp_path)
    store.write_measurement_experiment(experiment)
    poisoned_root = store.measurement_control_plan_path(
        experiment.run_id, plan.measurement_plan_fingerprint
    )
    object.__setattr__(plan, "estimator_version", "tampered-estimator")
    with pytest.raises(ValueError, match="canonical validation"):
        store.write_measurement_control_plan(
            experiment.run_id,
            plan,
            isolation_decision=_isolation_decision(),
            evidence_policy_profile=_evidence_profile(),
        )
    assert not poisoned_root.exists()


@pytest.mark.parametrize("publish_target_before_failure", (False, True))
def test_compaction_recovers_before_and_after_index_authority_switch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    publish_target_before_failure: bool,
) -> None:
    store, experiment, plan = _store_with_plan(tmp_path)
    _checkpoint_one_attempt(store, experiment, plan)
    root = store.measurement_control_plan_path(
        experiment.run_id, plan.measurement_plan_fingerprint
    )
    source_index = store.read_measurement_control_index(
        experiment.run_id, plan.measurement_plan_fingerprint
    )
    original_write = store._write_json_atomic

    def fail_index_publish(path: Path, payload) -> None:
        if path == root / "index.json" and (root / "compaction.json").exists():
            if publish_target_before_failure:
                original_write(path, payload)
            raise OSError("injected compaction crash")
        original_write(path, payload)

    monkeypatch.setattr(store, "_write_json_atomic", fail_index_publish)
    with pytest.raises(MeasurementControlCorruptionError) as error:
        store.compact_measurement_control_journal(
            experiment.run_id, plan.measurement_plan_fingerprint
        )
    assert error.value.reason_code == "measurement_compaction_recovery_failed"
    assert (root / "compaction.json").is_file()
    if not publish_target_before_failure:
        persisted = MeasurementControlIndex.from_dict(
            json.loads((root / "index.json").read_text(encoding="utf-8"))
        )
        assert persisted.index_fingerprint == source_index.index_fingerprint
        assert (root / source_index.journal_file).is_file()

    monkeypatch.setattr(store, "_write_json_atomic", original_write)
    recovered = store.read_measurement_control_index(
        experiment.run_id, plan.measurement_plan_fingerprint
    )
    assert recovered.event_count == source_index.event_count
    assert recovered.compacted_event_count == source_index.event_count
    assert recovered.journal_file != source_index.journal_file
    assert not (root / source_index.journal_file).exists()
    assert not (root / "compaction.json").exists()


def test_compaction_crash_before_intent_keeps_old_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, experiment, plan = _store_with_plan(tmp_path)
    _checkpoint_one_attempt(store, experiment, plan)
    root = store.measurement_control_plan_path(
        experiment.run_id, plan.measurement_plan_fingerprint
    )
    source = store.read_measurement_control_index(
        experiment.run_id, plan.measurement_plan_fingerprint
    )
    original_write = store._write_json_atomic

    def fail_intent(path: Path, payload) -> None:
        if path == root / "compaction.json":
            raise OSError("injected prepare crash")
        original_write(path, payload)

    monkeypatch.setattr(store, "_write_json_atomic", fail_intent)
    with pytest.raises(OSError, match="prepare crash"):
        store.compact_measurement_control_journal(
            experiment.run_id, plan.measurement_plan_fingerprint
        )
    monkeypatch.setattr(store, "_write_json_atomic", original_write)
    recovered = store.read_measurement_control_index(
        experiment.run_id, plan.measurement_plan_fingerprint
    )
    assert recovered == source
    assert (root / source.journal_file).is_file()
    assert not (root / "compaction.json").exists()


def test_compaction_crash_after_old_journal_cleanup_finishes_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, experiment, plan = _store_with_plan(tmp_path)
    _checkpoint_one_attempt(store, experiment, plan)
    root = store.measurement_control_plan_path(
        experiment.run_id, plan.measurement_plan_fingerprint
    )
    source = store.read_measurement_control_index(
        experiment.run_id, plan.measurement_plan_fingerprint
    )
    intent_path = root / "compaction.json"
    original_unlink = Path.unlink

    def fail_intent_unlink(path: Path, *args, **kwargs) -> None:
        if path == intent_path:
            raise OSError("injected cleanup crash")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_intent_unlink)
    with pytest.raises(OSError, match="cleanup crash"):
        store.compact_measurement_control_journal(
            experiment.run_id, plan.measurement_plan_fingerprint
        )
    assert intent_path.is_file()
    assert not (root / source.journal_file).exists()
    monkeypatch.setattr(Path, "unlink", original_unlink)
    recovered = store.read_measurement_control_index(
        experiment.run_id, plan.measurement_plan_fingerprint
    )
    assert recovered.compacted_event_count == source.event_count
    assert not intent_path.exists()


def test_oversized_journal_fails_before_payload_read_and_records_metadata(
    tmp_path: Path,
) -> None:
    from aworld.self_evolve import store as store_module

    store, experiment, plan = _store_with_plan(tmp_path)
    root = store.measurement_control_plan_path(
        experiment.run_id, plan.measurement_plan_fingerprint
    )
    journal_path = root / "journal.jsonl"
    with journal_path.open("r+b") as stream:
        stream.truncate(store_module._MEASUREMENT_JOURNAL_MAX_BYTES + 1)

    with pytest.raises(MeasurementControlCorruptionError) as error:
        store.read_measurement_control_journal(
            experiment.run_id, plan.measurement_plan_fingerprint
        )
    assert error.value.reason_code == "measurement_journal_oversized"
    metadata_path = root / "quarantine" / "oversized-journal.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["content_copied"] is False
    assert metadata["observed_bytes"] == (
        store_module._MEASUREMENT_JOURNAL_MAX_BYTES + 1
    )
    assert metadata_path.stat().st_size < 2_048


def test_large_torn_tail_quarantines_metadata_without_copying_payload(
    tmp_path: Path,
) -> None:
    from aworld.self_evolve import store as store_module

    store, experiment, plan = _store_with_plan(tmp_path)
    root = store.measurement_control_plan_path(
        experiment.run_id, plan.measurement_plan_fingerprint
    )
    torn = b"x" * (store_module._MEASUREMENT_JOURNAL_MAX_EVENT_BYTES + 1)
    (root / "journal.jsonl").write_bytes(torn)

    recovered = store.read_measurement_control_index(
        experiment.run_id, plan.measurement_plan_fingerprint
    )

    assert recovered.event_count == 0
    assert (root / "journal.jsonl").stat().st_size == 0
    metadata = tuple((root / "quarantine").glob("torn-tail-*.json"))
    assert len(metadata) == 1
    payload = json.loads(metadata[0].read_text(encoding="utf-8"))
    assert payload["content_copied"] is False
    assert payload["torn_tail_bytes"] == len(torn)
    assert not tuple((root / "quarantine").glob("torn-tail-*.bin"))


def test_public_journal_reader_uses_same_flock_as_append(tmp_path: Path) -> None:
    store, experiment, plan = _store_with_plan(tmp_path)
    root = store.measurement_control_plan_path(
        experiment.run_id, plan.measurement_plan_fingerprint
    )
    entered = threading.Event()
    finished = threading.Event()
    result: list[tuple[WorkUnitJournalEvent, ...]] = []

    def read_journal() -> None:
        entered.set()
        result.append(
            store.read_measurement_control_journal(
                experiment.run_id, plan.measurement_plan_fingerprint
            )
        )
        finished.set()

    with store._measurement_control_append_lock(root):
        thread = threading.Thread(target=read_journal)
        thread.start()
        assert entered.wait(timeout=1)
        assert not finished.wait(timeout=0.1)
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert result == [()]


def test_observation_rejects_missing_lane_attestation(tmp_path: Path) -> None:
    store, experiment, plan = _store_with_plan(tmp_path)
    observation = MeasurementControlObservationRecord.create(
        plan=plan,
        work_unit_id=plan.work_units[0].work_unit_id,
        terminal_state=MeasurementWorkUnitState.SUCCEEDED,
        result_fingerprint=_fp("result"),
        isolation_grant_fingerprint=None,
        lane_materialization_fingerprint=_fp("missing-lane-attestation"),
        recorded_at="2026-08-14T00:00:01Z",
    )

    with pytest.raises(
        MeasurementControlCorruptionError,
        match="lane materialization attestation is missing",
    ):
        store.write_measurement_control_observation(
            experiment.run_id,
            plan.measurement_plan_fingerprint,
            observation,
        )


def test_lane_attestation_tamper_invalidates_observation_reuse(
    tmp_path: Path,
) -> None:
    store, experiment, plan = _store_with_plan(tmp_path)
    observation = _observation(
        store,
        plan,
        terminal_state=MeasurementWorkUnitState.SUCCEEDED,
        label="result",
        recorded_at="2026-08-14T00:00:01Z",
    )
    store.write_measurement_control_observation(
        experiment.run_id, plan.measurement_plan_fingerprint, observation
    )
    digest = observation.lane_materialization_fingerprint.removeprefix("sha256:")
    path = (
        store.measurement_control_plan_path(
            experiment.run_id, plan.measurement_plan_fingerprint
        )
        / "lane-attestations"
        / f"attestation-{digest}.json"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["lane_id"] = 2
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        MeasurementControlCorruptionError,
        match="attestation failed content verification",
    ):
        store.read_measurement_control_observation(
            experiment.run_id,
            plan.measurement_plan_fingerprint,
            observation.observation_fingerprint,
        )


def test_caller_signed_lane_attestation_is_rejected(tmp_path: Path) -> None:
    store, experiment, plan = _store_with_plan(tmp_path)
    observation = _observation(
        store,
        plan,
        terminal_state=MeasurementWorkUnitState.SUCCEEDED,
        label="result",
        recorded_at="2026-08-14T00:00:01Z",
    )
    issued = store.read_lane_materialization_attestation(
        experiment.run_id,
        plan.measurement_plan_fingerprint,
        observation.lane_materialization_fingerprint,
    )
    forged = LaneMaterializationAttestationV1.create(
        measurement_plan_fingerprint=issued.measurement_plan_fingerprint,
        isolation_decision_fingerprint=issued.isolation_decision_fingerprint,
        evidence_policy_fingerprint=issued.evidence_policy_fingerprint,
        lane_id=issued.lane_id,
        isolation_grant_fingerprint=issued.isolation_grant_fingerprint,
        topology_fingerprint=issued.topology_fingerprint,
        topology=json.loads(issued.topology_json),
        writer_attestation_fingerprint=issued.writer_attestation_fingerprint,
        writer_attestation=json.loads(issued.writer_attestation_json),
        claims=issued.claims,
        recorded_at=issued.recorded_at,
        authority_public_key_fingerprint=(
            issued.authority_public_key_fingerprint
        ),
        authority_signature="0" * 128,
    )

    with pytest.raises(
        MeasurementControlCorruptionError,
        match="not signed by the measurement store",
    ):
        store.write_lane_materialization_attestation(
            experiment.run_id,
            plan.measurement_plan_fingerprint,
            forged,
        )


def test_lane_signer_rotates_on_restart_and_old_proof_remains_verifiable(
    tmp_path: Path,
) -> None:
    store, experiment, plan = _store_with_plan(tmp_path)
    observation = _observation(
        store,
        plan,
        terminal_state=MeasurementWorkUnitState.SUCCEEDED,
        label="result",
        recorded_at="2026-08-14T00:00:01Z",
    )
    root = store.measurement_control_plan_path(
        experiment.run_id, plan.measurement_plan_fingerprint
    )
    assert (root / "authority.pub").read_bytes()
    assert not (root / "authority.key").exists()
    assert not hasattr(store, "issue_lane_materialization_attestation")

    restarted = FilesystemSelfEvolveStore(tmp_path)
    reloaded = restarted.read_lane_materialization_attestation(
        experiment.run_id,
        plan.measurement_plan_fingerprint,
        observation.lane_materialization_fingerprint,
    )
    assert reloaded.attestation_fingerprint == (
        observation.lane_materialization_fingerprint
    )
    old_authority = reloaded.authority_public_key_fingerprint
    restarted._measurement_authority_private_key(root)
    new_authority = restarted._measurement_authority_public_key_fingerprint(root)
    assert new_authority != old_authority
    assert restarted.read_lane_materialization_attestation(
        experiment.run_id,
        plan.measurement_plan_fingerprint,
        observation.lane_materialization_fingerprint,
    ) == reloaded
