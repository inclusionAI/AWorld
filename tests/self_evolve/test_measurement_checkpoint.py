from __future__ import annotations

import json
from pathlib import Path

from aworld.core.tool.replay_policy import (
    ArtifactPolicy,
    compile_evidence_policy_profile_v2,
)
from aworld.self_evolve.candidate_package import candidate_package_fingerprint
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
from aworld.self_evolve.measurement_checkpoint import (
    discover_measurement_resume_checkpoint,
    discover_paired_replay_resume_checkpoint,
    load_measurement_resume_checkpoint,
    load_paired_replay_resume_checkpoint,
)
from aworld.self_evolve.measurement_control import (
    AdaptiveMeasurementPolicy,
    DeadlinePolicy,
    MeasurementPlanV2,
    SamplingStage,
    SamplingStageKind,
    stable_control_fingerprint,
)
from aworld.self_evolve.replay_adaptation import (
    IsolationDecision,
    IsolationExclusiveFallback,
)
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.runner import (
    _measurement_pending_candidate_checkpoint,
    _paired_replay_pending_candidate_checkpoint,
)
from aworld.self_evolve.types import CandidateVariant, SelfEvolveTargetRef


def _fp(label: str) -> str:
    return stable_control_fingerprint({"label": label})


def _authoritative_fixture(
    tmp_path: Path,
    *,
    run_id: str = "run-authoritative-checkpoint",
    candidate_id: str = "candidate-authoritative",
) -> tuple[
    FilesystemSelfEvolveStore,
    CandidateVariant,
    MeasurementPlanV2,
]:
    store = FilesystemSelfEvolveStore(tmp_path)
    candidate = CandidateVariant(
        candidate_id=candidate_id,
        target=SelfEvolveTargetRef("skill", "demo", "/skills/demo/SKILL.md"),
        content="# Demo\n\nImproved.\n",
        rationale="authoritative checkpoint fixture",
    )
    candidate_fingerprint = candidate_package_fingerprint(candidate)
    experiment = ControlledExperimentSpec.create(
        run_id=run_id,
        mode=MeasurementPolicyMode.REQUIRED,
        swap_axis=SwapAxis.ARTIFACT,
        control=ComponentIdentity("skill:demo", _fp("control")),
        treatment=ComponentIdentity("candidate:demo", candidate_fingerprint),
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
        sampling=SamplingPlan(independent_case_ids=("case-1",)),
        outcomes=OutcomePlan(
            primary_metric="task_success",
            minimum_independent_cases=1,
        ),
        budgets=ExperimentBudget(),
    )
    decision = IsolationDecision.exclusive_fallback(
        requested_lane_count=1,
        fallback=IsolationExclusiveFallback(
            code="binding_requires_exclusive",
            limiting_resource="workspace:fixture",
            detail="test fixture owns one workspace",
        ),
    )
    profile = compile_evidence_policy_profile_v2(
        artifact_policies=(
            ArtifactPolicy(
                artifact_type="browser.snapshot",
                registered_producers=("browser.snapshotter",),
                max_files=2,
                max_items=4,
                max_bytes=1_000_000,
            ),
        )
    )
    plan = MeasurementPlanV2.create(
        experiment_id=experiment.experiment_id,
        plan_revision=1,
        candidate_fingerprint=candidate_fingerprint,
        control_fingerprint=experiment.control.fingerprint or _fp("control"),
        dataset_fingerprint=_fp("dataset"),
        execution_contract_fingerprint=_fp("execution"),
        isolation_decision=decision,
        evidence_policy_profile=profile,
        stages=(
            SamplingStage(
                stage_id="sentinel",
                kind=SamplingStageKind.SENTINEL,
                case_ids=("case-1",),
                minimum_case_count=1,
                batch_size=1,
            ),
        ),
        repetitions_per_case=1,
        deadlines=DeadlinePolicy(
            attempt_timeout_seconds=30,
            member_hard_deadline_seconds=60,
            checkpoint_quantum_seconds=120,
        ),
        decision_policy=AdaptiveMeasurementPolicy(
            minimum_effect=0.0,
            minimum_independent_cases=1,
            maximum_invalid_controls=2,
            zero_yield_window=2,
            require_regression_transfer=False,
        ),
        estimator_version="paired-estimator-v1",
        decision_policy_version="decision-policy-v1",
    )
    store.write_candidate(run_id, candidate)
    store.write_measurement_experiment(experiment)
    store.write_measurement_control_plan(
        run_id,
        plan,
        isolation_decision=decision,
        evidence_policy_profile=profile,
    )

    run_path = store.run_path(run_id)
    replay_dir = run_path / "replay" / candidate_id
    replay_dir.mkdir(parents=True)
    overlay_root = run_path / "overlays" / candidate_id / "skills"
    overlay_root.mkdir(parents=True)
    capability_root = run_path / "replay_adaptation" / "dataset" / "capability"
    workspace_seed = capability_root / "workspace_seed"
    workspace_seed.mkdir(parents=True)
    manifest_path = capability_root / "workspace_manifest.json"
    environment_path = capability_root / "environment_snapshot.json"
    manifest_path.write_text("{}\n", encoding="utf-8")
    environment_path.write_text("{}\n", encoding="utf-8")
    request = {
        "run_id": run_id,
        "candidate_id": candidate_id,
        "measurement_plan": plan.to_dict(),
        "measurement_isolation_decision": decision.to_dict(),
        "measurement_evidence_policy_profile": profile.to_dict(),
        "overlay_skill_root": str(overlay_root),
        "replay_adaptation": {
            "workspace_seed": str(workspace_seed),
            "manifest_path": str(manifest_path),
            "environment_snapshot_path": str(environment_path),
        },
    }
    (replay_dir / "request.json").write_text(
        json.dumps(request, sort_keys=True), encoding="utf-8"
    )
    return store, candidate, plan


def _paired_replay_fixture(
    tmp_path: Path,
) -> tuple[FilesystemSelfEvolveStore, CandidateVariant]:
    run_id = "run-paired-replay-checkpoint"
    candidate = CandidateVariant(
        candidate_id="candidate-paired-replay",
        target=SelfEvolveTargetRef("skill", "demo", "/skills/demo/SKILL.md"),
        content="# Demo\n\nImproved.\n",
        rationale="paired replay checkpoint fixture",
    )
    verified_fingerprint = _fp("verified-paired-package")
    store = FilesystemSelfEvolveStore(tmp_path)
    store.write_candidate(run_id, candidate)
    replay_dir = store.run_path(run_id) / "replay" / candidate.candidate_id
    members_dir = replay_dir / "members"
    members_dir.mkdir(parents=True)
    (replay_dir / "request.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "candidate_id": candidate.candidate_id,
                "verified_candidate_package_fingerprint": verified_fingerprint,
                "measurement_plan": None,
                "repetition_semantics": "per_member_v3",
                "replay_adaptation": {
                    "cases": [
                        {"case_id": "case-complete"},
                        {"case_id": "case-pending"},
                    ]
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (members_dir / "paired_replay_checkpoint.json").write_text(
        json.dumps(
            {
                "schema_version": (
                    "aworld.self_evolve.paired_replay_checkpoint.v1"
                ),
                "schedule": "progressive_paired",
                "resume_safe": True,
                "pending_case_ids": ["case-pending"],
                "comparable_pair_case_ids": ["case-complete"],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return store, candidate


def test_authoritative_checkpoint_round_trips_and_binds_runtime_dependencies(
    tmp_path: Path,
) -> None:
    store, candidate, plan = _authoritative_fixture(tmp_path)
    run_id = "run-authoritative-checkpoint"

    checkpoint = discover_measurement_resume_checkpoint(
        store,
        run_id=run_id,
        candidate_id=candidate.candidate_id,
        candidate_fingerprint=candidate_package_fingerprint(candidate),
    )

    assert checkpoint is not None
    assert checkpoint.stage == "authoritative_replay"
    assert checkpoint.measurement_plan_fingerprint == (
        plan.measurement_plan_fingerprint
    )
    assert f"replay/{candidate.candidate_id}" in checkpoint.protected_paths
    assert any(path.startswith("overlays/") for path in checkpoint.protected_paths)
    assert any(
        path.startswith("replay_adaptation/")
        for path in checkpoint.protected_paths
    )

    report = {"measurement_resume_checkpoint": checkpoint.to_dict()}
    store.write_report(run_id, report)
    assert load_measurement_resume_checkpoint(
        store,
        run_id=run_id,
        report=store.read_report(run_id),
    ) == checkpoint


def test_paired_replay_checkpoint_round_trips_without_claiming_authority(
    tmp_path: Path,
) -> None:
    store, candidate = _paired_replay_fixture(tmp_path)
    run_id = "run-paired-replay-checkpoint"
    fingerprint = candidate_package_fingerprint(candidate)
    verified_fingerprint = _fp("verified-paired-package")

    checkpoint = discover_paired_replay_resume_checkpoint(
        store,
        run_id=run_id,
        candidate_id=candidate.candidate_id,
        verified_candidate_package_fingerprint=verified_fingerprint,
    )

    assert checkpoint is not None
    assert checkpoint.stage == "paired_replay"
    assert checkpoint.candidate_fingerprint == fingerprint
    assert (
        checkpoint.verified_candidate_package_fingerprint
        == verified_fingerprint
    )
    assert checkpoint.pending_case_ids == ("case-pending",)
    assert checkpoint.completed_pair_case_ids == ("case-complete",)
    report = {"paired_replay_resume_checkpoint": checkpoint.to_dict()}
    assert load_paired_replay_resume_checkpoint(
        store,
        run_id=run_id,
        report=report,
    ) == checkpoint
    assert load_measurement_resume_checkpoint(
        store,
        run_id=run_id,
        report=report,
    ) is None

    progress_path = (
        store.run_path(run_id)
        / "replay"
        / candidate.candidate_id
        / "members"
        / "paired_replay_checkpoint.json"
    )
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    progress["resumed_pair_case_ids"] = ["case-complete"]
    progress_path.write_text(json.dumps(progress, sort_keys=True), encoding="utf-8")
    assert load_paired_replay_resume_checkpoint(
        store,
        run_id=run_id,
        report=report,
    ) is None


def test_runner_admits_safe_paired_replay_timeout_checkpoint(
    tmp_path: Path,
) -> None:
    store, candidate = _paired_replay_fixture(tmp_path)
    run_id = "run-paired-replay-checkpoint"
    verified_fingerprint = _fp("verified-paired-package")
    report = {
        "rejection_attribution": {
            "code": "replay_total_timeout",
            "failure_class": "measurement",
            "failure_owner": "framework",
            "failure_scope": "shared_run",
            "repairable": True,
            "resume_safe": True,
            "next_action": "continue_measurement",
            "resume_candidate_id": candidate.candidate_id,
            "resume_candidate_package_fingerprint": verified_fingerprint,
        }
    }

    checkpoint = _paired_replay_pending_candidate_checkpoint(
        store=store,
        run_id=run_id,
        report=report,
    )

    assert checkpoint is not None
    assert checkpoint.candidate_id == candidate.candidate_id


def test_runner_admits_repairable_framework_member_timeout_checkpoint(
    tmp_path: Path,
) -> None:
    store, candidate = _paired_replay_fixture(tmp_path)
    run_id = "run-paired-replay-checkpoint"
    report = {
        "candidate_ids": [candidate.candidate_id],
        "rejection_attribution": {
            "code": "replay_member_phase_timeout",
            "failure_class": "framework",
            "failure_owner": "framework",
            "failure_scope": "member",
            "repairable": True,
        },
    }

    checkpoint = _paired_replay_pending_candidate_checkpoint(
        store=store,
        run_id=run_id,
        report=report,
    )

    assert checkpoint is not None
    assert checkpoint.candidate_id == candidate.candidate_id
    assert checkpoint.pending_case_ids == ("case-pending",)


def test_runner_admits_checkpoint_only_from_authoritative_graph(
    tmp_path: Path,
) -> None:
    store, candidate, _plan = _authoritative_fixture(tmp_path)
    run_id = "run-authoritative-checkpoint"
    report = {
        "candidate_ids": [candidate.candidate_id],
        "selected_candidate_id": candidate.candidate_id,
        "campaign_measurement_outcome": {
            "execution_status": "checkpointed",
            "continuation_available": True,
        },
    }

    checkpoint = _measurement_pending_candidate_checkpoint(
        store=store,
        run_id=run_id,
        report=report,
    )

    assert checkpoint is not None
    assert checkpoint.candidate_id == candidate.candidate_id


def test_screening_namespace_cannot_authorize_measurement_resume(
    tmp_path: Path,
) -> None:
    store, candidate, _plan = _authoritative_fixture(tmp_path)
    run_id = "run-authoritative-checkpoint"
    run_path = store.run_path(run_id)
    authoritative = run_path / "replay" / candidate.candidate_id
    screening = (
        run_path
        / "screening"
        / "case-1"
        / "replay"
        / candidate.candidate_id
    )
    screening.parent.mkdir(parents=True, exist_ok=True)
    authoritative.rename(screening)

    assert discover_measurement_resume_checkpoint(
        store,
        run_id=run_id,
        candidate_id=candidate.candidate_id,
        candidate_fingerprint=candidate_package_fingerprint(candidate),
    ) is None


def test_checkpoint_fails_closed_when_runtime_dependency_was_cleaned(
    tmp_path: Path,
) -> None:
    store, candidate, _plan = _authoritative_fixture(tmp_path)
    run_id = "run-authoritative-checkpoint"
    checkpoint = discover_measurement_resume_checkpoint(
        store,
        run_id=run_id,
        candidate_id=candidate.candidate_id,
        candidate_fingerprint=candidate_package_fingerprint(candidate),
    )
    assert checkpoint is not None
    report = {"measurement_resume_checkpoint": checkpoint.to_dict()}
    store.write_report(run_id, report)

    overlay = store.run_path(run_id) / "overlays" / candidate.candidate_id
    for child in sorted(overlay.rglob("*"), reverse=True):
        child.rmdir() if child.is_dir() else child.unlink()
    overlay.rmdir()

    assert load_measurement_resume_checkpoint(
        store,
        run_id=run_id,
        report=store.read_report(run_id),
    ) is None


def test_checkpoint_rejects_request_contract_substitution(tmp_path: Path) -> None:
    store, candidate, _plan = _authoritative_fixture(tmp_path)
    run_id = "run-authoritative-checkpoint"
    request_path = (
        store.run_path(run_id)
        / "replay"
        / candidate.candidate_id
        / "request.json"
    )
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request["measurement_isolation_decision"]["safe_lane_count"] = 99
    request_path.write_text(json.dumps(request, sort_keys=True), encoding="utf-8")

    assert discover_measurement_resume_checkpoint(
        store,
        run_id=run_id,
        candidate_id=candidate.candidate_id,
        candidate_fingerprint=candidate_package_fingerprint(candidate),
    ) is None
