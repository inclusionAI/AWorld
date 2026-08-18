from __future__ import annotations

import json
from pathlib import Path
import pytest

from aworld.self_evolve.datasets import EvalCase, SelfEvolveDataset
from aworld.self_evolve.measurement import (
    ComponentIdentity,
    ControlledExperimentSpec,
    ExperimentBudget,
    FrozenIdentities,
    MeasurementPolicyMode,
    OutcomePlan,
    SamplingPlan,
    SwapAxis,
    TransferPanel,
    TransferPanelRole,
    stable_measurement_fingerprint,
)
from aworld.self_evolve.measurement_control import (
    CaseVisibilityRole,
    DeadlinePolicy,
    SamplingStageKind,
)
from aworld.self_evolve.measurement_planner import (
    compile_measurement_plan_v2,
    persist_compiled_measurement_plan,
)
from aworld.self_evolve.replay import (
    AWorldCliCandidateReplayBackend,
    CandidateReplayRequest,
    ReplayExecutionRequest,
    ReplayExecutionResult,
    ReplayVariantResult,
    _load_measurement_result_projection,
    _persist_measurement_result_projection,
    _persist_variant_lifecycle,
    build_replay_request,
    compile_replay_evidence_policy_profile_v2,
    normalize_replay_members,
    replay_dataset_fingerprint,
)
from aworld.self_evolve.failure_events import (
    FailureOwner,
    FailureScope,
    FailureStage,
    ReplayFailureEvent,
)
from aworld.self_evolve.replay_adaptation import (
    IsolationDecision,
    IsolationExclusiveFallback,
    ReplayAdaptationBundle,
    ReplayCaseAdaptation,
)
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.runner import (
    SelfEvolveRunner,
    _campaign_measurement_outcome_for_replay,
    _effective_cli_measurement_mode,
)
from aworld.self_evolve.targets import SkillTextTarget
from aworld.self_evolve.types import (
    CandidateVariant,
    DatasetRecipe,
    SelfEvolveTargetRef,
    SelfEvolveRunStatus,
    to_json_dict,
)


def _fp(label: str) -> str:
    return stable_measurement_fingerprint({"label": label})


def test_measurement_result_projection_content_addresses_large_replay_artifacts(
    tmp_path: Path,
) -> None:
    artifact_dir = tmp_path / "member" / "baseline" / "1"
    failure = ReplayFailureEvent(
        code="baseline_task_failed",
        owner=FailureOwner.TASK,
        stage=FailureStage.TASK_ROLLOUT,
        scope=FailureScope.MEMBER,
        repairable=False,
        diagnostics={"large_detail": "d" * 200_000},
    )
    result = ReplayVariantResult(
        variant_id="baseline",
        status="failed",
        trajectory=[{"content": "x" * 1_500_000}],
        metrics={"large_metric": "m" * 1_500_000},
        failure=failure,
    )
    _persist_variant_lifecycle(artifact_dir, result)

    resolved = _persist_measurement_result_projection(
        artifact_dir,
        result=result,
    )

    assert len(resolved.content_bytes) < 32_000
    assert resolved.value["status"] == "failed"
    assert resolved.value["failure"]["code"] == "baseline_task_failed"
    assert {
        item["name"] for item in resolved.value["artifact_references"]
    } >= {"lifecycle.json", "trajectory.json", "metrics.json", "failure.json"}
    assert _load_measurement_result_projection(artifact_dir) == resolved

    (artifact_dir / "trajectory.json").write_text("[]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="content drifted"):
        _load_measurement_result_projection(artifact_dir)


@pytest.mark.parametrize(
    ("configured", "apply_policy", "replay_enabled", "expected"),
    (
        (None, "verified_only", True, MeasurementPolicyMode.REQUIRED),
        (None, "auto_verified", True, MeasurementPolicyMode.REQUIRED),
        ("off", "proposal", True, MeasurementPolicyMode.OFF),
        ("off", "verified_only", True, MeasurementPolicyMode.OFF),
        ("off", "verified_only", False, MeasurementPolicyMode.OFF),
        ("shadow", "verified_only", True, MeasurementPolicyMode.SHADOW),
    ),
)
def test_verified_replay_defaults_to_authoritative_measurement_v2(
    configured: str | None,
    apply_policy: str,
    replay_enabled: bool,
    expected: MeasurementPolicyMode,
) -> None:
    assert _effective_cli_measurement_mode(
        configured,
        apply_policy=apply_policy,
        replay_enabled=replay_enabled,
    ) is expected


def test_runner_atomically_compiles_v2_plan_for_advisory_replay(
    tmp_path: Path,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n", encoding="utf-8")
    target = SkillTextTarget(skill_path)
    case_ids = tuple(f"case-{index}" for index in range(1, 6))
    dataset = SelfEvolveDataset(
        cases=tuple(
            EvalCase(case_id=case_id, input=f"run {case_id}")
            for case_id in case_ids
        ),
        recipe=DatasetRecipe(
            source={"kind": "runner-v2-integration"},
            split_seed="runner-v2",
            splits={
                "train": list(case_ids[:2]),
                "validation": [],
                "held_out": list(case_ids[2:]),
            },
            trainable_case_ids=case_ids[:2],
            held_out_case_ids=case_ids[2:],
        ),
    )
    candidate = CandidateVariant(
        candidate_id="runner-candidate-v2",
        target=target.identity,
        content="---\nname: demo\n---\n# Demo\nImproved.\n",
        rationale="runner integration fixture",
        target_fingerprint=target.fingerprint_current_content(),
    )

    class Optimizer:
        async def propose(self, _request):  # pragma: no cover - not invoked
            raise AssertionError("optimizer is outside this integration seam")

    backend = AWorldCliCandidateReplayBackend()
    store = FilesystemSelfEvolveStore(tmp_path)
    runner = SelfEvolveRunner(
        store=store,
        optimizer=Optimizer(),
        replay_enabled=True,
        candidate_replay_backend=backend,
        measurement_mode=MeasurementPolicyMode.ADVISORY,
        measurement_primary_metric="score",
        measurement_minimum_effect=0.1,
        measurement_min_independent_cases=2,
    )
    experiment = runner._plan_candidate_measurement(
        run_id="run-runner-v2",
        target=target,
        dataset=dataset,
        candidate=candidate,
        candidate_count=1,
    )
    assert experiment is not None
    bundle = runner._compile_authoritative_measurement_plan(
        run_id="run-runner-v2",
        dataset=dataset,
        candidate=candidate,
        replay_adaptation=ReplayAdaptationBundle(
            schema_version="test",
            source_workspace_root=str(tmp_path),
            workspace_seed=str(tmp_path / "seed"),
            workspace_seed_fingerprint=_fp("workspace-seed"),
            manifest_path=str(tmp_path / "manifest.json"),
            environment_snapshot_path=str(tmp_path / "environment.json"),
            environment_fingerprint=_fp("environment"),
            cases=tuple(
                ReplayCaseAdaptation(
                    case_id=case_id,
                    adapted_task_input=f"run {case_id}",
                    task_input_fingerprint=_fp(f"task-{case_id}"),
                    dependencies=(),
                    bindings=(),
                    tool_names=(),
                    readiness="ready",
                )
                for case_id in case_ids
            ),
            adaptation_fingerprint=_fp("adaptation"),
            ready=True,
        ),
        replay_backend=backend,
        member_timeout_seconds=30,
    )

    assert bundle is not None
    plan, decision, profile = bundle
    assert decision.safe_lane_count == 2
    assert plan.isolation_summary.safe_lane_count == 2
    assert set(plan.case_ids) == set(case_ids[2:])
    assert set(plan.case_ids).isdisjoint(experiment.search_visible_case_ids)
    assert store.read_measurement_control_plan(
        "run-runner-v2", plan.measurement_plan_fingerprint
    ) == plan
    assert store.read_measurement_control_contracts(
        "run-runner-v2", plan.measurement_plan_fingerprint
    ) == (decision, profile)


def test_runner_resumes_exact_measurement_authority_across_campaign_cycles(
    tmp_path: Path,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n", encoding="utf-8")
    target = SkillTextTarget(skill_path)
    case_ids = ("case-1", "case-2")
    dataset = SelfEvolveDataset(
        cases=tuple(EvalCase(case_id=item, input=item) for item in case_ids),
        recipe=DatasetRecipe(
            source={"kind": "resume-v2"},
            split_seed="resume-v2",
            splits={"train": [], "validation": [], "held_out": list(case_ids)},
            held_out_case_ids=case_ids,
        ),
    )
    candidate = CandidateVariant(
        candidate_id="resume-candidate",
        target=target.identity,
        content="---\nname: demo\n---\n# Demo\nImproved.\n",
        rationale="resume fixture",
        target_fingerprint=target.fingerprint_current_content(),
    )
    adaptation = ReplayAdaptationBundle(
        schema_version="test",
        source_workspace_root=str(tmp_path),
        workspace_seed=str(tmp_path / "seed"),
        workspace_seed_fingerprint=_fp("resume-seed"),
        manifest_path=str(tmp_path / "manifest.json"),
        environment_snapshot_path=str(tmp_path / "environment.json"),
        environment_fingerprint=_fp("resume-environment"),
        cases=tuple(
            ReplayCaseAdaptation(
                case_id=item,
                adapted_task_input=item,
                task_input_fingerprint=_fp(item),
                dependencies=(),
                bindings=(),
                tool_names=(),
                readiness="ready",
            )
            for item in case_ids
        ),
        adaptation_fingerprint=_fp("resume-adaptation"),
        ready=True,
    )

    class Optimizer:
        async def propose(self, _request):  # pragma: no cover
            raise AssertionError("optimizer is outside this integration seam")

    backend = AWorldCliCandidateReplayBackend()
    store = FilesystemSelfEvolveStore(tmp_path)
    source = SelfEvolveRunner(
        store=store,
        optimizer=Optimizer(),
        replay_enabled=True,
        candidate_replay_backend=backend,
        measurement_mode=MeasurementPolicyMode.REQUIRED,
        measurement_min_independent_cases=2,
    )
    experiment = source._plan_candidate_measurement(
        run_id="campaign-cycle-1",
        target=target,
        dataset=dataset,
        candidate=candidate,
        candidate_count=1,
    )
    assert experiment is not None
    compiled = source._compile_authoritative_measurement_plan(
        run_id="campaign-cycle-1",
        dataset=dataset,
        candidate=candidate,
        replay_adaptation=adaptation,
        replay_backend=backend,
        member_timeout_seconds=30,
        experiment=experiment,
    )
    assert compiled is not None
    plan, decision, profile = compiled
    replay_dir = tmp_path / ".aworld" / "self_evolve" / "campaign-cycle-1" / "replay" / candidate.candidate_id
    replay_dir.mkdir(parents=True, exist_ok=True)
    request = build_replay_request(
        run_id="campaign-cycle-1",
        workspace_root=tmp_path,
        target=target.identity,
        candidate=candidate,
        overlay_skill_root=skill_path.parent,
        dataset=dataset,
        replay_adaptation=adaptation,
        evidence_policy_mode="required",
        measurement_plan=plan,
        measurement_isolation_decision=decision,
        measurement_evidence_policy_profile=profile,
    )
    (replay_dir / "request.json").write_text(
        json.dumps(to_json_dict(request)), encoding="utf-8"
    )

    resumed = SelfEvolveRunner(
        store=store,
        optimizer=Optimizer(),
        replay_enabled=True,
        candidate_replay_backend=backend,
        replay_resume_dir=replay_dir,
        measurement_resume_run_id="campaign-cycle-1",
        measurement_mode=MeasurementPolicyMode.REQUIRED,
        measurement_min_independent_cases=2,
    )
    resumed_experiment = resumed._plan_candidate_measurement(
        run_id="campaign-cycle-2",
        target=target,
        dataset=dataset,
        candidate=candidate,
        candidate_count=1,
    )
    resumed_bundle = resumed._compile_authoritative_measurement_plan(
        run_id="campaign-cycle-2",
        dataset=dataset,
        candidate=candidate,
        replay_adaptation=adaptation,
        replay_backend=backend,
        member_timeout_seconds=30,
        experiment=resumed_experiment,
    )

    assert resumed_experiment == experiment
    assert resumed_bundle == (plan, decision, profile)
    assert not (
        tmp_path / ".aworld" / "self_evolve" / "campaign-cycle-2" / "measurement_control"
    ).exists()


def test_runner_compiles_screening_plan_with_stable_candidate_identity(
    tmp_path: Path,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n", encoding="utf-8")
    target = SkillTextTarget(skill_path)
    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="qualification-case", input="run qualification"),),
        recipe=DatasetRecipe(
            source={"kind": "screening-v2-integration"},
            split_seed="screening-v2",
            splits={"train": ["qualification-case"], "validation": [], "held_out": []},
            trainable_case_ids=("qualification-case",),
        ),
    )
    candidate = CandidateVariant(
        candidate_id="stable-screening-candidate",
        target=target.identity,
        content="---\nname: demo\n---\n# Demo\nImproved.\n",
        rationale="screening plan integration fixture",
        target_fingerprint=target.fingerprint_current_content(),
    )
    store = FilesystemSelfEvolveStore(tmp_path)
    backend = AWorldCliCandidateReplayBackend()
    runner = SelfEvolveRunner(
        store=store,
        optimizer=object(),
        replay_enabled=True,
        candidate_replay_backend=backend,
        measurement_mode=MeasurementPolicyMode.REQUIRED,
    )
    experiment = runner._plan_candidate_measurement(
        run_id="run-screening-v2",
        target=target,
        dataset=dataset,
        candidate=candidate,
        candidate_count=1,
        experiment_registry=runner._screening_measurement_experiments,
        experiment_key=(
            "run-screening-v2",
            candidate.candidate_id,
            replay_dataset_fingerprint(dataset),
        ),
        selection_protocol="staged_qualification_candidate",
        repetitions=1,
        minimum_independent_cases=1,
    )
    assert experiment is not None
    adaptation = ReplayAdaptationBundle(
        schema_version="test",
        source_workspace_root=str(tmp_path),
        workspace_seed=str(tmp_path / "seed"),
        workspace_seed_fingerprint=_fp("screening-workspace-seed"),
        manifest_path=str(tmp_path / "manifest.json"),
        environment_snapshot_path=str(tmp_path / "environment.json"),
        environment_fingerprint=_fp("screening-environment"),
        cases=(
            ReplayCaseAdaptation(
                case_id="qualification-case",
                adapted_task_input="run qualification",
                task_input_fingerprint=_fp("screening-task"),
                dependencies=(),
                bindings=(),
                tool_names=(),
                readiness="ready",
            ),
        ),
        adaptation_fingerprint=_fp("screening-adaptation"),
        ready=True,
    )

    bundle = runner._compile_authoritative_measurement_plan(
        run_id="run-screening-v2",
        dataset=dataset,
        candidate=candidate,
        replay_adaptation=adaptation,
        replay_backend=backend,
        member_timeout_seconds=30,
        artifact_namespace="screening/qualification-case",
        target_adapter=target,
        experiment=experiment,
        measurement_stage="screening",
    )

    assert bundle is not None
    plan, _, _ = bundle
    assert plan.candidate_fingerprint == experiment.treatment.fingerprint
    assert plan.case_ids == ("qualification-case",)
    assert plan.stages[0].visibility_role is CaseVisibilityRole.REPAIR_SCREENING
    assert plan.estimator_version == "paired-screening-experiment-v2"
    assert store.read_measurement_control_plan(
        "run-screening-v2", plan.measurement_plan_fingerprint
    ) == plan


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "transfer_score",
        "framework_failure_variant",
        "repetitions_per_case",
        "expected_decision",
        "expected_projection",
    ),
    (
        (1.0, None, 1, "stop_confident_positive", "succeeded"),
        (-1.0, None, 1, "stop_regression", "candidate_rejected"),
        (None, "baseline", 1, "stop_framework_blocked", "framework_blocked"),
        (
            None,
            "baseline-policy",
            3,
            "stop_framework_blocked",
            "framework_blocked",
        ),
        (
            None,
            "baseline-attestation",
            3,
            "stop_framework_blocked",
            "framework_blocked",
        ),
        (
            None,
            "candidate-v2",
            1,
            "stop_framework_blocked",
            "framework_blocked",
        ),
    ),
)
async def test_authoritative_replay_executes_adaptive_plan_not_legacy_batch(
    tmp_path: Path,
    transfer_score: float | None,
    framework_failure_variant: str | None,
    repetitions_per_case: int,
    expected_decision: str,
    expected_projection: str,
) -> None:
    primary = tuple(f"primary-{index}" for index in range(1, 6))
    transfer = "transfer-1"
    dataset = SelfEvolveDataset(
        cases=tuple(
            EvalCase(case_id=case_id, input=f"run {case_id}")
            for case_id in (*primary, transfer)
        ),
        recipe=DatasetRecipe(
            source={"kind": "measurement-v2-integration"},
            split_seed="measurement-v2",
            splits={"held_out": [*primary, transfer]},
        ),
    )
    candidate = CandidateVariant(
        candidate_id="candidate-v2",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        content="---\nname: demo\n---\n# Demo\n",
        rationale="measurement integration fixture",
        target_fingerprint=_fp("control"),
    )
    experiment = ControlledExperimentSpec.create(
        run_id="run-measurement-v2",
        mode=MeasurementPolicyMode.REQUIRED,
        swap_axis=SwapAxis.ARTIFACT,
        control=ComponentIdentity("control", _fp("control")),
        treatment=ComponentIdentity("treatment", _fp("treatment")),
        frozen_identities=FrozenIdentities(
            task_model=_fp("task-model"),
            generator=_fp("generator"),
            scheduler=_fp("scheduler"),
            evaluator=_fp("evaluator"),
            dataset=replay_dataset_fingerprint(dataset),
            environment=_fp("environment"),
            runtime=_fp("runtime"),
            prompt_context=_fp("prompt"),
            budget=_fp("budget"),
        ),
        sampling=SamplingPlan(
            independent_case_ids=primary,
            repetitions_per_case=repetitions_per_case,
        ),
        outcomes=OutcomePlan(
            primary_metric="score",
            minimum_effect=0.1,
            minimum_independent_cases=2,
            bootstrap_samples=500,
        ),
        budgets=ExperimentBudget(),
        transfer_panels=(
            TransferPanel.create(
                panel_id="transfer",
                role=TransferPanelRole.REGRESSION_CANARY,
                case_ids=(transfer,),
            ),
        ),
    )
    store = FilesystemSelfEvolveStore(tmp_path)
    store.write_measurement_experiment(experiment)
    decision = IsolationDecision.exclusive_fallback(
        requested_lane_count=2,
        fallback=IsolationExclusiveFallback(
            code="binding_requires_exclusive",
            limiting_resource="fixture",
            detail="fixture intentionally proves one exclusive lane",
        ),
    )
    profile = compile_replay_evidence_policy_profile_v2()
    compiled = compile_measurement_plan_v2(
        experiment=experiment,
        dataset_fingerprint=replay_dataset_fingerprint(dataset),
        execution_contract_fingerprint=_fp("execution"),
        isolation_decision=decision,
        evidence_policy_profile=profile,
        deadlines=DeadlinePolicy(
            attempt_timeout_seconds=10,
            member_hard_deadline_seconds=10,
            checkpoint_quantum_seconds=60,
            resumable_chunked=True,
        ),
    )
    persist_compiled_measurement_plan(
        store,
        run_id=experiment.run_id,
        compiled=compiled,
        isolation_decision=decision,
        evidence_policy_profile=profile,
    )
    sentinel = next(
        stage
        for stage in compiled.plan.stages
        if stage.kind is SamplingStageKind.SENTINEL
    )
    neutral_case = sentinel.case_ids[0]
    calls: list[tuple[str, str]] = []

    async def fake_executor(
        execution: ReplayExecutionRequest,
    ) -> ReplayExecutionResult:
        assert execution.evidence_finalization_timeout_seconds == (
            compiled.plan.deadlines.evidence_finalization_timeout_seconds
        )
        calls.append((execution.task_id, execution.variant_id))
        if (
            framework_failure_variant == "baseline-policy"
            and execution.variant_id == "baseline"
        ):
            return ReplayExecutionResult(
                status="failed",
                trajectory=[{"action": {"content": "bounded control"}}],
                failure={
                    "code": "replay_evidence_runtime_policy_violation",
                    "outcome": "task_failure",
                    "failure_owner": "task",
                    "failure_scope": "member",
                    "failure_stage": "task_rollout",
                    "repairable": False,
                    "reason": "frozen policy rejected unchanged control",
                },
            )
        if (
            framework_failure_variant == "baseline-attestation"
            and execution.variant_id == "baseline"
        ):
            return ReplayExecutionResult(
                status="failed",
                trajectory=[],
                failure={
                    "code": "evidence_policy_v2_attestation_failed",
                    "outcome": "framework_failure",
                    "failure_class": "measurement_runtime_trust",
                    "failure_owner": "framework",
                    "failure_scope": "shared_run",
                    "failure_stage": "evidence_finalization",
                    "repairable": True,
                    "reason": "control evidence bundle was not attested",
                },
            )
        if (
            transfer_score is None
            and execution.variant_id == framework_failure_variant
        ):
            return ReplayExecutionResult(
                status="failed",
                trajectory=[],
                failure={
                    "code": "framework_probe_failed",
                    "outcome": "framework_failure",
                    "failure_class": "measurement_runtime_trust",
                    "failure_owner": "framework",
                    "failure_scope": "shared_run",
                    "failure_stage": "evidence_finalization",
                    "repairable": False,
                    "reason": "fixture framework failure",
                },
            )
        if execution.variant_id == "baseline" or execution.task_id == neutral_case:
            score = 0.0
        elif execution.task_id == transfer:
            score = transfer_score
        else:
            score = 1.0
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[
                {
                    "action": {
                        "content": (
                            "x" * 1_200_000
                            if execution.task_id == neutral_case
                            and execution.variant_id == "baseline"
                            else execution.variant_id
                        )
                    }
                }
            ],
            metrics={"score": score},
        )

    request = CandidateReplayRequest(
        run_id=experiment.run_id,
        task_id=primary[0],
        workspace_root=str(tmp_path),
        target=candidate.target,
        candidate_id=candidate.candidate_id,
        overlay_skill_root=str(tmp_path / "overlay"),
        task_input="unused root input",
        dataset_fingerprint=replay_dataset_fingerprint(dataset),
        baseline_skill_fingerprint=_fp("control"),
        baseline_repetitions=1,
        candidate_repetitions=1,
        evidence_policy_mode="required",
        measurement_plan=compiled.plan,
        measurement_isolation_decision=decision,
        measurement_evidence_policy_profile=profile,
    )
    result = await AWorldCliCandidateReplayBackend(
        executor=fake_executor
    ).replay_candidate(request, candidate=candidate, dataset=dataset)

    if transfer_score is None:
        assert len(result.member_results or ()) == 1
        assert len(calls) == (
            1
            if framework_failure_variant
            in {"baseline", "baseline-policy", "baseline-attestation"}
            else 2
        )
        assert calls[0][0] in sentinel.case_ids
        assert calls[0][1] == "baseline"
        assert all(call[0] == calls[0][0] for call in calls)
        normalized = normalize_replay_members(
            dataset=dataset,
            replay_result=result,
        )
        returned_case_ids = {
            member.case_id for member in result.member_results or ()
        }
        assert normalized.missing_case_ids == ()
        assert set(normalized.intentionally_unadmitted_case_ids) == {
            case.case_id for case in dataset.cases
        } - returned_case_ids
    else:
        assert {member.case_id for member in result.member_results or ()} == {
            *primary,
            transfer,
        }
        assert len(calls) == len(dataset.cases) * 2
    schedule = json.loads(
        (
            tmp_path
            / ".aworld"
            / "self_evolve"
            / experiment.run_id
            / "replay"
            / candidate.candidate_id
            / "members"
            / "measurement_schedule.json"
        ).read_text(encoding="utf-8")
    )
    if transfer_score is None:
        assert schedule["schedule_count"] == 1
        assert transfer not in schedule["admitted_case_ids"]
    else:
        assert schedule["schedule_count"] == 3
        assert transfer in schedule["admitted_case_ids"]
        expansion_decisions = [
            decision
            for decision in schedule["decision_history"]
            if decision["kind"] == "admit_expansion"
        ]
        assert expansion_decisions
        assert all(
            decision["expected_information_value"] > 0
            and decision["remaining_case_budget"] >= 0
            and decision["admission_policy"]
            == "stratified-information-cost-risk-v1"
            for decision in expansion_decisions
        )
    assert schedule["scheduling_policy"] == "information-cost-risk-v1"
    assert len(schedule["schedules"]) == schedule["schedule_count"]
    assert all(
        set(item)
        >= {
            "safe_lane_count",
            "completed_pair_count",
            "pending_pair_count",
            "elapsed_seconds",
            "pairs",
        }
        for item in schedule["schedules"]
    )
    assert schedule["decision"]["kind"] == expected_decision
    projection_paths = tuple(
        (
            tmp_path
            / ".aworld"
            / "self_evolve"
            / experiment.run_id
            / "replay"
            / candidate.candidate_id
            / "members"
        ).rglob("measurement_result_projection.json")
    )
    assert projection_paths
    assert all(path.stat().st_size < 32_000 for path in projection_paths)
    if framework_failure_variant in {
        "baseline-policy",
        "baseline-attestation",
    }:
        assert schedule["decision"]["reason_code"] == (
            "baseline_evidence_policy_infeasible"
        )
    outcome = _campaign_measurement_outcome_for_replay(
        result,
        final_status=(
            SelfEvolveRunStatus.SUCCEEDED
            if expected_decision == "stop_confident_positive"
            else SelfEvolveRunStatus.REJECTED
        ),
    )
    assert outcome is not None
    assert outcome["projection"] == expected_projection
