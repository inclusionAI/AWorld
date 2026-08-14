from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

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
from aworld.self_evolve.measurement_control import DeadlinePolicy, SamplingStageKind
from aworld.self_evolve.measurement_planner import (
    compile_measurement_plan_v2,
    persist_compiled_measurement_plan,
)
from aworld.self_evolve.replay import (
    AWorldCliCandidateReplayBackend,
    CandidateReplayRequest,
    ReplayExecutionRequest,
    ReplayExecutionResult,
    compile_replay_evidence_policy_profile_v2,
    replay_dataset_fingerprint,
)
from aworld.self_evolve.replay_adaptation import (
    IsolationDecision,
    IsolationExclusiveFallback,
)
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.runner import (
    SelfEvolveRunner,
    _campaign_measurement_outcome_for_replay,
)
from aworld.self_evolve.targets import SkillTextTarget
from aworld.self_evolve.types import (
    CandidateVariant,
    DatasetRecipe,
    SelfEvolveTargetRef,
    SelfEvolveRunStatus,
)


def _fp(label: str) -> str:
    return stable_measurement_fingerprint({"label": label})


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
        replay_adaptation=SimpleNamespace(
            adaptation_fingerprint=_fp("adaptation"),
            workspace_seed_fingerprint=_fp("workspace-seed"),
        ),
        replay_backend=backend,
        member_timeout_seconds=30,
    )

    assert bundle is not None
    plan, decision, profile = bundle
    assert set(plan.case_ids) == set(case_ids[2:])
    assert set(plan.case_ids).isdisjoint(experiment.search_visible_case_ids)
    assert store.read_measurement_control_plan(
        "run-runner-v2", plan.measurement_plan_fingerprint
    ) == plan
    assert store.read_measurement_control_contracts(
        "run-runner-v2", plan.measurement_plan_fingerprint
    ) == (decision, profile)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("transfer_score", "expected_decision"),
    ((1.0, "stop_confident_positive"), (-1.0, "stop_regression")),
)
async def test_authoritative_replay_executes_adaptive_plan_not_legacy_batch(
    tmp_path: Path,
    transfer_score: float,
    expected_decision: str,
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
        sampling=SamplingPlan(independent_case_ids=primary),
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
        calls.append((execution.task_id, execution.variant_id))
        if execution.variant_id == "baseline" or execution.task_id == neutral_case:
            score = 0.0
        elif execution.task_id == transfer:
            score = transfer_score
        else:
            score = 1.0
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": execution.variant_id}}],
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
    assert schedule["schedule_count"] == 3
    assert transfer in schedule["admitted_case_ids"]
    assert schedule["decision"]["kind"] == expected_decision
    outcome = _campaign_measurement_outcome_for_replay(
        result,
        final_status=(
            SelfEvolveRunStatus.SUCCEEDED
            if expected_decision == "stop_confident_positive"
            else SelfEvolveRunStatus.REJECTED
        ),
    )
    assert outcome is not None
    assert outcome["projection"] == (
        "succeeded"
        if expected_decision == "stop_confident_positive"
        else "candidate_rejected"
    )
