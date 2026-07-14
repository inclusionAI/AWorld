from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from aworld.self_evolve.datasets import (
    EvalCase,
    SelfEvolveDataset,
    SelfEvolveEvalSourceConfig,
    build_dataset_from_source,
)
from aworld.self_evolve.overlay import create_candidate_skill_overlay
from aworld.self_evolve.overlay import cleanup_self_evolve_overlays
from aworld.self_evolve.replay import (
    AWorldCliCandidateReplayBackend,
    AWorldCliReplayExecutor,
    CandidateReplayRequest,
    CandidateReplayResult,
    ReplayExecutionRequest,
    ReplayExecutionResult,
    ReplayVariantResult,
    build_paired_replay_dataset,
    build_replay_request,
    candidate_replay_is_comparable,
    load_candidate_replay_result,
    _invalid_evidence_manifest_entry_reason,
    _member_artifact_name,
    _member_baseline_replay_dir,
)
from aworld.self_evolve.types import CandidateVariant, DatasetRecipe, SelfEvolveTargetRef
from aworld.skills.compat_provider import build_compat_registry


def _candidate(content: str, candidate_id: str = "cand-1") -> CandidateVariant:
    return CandidateVariant(
        candidate_id=candidate_id,
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        content=content,
        rationale="test candidate",
        target_fingerprint="sha256:old",
    )


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, default=lambda value: value.__dict__, indent=2),
        encoding="utf-8",
    )


def test_candidate_skill_overlay_materializes_shadow_root_without_mutating_real_skill(
    tmp_path: Path,
) -> None:
    skills_root = tmp_path / "skills"
    demo_path = skills_root / "demo" / "SKILL.md"
    helper_path = skills_root / "helper" / "SKILL.md"
    demo_path.parent.mkdir(parents=True)
    helper_path.parent.mkdir(parents=True)
    original_demo = "---\nname: demo\n---\n# Demo\n\nOriginal.\n"
    candidate_demo = "---\nname: demo\n---\n# Demo\n\nCandidate.\n"
    demo_path.write_text(original_demo, encoding="utf-8")
    helper_path.write_text("---\nname: helper\n---\n# Helper\n", encoding="utf-8")

    overlay = create_candidate_skill_overlay(
        workspace_root=tmp_path,
        run_id="run-1",
        candidate=_candidate(candidate_demo),
        target_skill_path=demo_path,
        baseline_skill_roots=(skills_root,),
    )

    assert overlay.shadow_root == tmp_path / ".aworld" / "self_evolve" / "run-1" / "overlays" / "cand-1" / "skills"
    assert overlay.candidate_skill_path.read_text(encoding="utf-8") == candidate_demo
    assert (overlay.shadow_root / "helper" / "SKILL.md").exists()
    assert demo_path.read_text(encoding="utf-8") == original_demo

    registry = build_compat_registry(overlay.shadow_root)
    descriptors = {descriptor.skill_name: descriptor for descriptor in registry.list_descriptors()}
    loaded_demo = registry.load_content(descriptors["demo"].skill_id)
    loaded_helper = registry.load_content(descriptors["helper"].skill_id)
    assert "Candidate." in loaded_demo.usage
    assert "Original." not in loaded_demo.usage
    assert "Helper" in loaded_helper.usage


def test_cleanup_self_evolve_overlays_retains_latest_runs(tmp_path: Path) -> None:
    root = tmp_path / ".aworld" / "self_evolve"
    old_overlay = root / "run-old" / "overlays" / "cand-1" / "skills"
    new_overlay = root / "run-new" / "overlays" / "cand-2" / "skills"
    old_overlay.mkdir(parents=True)
    new_overlay.mkdir(parents=True)
    old_report = root / "run-old" / "report.json"
    new_report = root / "run-new" / "report.json"
    old_report.write_text("{}", encoding="utf-8")
    new_report.write_text("{}", encoding="utf-8")

    cleanup = cleanup_self_evolve_overlays(tmp_path, keep_latest_runs=1)

    assert cleanup["removed_run_count"] == 1
    assert not (root / "run-old" / "overlays").exists()
    assert (root / "run-new" / "overlays").exists()


def test_paired_replay_dataset_maps_baseline_and_candidate_trajectories() -> None:
    baseline_trajectory = [
        {"state": {"input": {"content": "task"}}, "action": {"content": "old"}, "reward": {}}
    ]
    candidate_trajectory = [
        {"state": {"input": {"content": "task"}}, "action": {"content": "new"}, "reward": {}}
    ]
    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(
                case_id="task-1",
                input={"content": "task"},
                metadata={"baseline_trajectory": baseline_trajectory},
            ),
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 1},
            split_seed="seed",
            splits={"train": ["task-1"], "validation": [], "held_out": []},
        ),
    )
    replay = CandidateReplayResult(
        request=CandidateReplayRequest(
            run_id="run-1",
            task_id="task-1",
            workspace_root=str(Path("/tmp/workspace")),
            target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
            candidate_id="cand-1",
            overlay_skill_root="/tmp/overlay",
            task_input={"content": "task"},
        ),
        baseline=ReplayVariantResult(
            variant_id="baseline",
            status="succeeded",
            trajectory=baseline_trajectory,
        ),
        candidate=ReplayVariantResult(
            variant_id="cand-1",
            status="succeeded",
            trajectory=candidate_trajectory,
            metrics={"latency_ms": 120.0},
        ),
    )

    paired = build_paired_replay_dataset(
        dataset=dataset,
        replay_result=replay,
        candidate=_candidate("---\nname: demo\n---\n# Demo\n"),
    )

    assert paired.cases[0].metadata["variant_trajectories"]["baseline"] == baseline_trajectory
    assert paired.cases[0].metadata["variant_trajectories"]["cand-1"] == candidate_trajectory
    assert paired.cases[0].metadata["replay"]["candidate"]["metrics"]["latency_ms"] == 120.0


def test_build_replay_request_skips_framework_generated_eval_cases(tmp_path: Path) -> None:
    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(
                case_id="framework-evaluator-case",
                input={
                    "content": json.dumps(
                        {
                            "evaluation_runtime_contract": {
                                "do_not_call_external_tools": True,
                                "trajectory_log_path": str(
                                    tmp_path
                                    / ".aworld"
                                    / "self_evolve"
                                    / "evaluator"
                                    / "old-run"
                                    / "trajectory.log"
                                ),
                            },
                            "report_output_path": str(tmp_path / "report.json"),
                        }
                    )
                },
                metadata={"framework_meta_trajectory": True},
            ),
            EvalCase(
                case_id="user-task",
                input={"content": "Summarize the referenced page with grounded citations."},
            ),
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 2},
            split_seed="seed",
            splits={"train": ["framework-evaluator-case", "user-task"], "validation": [], "held_out": []},
        ),
    )

    request = build_replay_request(
        run_id="run-1",
        workspace_root=tmp_path,
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate=_candidate("---\nname: demo\n---\n# Demo\n"),
        overlay_skill_root=tmp_path / "overlay-skills",
        dataset=dataset,
    )

    assert request.task_id == "user-task"
    assert request.task_input == {"content": "Summarize the referenced page with grounded citations."}


def test_build_replay_request_rejects_framework_only_dataset(tmp_path: Path) -> None:
    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(
                case_id="framework-evaluator-case",
                input={
                    "content": (
                        "evaluation_runtime_contract: do_not_call_external_tools=true "
                        f"trajectory_log_path={tmp_path}/.aworld/self_evolve/evaluator/run/trajectory.log"
                    )
                },
                metadata={"framework_meta_trajectory": True},
            ),
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 1},
            split_seed="seed",
            splits={"train": ["framework-evaluator-case"], "validation": [], "held_out": []},
        ),
    )

    with pytest.raises(ValueError, match="user task eval case"):
        build_replay_request(
            run_id="run-1",
            workspace_root=tmp_path,
            target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
            candidate=_candidate("---\nname: demo\n---\n# Demo\n"),
            overlay_skill_root=tmp_path / "overlay-skills",
            dataset=dataset,
        )


def test_paired_replay_dataset_expands_repetition_trajectories_into_eval_cases() -> None:
    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="task-1", input={"content": "task"}),),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 1},
            split_seed="seed",
            splits={"train": ["task-1"], "validation": [], "held_out": []},
        ),
    )
    baseline_1 = ReplayVariantResult(
        variant_id="baseline-1",
        status="succeeded",
        trajectory=[{"action": {"content": "baseline-1"}}],
    )
    baseline_2 = ReplayVariantResult(
        variant_id="baseline-2",
        status="succeeded",
        trajectory=[{"action": {"content": "baseline-2"}}],
    )
    candidate_1 = ReplayVariantResult(
        variant_id="cand-1-1",
        status="succeeded",
        trajectory=[{"action": {"content": "candidate-1"}}],
    )
    candidate_2 = ReplayVariantResult(
        variant_id="cand-1-2",
        status="succeeded",
        trajectory=[{"action": {"content": "candidate-2"}}],
    )
    candidate_3 = ReplayVariantResult(
        variant_id="cand-1-3",
        status="succeeded",
        trajectory=[{"action": {"content": "candidate-3"}}],
    )
    replay = CandidateReplayResult(
        request=CandidateReplayRequest(
            run_id="run-1",
            task_id="task-1",
            workspace_root=str(Path("/tmp/workspace")),
            target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
            candidate_id="cand-1",
            overlay_skill_root="/tmp/overlay",
            task_input={"content": "task"},
            baseline_repetitions=2,
            candidate_repetitions=3,
        ),
        baseline=ReplayVariantResult(
            variant_id="baseline",
            status="succeeded",
            trajectory=baseline_2.trajectory,
            metrics={"repetition_count": 2, "successful_repetition_count": 2},
            repetition_results=(baseline_1, baseline_2),
        ),
        candidate=ReplayVariantResult(
            variant_id="cand-1",
            status="succeeded",
            trajectory=candidate_3.trajectory,
            metrics={"repetition_count": 3, "successful_repetition_count": 3},
            repetition_results=(candidate_1, candidate_2, candidate_3),
        ),
    )

    paired = build_paired_replay_dataset(
        dataset=dataset,
        replay_result=replay,
        candidate=_candidate("---\nname: demo\n---\n# Demo\n", candidate_id="cand-1"),
    )

    assert [case.case_id for case in paired.cases] == [
        "task-1__replay_1",
        "task-1__replay_2",
        "task-1__replay_3",
    ]
    assert [
        case.metadata["variant_trajectories"]["baseline"][0]["action"]["content"]
        for case in paired.cases
    ] == ["baseline-1", "baseline-2", "baseline-1"]
    assert [
        case.metadata["variant_trajectories"]["cand-1"][0]["action"]["content"]
        for case in paired.cases
    ] == ["candidate-1", "candidate-2", "candidate-3"]
    assert paired.recipe.source["paired_replay"] is True
    assert paired.recipe.source["original_case_count"] == 1
    assert paired.recipe.source["replay_case_count"] == 3
    assert paired.recipe.splits["train"] == [
        "task-1__replay_1",
        "task-1__replay_2",
        "task-1__replay_3",
    ]


def test_load_candidate_replay_result_restores_repetition_artifacts(tmp_path: Path) -> None:
    replay_dir = tmp_path / "replay" / "cand-1"
    request = CandidateReplayRequest(
        run_id="run-1",
        task_id="task-1",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate_id="cand-1",
        overlay_skill_root=str(tmp_path / "overlay"),
        task_input={"content": "task"},
        baseline_repetitions=2,
        candidate_repetitions=3,
    )
    _write_json(replay_dir / "request.json", request)
    for variant_root, base_variant_id, count in (
        (replay_dir / "baseline", "baseline", 2),
        (replay_dir / "cand-1", "cand-1", 3),
    ):
        variant_root.mkdir(parents=True)
        _write_json(
            variant_root / "aggregate_metrics.json",
            {
                "repetition_count": count,
                "successful_repetition_count": count,
                "failed_repetition_count": 0,
            },
        )
        for index in range(1, count + 1):
            repetition_dir = variant_root / str(index)
            repetition_dir.mkdir()
            (repetition_dir / "stdout.txt").write_text("", encoding="utf-8")
            (repetition_dir / "stderr.txt").write_text("", encoding="utf-8")
            _write_json(repetition_dir / "metrics.json", {"returncode": 0})
            _write_json(
                repetition_dir / "trajectory.json",
                [{"action": {"content": f"{base_variant_id}-{index}"}}],
            )

    loaded = load_candidate_replay_result(replay_dir)

    assert loaded.request.candidate_id == "cand-1"
    assert loaded.succeeded is True
    assert len(loaded.baseline.repetition_results) == 2
    assert len(loaded.candidate.repetition_results) == 3
    assert loaded.candidate.trajectory[0]["action"]["content"] == "cand-1-3"


def test_load_candidate_replay_result_prefers_successful_single_evidence_retry(
    tmp_path: Path,
) -> None:
    replay_dir = tmp_path / "replay" / "cand-1"
    request = CandidateReplayRequest(
        run_id="run-single-retry",
        task_id="task-1",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate_id="cand-1",
        overlay_skill_root=str(tmp_path / "overlay"),
        task_input={"content": "task"},
    )
    _write_json(replay_dir / "request.json", request)
    baseline_dir = replay_dir / "baseline"
    _write_json(
        baseline_dir / "trajectory.json",
        [{"action": {"content": "compacted baseline"}}],
    )
    _write_json(
        baseline_dir / "failure.json",
        {"reason": "evidence_quality_failed"},
    )
    retry_dir = baseline_dir / "evidence_retry_2"
    _write_json(
        retry_dir / "trajectory.json",
        [{"action": {"content": "complete baseline"}}],
    )
    _write_json(retry_dir / "metrics.json", {"evidence_strategy_passed": True})
    candidate_dir = replay_dir / "cand-1"
    _write_json(
        candidate_dir / "trajectory.json",
        [{"action": {"content": "candidate"}}],
    )
    _write_json(candidate_dir / "metrics.json", {})

    loaded = load_candidate_replay_result(replay_dir)

    assert loaded.baseline.succeeded is True
    assert loaded.baseline.trajectory[0]["action"]["content"] == "complete baseline"
    assert loaded.succeeded is True


def test_paired_replay_dataset_requires_successful_candidate_replay() -> None:
    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="task-1", input="task"),),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 1},
            split_seed="seed",
            splits={"train": ["task-1"], "validation": [], "held_out": []},
        ),
    )
    replay = CandidateReplayResult(
        request=CandidateReplayRequest(
            run_id="run-1",
            task_id="task-1",
            workspace_root=str(Path("/tmp/workspace")),
            target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
            candidate_id="cand-1",
            overlay_skill_root="/tmp/overlay",
            task_input="task",
        ),
        baseline=ReplayVariantResult(
            variant_id="baseline",
            status="succeeded",
            trajectory=[],
        ),
        candidate=ReplayVariantResult(
            variant_id="cand-1",
            status="failed",
            trajectory=[],
            failure={"reason": "missing browser"},
        ),
    )

    with pytest.raises(ValueError, match="candidate replay did not succeed"):
        build_paired_replay_dataset(
            dataset=dataset,
            replay_result=replay,
            candidate=_candidate("---\nname: demo\n---\n# Demo\n"),
        )


def test_paired_replay_dataset_uses_source_trajectory_for_baseline_task_failure() -> None:
    source_trajectory = [
        {
            "state": {"input": {"content": "task"}},
            "action": {"content": "baseline did not finish"},
            "reward": {"status": "failed"},
        }
    ]
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=source_trajectory,
        task_id="task-1",
    )
    replay = CandidateReplayResult(
        request=CandidateReplayRequest(
            run_id="run-task-failure",
            task_id="task-1",
            workspace_root=str(Path("/tmp/workspace")),
            target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
            candidate_id="cand-1",
            overlay_skill_root="/tmp/overlay",
            task_input="task",
        ),
        baseline=ReplayVariantResult(
            variant_id="baseline",
            status="failed",
            trajectory=[],
            failure={"type": "TimeoutExpired", "reason": "replay timed out"},
        ),
        candidate=ReplayVariantResult(
            variant_id="cand-1",
            status="succeeded",
            trajectory=[{"action": {"content": "candidate completed"}}],
        ),
    )

    assert candidate_replay_is_comparable(dataset=dataset, replay_result=replay) is True

    paired = build_paired_replay_dataset(
        dataset=dataset,
        replay_result=replay,
        candidate=_candidate("---\nname: demo\n---\n# Demo\n"),
    )

    case = paired.cases[0]
    assert case.metadata["variant_trajectories"]["baseline"][0]["action"][
        "content"
    ] == "baseline did not finish"
    assert case.metadata["replay"]["baseline"]["outcome"] == "task_failure"
    assert (
        case.metadata["replay"]["baseline"]["trajectory_source"]
        == "source_trajectory"
    )


def test_paired_replay_dataset_rejects_infrastructure_baseline_failure() -> None:
    source_trajectory = [
        {
            "state": {"input": {"content": "task"}},
            "action": {"content": "baseline"},
            "reward": {"status": "failed"},
        }
    ]
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=source_trajectory,
        task_id="task-1",
    )
    replay = CandidateReplayResult(
        request=CandidateReplayRequest(
            run_id="run-infrastructure-failure",
            task_id="task-1",
            workspace_root=str(Path("/tmp/workspace")),
            target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
            candidate_id="cand-1",
            overlay_skill_root="/tmp/overlay",
            task_input="task",
        ),
        baseline=ReplayVariantResult(
            variant_id="baseline",
            status="failed",
            trajectory=[],
            failure={"type": "ProcessError", "reason": "aworld-cli run failed"},
        ),
        candidate=ReplayVariantResult(
            variant_id="cand-1",
            status="succeeded",
            trajectory=[{"action": {"content": "candidate completed"}}],
        ),
    )

    assert candidate_replay_is_comparable(dataset=dataset, replay_result=replay) is False
    with pytest.raises(ValueError, match="comparable paired outcomes"):
        build_paired_replay_dataset(
            dataset=dataset,
            replay_result=replay,
            candidate=_candidate("---\nname: demo\n---\n# Demo\n"),
        )


@pytest.mark.asyncio
async def test_aworld_cli_candidate_replay_backend_aggregates_repetitions(
    tmp_path: Path,
) -> None:
    calls = []
    scores = {
        "baseline-1": 0.4,
        "baseline-2": 0.6,
        "cand-1-1": 0.8,
        "cand-1-2": 0.9,
        "cand-1-3": 1.0,
    }

    async def fake_executor(request):
        calls.append(request)
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[
                {
                    "state": {"input": request.task_input},
                    "action": {"content": request.variant_id},
                    "reward": {"status": "ok"},
                }
            ],
            metrics={"score": scores[request.variant_id]},
        )

    request = CandidateReplayRequest(
        run_id="run-repetitions",
        task_id="task-1",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate_id="cand-1",
        overlay_skill_root=str(tmp_path / "overlay-skills"),
        task_input="Replay this task",
        baseline_repetitions=2,
        candidate_repetitions=3,
    )

    result = await AWorldCliCandidateReplayBackend(executor=fake_executor).replay_candidate(
        request,
        candidate=_candidate("---\nname: demo\n---\n# Demo\n", candidate_id="cand-1"),
        dataset=SelfEvolveDataset(
            cases=(EvalCase(case_id="task-1", input="Replay this task"),),
            recipe=DatasetRecipe(
                source={"kind": "test", "case_count": 1},
                split_seed="seed",
                splits={"train": ["task-1"], "validation": [], "held_out": []},
            ),
        ),
    )

    assert [call.variant_id for call in calls] == [
        "baseline-1",
        "baseline-2",
        "cand-1-1",
        "cand-1-2",
        "cand-1-3",
    ]
    assert result.baseline.variant_id == "baseline"
    assert result.baseline.metrics["repetition_count"] == 2
    assert result.baseline.metrics["score"] == pytest.approx(0.5)
    assert result.candidate.variant_id == "cand-1"
    assert result.candidate.metrics["repetition_count"] == 3
    assert result.candidate.metrics["score"] == pytest.approx(0.9)
    assert [item.variant_id for item in result.candidate.repetition_results] == [
        "cand-1-1",
        "cand-1-2",
        "cand-1-3",
    ]
    assert result.candidate.trajectory[0]["action"]["content"] == "cand-1-3"


@pytest.mark.asyncio
async def test_aworld_cli_candidate_replay_backend_reuses_stored_baseline_replay(
    tmp_path: Path,
) -> None:
    baseline_dir = tmp_path / "stored-baseline"
    (baseline_dir / "1").mkdir(parents=True)
    (baseline_dir / "1" / "trajectory.json").write_text(
        json.dumps([{"action": {"content": "stored baseline"}}]),
        encoding="utf-8",
    )
    (baseline_dir / "1" / "metrics.json").write_text(
        json.dumps({"score": 0.7}),
        encoding="utf-8",
    )
    (baseline_dir / "2").mkdir(parents=True)
    (baseline_dir / "2" / "trajectory.json").write_text(
        json.dumps([{"action": {"content": "stored baseline selected"}}]),
        encoding="utf-8",
    )
    (baseline_dir / "2" / "metrics.json").write_text(
        json.dumps({"score": 0.9}),
        encoding="utf-8",
    )
    (baseline_dir / "aggregate_metrics.json").write_text(
        json.dumps(
            {
                "repetition_count": 2,
                "successful_repetition_count": 2,
                "score": 0.8,
            }
        ),
        encoding="utf-8",
    )

    calls: list[ReplayExecutionRequest] = []

    async def fake_executor(request):
        calls.append(request)
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": request.variant_id}}],
            metrics={"score": 1.0},
        )

    request = CandidateReplayRequest(
        run_id="run-baseline-reuse",
        task_id="task-1",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate_id="cand-1",
        overlay_skill_root=str(tmp_path / "overlay-skills"),
        task_input="Replay this task",
        baseline_repetitions=2,
        candidate_repetitions=3,
        baseline_replay_dir=str(baseline_dir),
    )

    result = await AWorldCliCandidateReplayBackend(executor=fake_executor).replay_candidate(
        request,
        candidate=_candidate("---\nname: demo\n---\n# Demo\n", candidate_id="cand-1"),
        dataset=SelfEvolveDataset(
            cases=(EvalCase(case_id="task-1", input="Replay this task"),),
            recipe=DatasetRecipe(
                source={"kind": "test", "case_count": 1},
                split_seed="seed",
                splits={"train": ["task-1"], "validation": [], "held_out": []},
            ),
        ),
    )

    assert [call.variant_id for call in calls] == ["cand-1-1", "cand-1-2", "cand-1-3"]
    assert result.baseline.succeeded is True
    assert result.baseline.metrics["repetition_count"] == 2
    assert result.baseline.trajectory[0]["action"]["content"] == "stored baseline selected"
    assert result.candidate.succeeded is True


@pytest.mark.asyncio
async def test_aworld_cli_candidate_replay_backend_allows_partial_repetition_success(
    tmp_path: Path,
) -> None:
    async def fake_executor(request):
        if request.variant_id == "baseline-2":
            return ReplayExecutionResult(
                status="failed",
                trajectory=[],
                failure={"type": "TimeoutExpired", "reason": "replay timed out"},
                metrics={"latency_ms": 600000},
            )
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[
                {
                    "state": {"input": request.task_input},
                    "action": {"content": request.variant_id},
                    "reward": {"status": "ok"},
                }
            ],
            metrics={"latency_ms": 1000},
        )

    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="task-1", input="Replay this task"),),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 1},
            split_seed="seed",
            splits={"train": ["task-1"], "validation": [], "held_out": []},
        ),
    )
    request = CandidateReplayRequest(
        run_id="run-partial-repetitions",
        task_id="task-1",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate_id="cand-1",
        overlay_skill_root=str(tmp_path / "overlay-skills"),
        task_input="Replay this task",
        baseline_repetitions=2,
        candidate_repetitions=3,
    )

    result = await AWorldCliCandidateReplayBackend(executor=fake_executor).replay_candidate(
        request,
        candidate=_candidate("---\nname: demo\n---\n# Demo\n", candidate_id="cand-1"),
        dataset=dataset,
    )

    assert result.succeeded is True
    assert result.baseline.succeeded is True
    assert result.baseline.metrics["repetition_count"] == 2
    assert result.baseline.metrics["successful_repetition_count"] == 1
    assert result.baseline.metrics["failed_repetition_count"] == 1
    assert result.baseline.metrics["repetition_failures"] == [
        {"type": "TimeoutExpired", "reason": "replay timed out"}
    ]
    assert result.baseline.trajectory[0]["action"]["content"] == "baseline-1"
    assert result.baseline.failure is None

    paired = build_paired_replay_dataset(
        dataset=dataset,
        replay_result=result,
        candidate=_candidate("---\nname: demo\n---\n# Demo\n", candidate_id="cand-1"),
    )

    assert [case.case_id for case in paired.cases] == [
        "task-1__replay_1",
        "task-1__replay_2",
        "task-1__replay_3",
    ]
    assert {
        case.metadata["variant_trajectories"]["baseline"][0]["action"]["content"]
        for case in paired.cases
    } == {"baseline-1"}
    assert paired.cases[0].metadata["replay"]["baseline"]["metrics"][
        "failed_repetition_count"
    ] == 1


@pytest.mark.asyncio
async def test_multi_member_replay_executes_and_maps_each_member_independently(
    tmp_path: Path,
) -> None:
    calls: list[ReplayExecutionRequest] = []

    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        calls.append(request)
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[
                {
                    "state": {"input": request.task_input},
                    "action": {
                        "content": f"{request.task_id}:{request.variant_id}"
                    },
                    "reward": {"status": "ok"},
                }
            ],
        )

    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(case_id="task-a", input="Replay task A"),
            EvalCase(case_id="task-b", input="Replay task B"),
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 2},
            split_seed="seed",
            splits={
                "train": ["task-a"],
                "validation": [],
                "held_out": ["task-b"],
            },
            trainable_case_ids=("task-a",),
            held_out_case_ids=("task-b",),
        ),
    )
    candidate = _candidate(
        "---\nname: demo\n---\n# Demo\n",
        candidate_id="cand-1",
    )
    request = build_replay_request(
        run_id="run-members",
        workspace_root=tmp_path,
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay-skills",
        dataset=dataset,
    )

    result = await AWorldCliCandidateReplayBackend(
        executor=fake_executor
    ).replay_candidate(
        request,
        candidate=candidate,
        dataset=dataset,
    )

    assert [(call.task_id, call.variant_id) for call in calls] == [
        ("task-a", "baseline"),
        ("task-b", "baseline"),
        ("task-a", "cand-1"),
        ("task-b", "cand-1"),
    ]
    assert [member.case_id for member in result.member_results] == [
        "task-a",
        "task-b",
    ]
    assert len({Path(call.artifact_dir) for call in calls}) == 4
    assert len({Path(call.artifact_dir).parent for call in calls}) == 2

    paired = build_paired_replay_dataset(
        dataset=dataset,
        replay_result=result,
        candidate=candidate,
    )

    assert [case.case_id for case in paired.cases] == ["task-a", "task-b"]
    for case in paired.cases:
        variants = case.metadata["variant_trajectories"]
        assert variants["baseline"][0]["action"]["content"] == (
            f"{case.case_id}:baseline"
        )
        assert variants["cand-1"][0]["action"]["content"] == (
            f"{case.case_id}:cand-1"
        )
    assert paired.recipe.splits == {
        "train": ["task-a"],
        "validation": [],
        "held_out": ["task-b"],
    }
    assert paired.recipe.trainable_case_ids == ("task-a",)
    assert paired.recipe.held_out_case_ids == ("task-b",)


@pytest.mark.asyncio
async def test_multi_member_replay_distributes_repetition_budget_across_members(
    tmp_path: Path,
) -> None:
    calls: list[ReplayExecutionRequest] = []

    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        calls.append(request)
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[
                {
                    "state": {"input": request.task_input},
                    "action": {"content": f"{request.task_id}:{request.variant_id}"},
                    "reward": {"status": "ok"},
                }
            ],
        )

    dataset = SelfEvolveDataset(
        cases=tuple(
            EvalCase(case_id=f"task-{index}", input=f"Replay task {index}")
            for index in range(1, 5)
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 4},
            split_seed="seed",
            splits={
                "train": ["task-1", "task-2"],
                "validation": ["task-3"],
                "held_out": ["task-4"],
            },
        ),
    )
    candidate = _candidate("---\nname: demo\n---\n# Demo\n")
    request = build_replay_request(
        run_id="run-distributed-repetitions",
        workspace_root=tmp_path,
        target=candidate.target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay-skills",
        dataset=dataset,
        baseline_repetitions=2,
        candidate_repetitions=3,
    )

    result = await AWorldCliCandidateReplayBackend(executor=fake_executor).replay_candidate(
        request,
        candidate=candidate,
        dataset=dataset,
    )

    assert [(call.task_id, call.variant_id) for call in calls] == [
        ("task-1", "baseline"),
        ("task-2", "baseline"),
        ("task-3", "baseline"),
        ("task-4", "baseline"),
        ("task-1", "cand-1"),
        ("task-2", "cand-1"),
        ("task-3", "cand-1"),
        ("task-4", "cand-1"),
    ]
    assert result.baseline.metrics["repetition_count"] == 4
    assert result.candidate.metrics["repetition_count"] == 4
    assert all(
        member.baseline.metrics["repetition_count"] == 1
        and member.candidate.metrics["repetition_count"] == 1
        for member in result.member_results
    )


@pytest.mark.asyncio
async def test_multi_member_replay_reports_failed_case_without_masking_it(
    tmp_path: Path,
) -> None:
    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        if request.task_id == "task-b" and request.variant_id == "cand-1":
            return ReplayExecutionResult(
                status="failed",
                trajectory=[],
                failure={"type": "TaskFailure", "reason": "task-b failed"},
            )
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": request.task_id}}],
        )

    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(case_id="task-a", input="Replay task A"),
            EvalCase(case_id="task-b", input="Replay task B"),
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 2},
            split_seed="seed",
            splits={"train": ["task-a", "task-b"], "validation": [], "held_out": []},
        ),
    )
    candidate = _candidate("---\nname: demo\n---\n# Demo\n")
    request = build_replay_request(
        run_id="run-member-failure",
        workspace_root=tmp_path,
        target=candidate.target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay-skills",
        dataset=dataset,
    )

    result = await AWorldCliCandidateReplayBackend(
        executor=fake_executor
    ).replay_candidate(request, candidate=candidate, dataset=dataset)

    assert result.succeeded is False
    assert result.candidate.status == "failed"
    assert result.candidate.metrics["successful_member_count"] == 1
    assert result.candidate.metrics["failed_member_count"] == 1
    assert result.candidate.metrics["member_failures"] == [
        {
            "case_id": "task-b",
            "failure": {"type": "TaskFailure", "reason": "task-b failed"},
        }
    ]


@pytest.mark.asyncio
async def test_load_candidate_replay_result_restores_multi_member_mapping(
    tmp_path: Path,
) -> None:
    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[
                {
                    "state": {"input": request.task_input},
                    "action": {
                        "content": f"{request.task_id}:{request.variant_id}"
                    },
                }
            ],
        )

    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(case_id="task-a", input="Replay task A"),
            EvalCase(case_id="task-b", input="Replay task B"),
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 2},
            split_seed="seed",
            splits={"train": ["task-a"], "validation": [], "held_out": ["task-b"]},
            trainable_case_ids=("task-a",),
            held_out_case_ids=("task-b",),
        ),
    )
    candidate = _candidate("---\nname: demo\n---\n# Demo\n")
    request = build_replay_request(
        run_id="run-load-members",
        workspace_root=tmp_path,
        target=candidate.target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay-skills",
        dataset=dataset,
        baseline_repetitions=2,
        candidate_repetitions=2,
    )
    await AWorldCliCandidateReplayBackend(executor=fake_executor).replay_candidate(
        request,
        candidate=candidate,
        dataset=dataset,
    )
    replay_dir = (
        tmp_path
        / ".aworld"
        / "self_evolve"
        / "run-load-members"
        / "replay"
        / "cand-1"
    )

    loaded = load_candidate_replay_result(replay_dir)

    assert loaded.succeeded is True
    assert [member.case_id for member in loaded.member_results] == [
        "task-a",
        "task-b",
    ]
    assert all(
        len(member.baseline.repetition_results) == 0
        and member.baseline.metrics["repetition_count"] == 1
        and len(member.candidate.repetition_results) == 0
        and member.candidate.metrics["repetition_count"] == 1
        for member in loaded.member_results
    )
    assert loaded.baseline.metrics["repetition_count"] == 2
    assert loaded.candidate.metrics["repetition_count"] == 2
    paired = build_paired_replay_dataset(
        dataset=dataset,
        replay_result=loaded,
        candidate=candidate,
    )
    assert {
        case.case_id.split("__replay_", 1)[0]: case.metadata[
            "variant_trajectories"
        ]["cand-1"][0]["action"]["content"].split(":", 1)[0]
        for case in paired.cases
    } == {"task-a": "task-a", "task-b": "task-b"}


@pytest.mark.asyncio
async def test_multi_member_replay_reuses_each_members_baseline(
    tmp_path: Path,
) -> None:
    calls: list[ReplayExecutionRequest] = []

    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        calls.append(request)
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": request.variant_id}}],
        )

    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(case_id="task-a", input="Replay task A"),
            EvalCase(case_id="task-b", input="Replay task B"),
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 2},
            split_seed="seed",
            splits={"train": ["task-a", "task-b"], "validation": [], "held_out": []},
        ),
    )
    first_candidate = _candidate(
        "---\nname: demo\n---\n# Demo\nFirst.\n",
        candidate_id="cand-1",
    )
    first_request = build_replay_request(
        run_id="run-reuse-members",
        workspace_root=tmp_path,
        target=first_candidate.target,
        candidate=first_candidate,
        overlay_skill_root=tmp_path / "overlay-1",
        dataset=dataset,
    )
    backend = AWorldCliCandidateReplayBackend(executor=fake_executor)
    await backend.replay_candidate(
        first_request,
        candidate=first_candidate,
        dataset=dataset,
    )
    calls.clear()
    second_candidate = _candidate(
        "---\nname: demo\n---\n# Demo\nSecond.\n",
        candidate_id="cand-2",
    )
    members_root = (
        tmp_path
        / ".aworld"
        / "self_evolve"
        / "run-reuse-members"
        / "replay"
        / "cand-1"
        / "members"
    )
    second_request = build_replay_request(
        run_id="run-reuse-members",
        workspace_root=tmp_path,
        target=second_candidate.target,
        candidate=second_candidate,
        overlay_skill_root=tmp_path / "overlay-2",
        dataset=dataset,
        baseline_replay_dir=members_root,
    )

    result = await backend.replay_candidate(
        second_request,
        candidate=second_candidate,
        dataset=dataset,
    )

    assert result.succeeded is True
    assert [(call.task_id, call.variant_id) for call in calls] == [
        ("task-a", "cand-2"),
        ("task-b", "cand-2"),
    ]
    assert all(member.baseline.succeeded for member in result.member_results)
    second_replay_dir = (
        tmp_path
        / ".aworld"
        / "self_evolve"
        / "run-reuse-members"
        / "replay"
        / "cand-2"
    )
    loaded = load_candidate_replay_result(second_replay_dir)
    assert loaded.succeeded is True
    assert [member.case_id for member in loaded.member_results] == [
        "task-a",
        "task-b",
    ]
    assert all(member.baseline.succeeded for member in loaded.member_results)


@pytest.mark.asyncio
async def test_multi_member_replay_reuses_successful_baselines_and_retries_failed_member(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str]] = []
    baseline_attempts: dict[str, int] = {}

    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        calls.append((request.task_id, request.variant_id))
        if request.variant_id == "baseline":
            baseline_attempts[request.task_id] = (
                baseline_attempts.get(request.task_id, 0) + 1
            )
            if request.task_id == "task-b" and baseline_attempts[request.task_id] == 1:
                return ReplayExecutionResult(
                    status="failed",
                    trajectory=[],
                    failure={"type": "TimeoutExpired", "reason": "replay timed out"},
                )
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[
                {
                    "state": {"input": request.task_input},
                    "action": {"content": f"{request.task_id}:{request.variant_id}"},
                }
            ],
        )

    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(case_id="task-a", input="Replay task A"),
            EvalCase(case_id="task-b", input="Replay task B"),
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 2},
            split_seed="seed",
            splits={"train": ["task-a", "task-b"], "validation": [], "held_out": []},
        ),
    )
    backend = AWorldCliCandidateReplayBackend(executor=fake_executor)
    first_candidate = _candidate(
        "---\nname: demo\n---\n# Demo\nFirst.\n",
        candidate_id="cand-1",
    )
    first_request = build_replay_request(
        run_id="run-partial-member-cache",
        workspace_root=tmp_path,
        target=first_candidate.target,
        candidate=first_candidate,
        overlay_skill_root=tmp_path / "overlay-1",
        dataset=dataset,
    )

    first_result = await backend.replay_candidate(
        first_request,
        candidate=first_candidate,
        dataset=dataset,
    )

    assert first_result.baseline.succeeded is False
    members_root = (
        tmp_path
        / ".aworld"
        / "self_evolve"
        / "run-partial-member-cache"
        / "replay"
        / "cand-1"
        / "members"
    )
    calls.clear()
    second_candidate = _candidate(
        "---\nname: demo\n---\n# Demo\nSecond.\n",
        candidate_id="cand-2",
    )
    second_request = build_replay_request(
        run_id="run-partial-member-cache",
        workspace_root=tmp_path,
        target=second_candidate.target,
        candidate=second_candidate,
        overlay_skill_root=tmp_path / "overlay-2",
        dataset=dataset,
        baseline_replay_dir=members_root,
    )

    second_result = await backend.replay_candidate(
        second_request,
        candidate=second_candidate,
        dataset=dataset,
    )

    assert second_result.baseline.succeeded is True
    assert calls == [
        ("task-b", "baseline"),
        ("task-a", "cand-2"),
        ("task-b", "cand-2"),
    ]


@pytest.mark.asyncio
async def test_multi_member_replay_stops_before_candidates_when_baseline_preflight_fails(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str]] = []

    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        calls.append((request.task_id, request.variant_id))
        if request.task_id == "task-b" and request.variant_id == "baseline":
            return ReplayExecutionResult(
                status="failed",
                trajectory=[],
                failure={"reason": "replay_compacted_argument_unavailable"},
            )
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": request.variant_id}}],
        )

    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(case_id="task-a", input="Replay task A"),
            EvalCase(case_id="task-b", input="Replay task B"),
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 2},
            split_seed="seed",
            splits={"train": ["task-a", "task-b"], "validation": [], "held_out": []},
        ),
    )
    candidate = _candidate(
        "---\nname: demo\n---\n# Demo\nCandidate.\n",
        candidate_id="cand-1",
    )
    request = build_replay_request(
        run_id="run-baseline-preflight",
        workspace_root=tmp_path,
        target=candidate.target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay",
        dataset=dataset,
    )

    result = await AWorldCliCandidateReplayBackend(executor=fake_executor).replay_candidate(
        request,
        candidate=candidate,
        dataset=dataset,
    )

    assert calls == [("task-a", "baseline"), ("task-b", "baseline")]
    assert result.baseline.succeeded is False
    assert all(
        member.candidate.failure
        == {
            "reason": "baseline_preflight_failed",
            "detail": "candidate replay skipped because baseline infrastructure replay failed",
        }
        for member in result.member_results
    )


def test_member_baseline_replay_dir_maps_legacy_member_root_without_manifest(
    tmp_path: Path,
) -> None:
    members_root = tmp_path / "members"
    case_id = "task-a"
    member_dir = members_root / _member_artifact_name(case_id)
    member_dir.mkdir(parents=True)
    (member_dir / "request.json").write_text(
        json.dumps(
            {
                "run_id": "old-run",
                "task_id": case_id,
                "workspace_root": str(tmp_path),
                "target": {"target_type": "skill", "target_id": "demo"},
                "candidate_id": "cand-1",
                "overlay_skill_root": str(tmp_path / "overlay"),
                "task_input": "Replay task A",
            }
        ),
        encoding="utf-8",
    )

    assert _member_baseline_replay_dir(str(members_root), case_id) == str(
        member_dir / "baseline"
    )


def test_member_baseline_replay_dir_rejects_mismatched_chained_baseline(
    tmp_path: Path,
) -> None:
    members_root = tmp_path / "members"
    case_id = "task-b"
    member_name = _member_artifact_name(case_id)
    member_dir = members_root / member_name
    member_dir.mkdir(parents=True)
    stale_replay_root = tmp_path / "old-replay"
    stale_baseline = stale_replay_root / "baseline"
    stale_baseline.mkdir(parents=True)
    (stale_replay_root / "request.json").write_text(
        json.dumps({"task_id": "task-a"}),
        encoding="utf-8",
    )
    (member_dir / "request.json").write_text(
        json.dumps(
            {
                "task_id": case_id,
                "baseline_replay_dir": str(stale_baseline),
            }
        ),
        encoding="utf-8",
    )
    (members_root / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "aworld.self_evolve.member_replay.v1",
                "members": [
                    {
                        "case_id": case_id,
                        "path": member_name,
                        "succeeded": False,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert _member_baseline_replay_dir(str(members_root), case_id) is None


def test_member_baseline_replay_dir_follows_matching_chained_baseline(
    tmp_path: Path,
) -> None:
    members_root = tmp_path / "members"
    case_id = "task-a"
    member_name = _member_artifact_name(case_id)
    member_dir = members_root / member_name
    member_dir.mkdir(parents=True)
    prior_replay_root = tmp_path / "old-replay"
    prior_baseline = prior_replay_root / "baseline"
    prior_baseline.mkdir(parents=True)
    (prior_baseline / "trajectory.json").write_text(
        json.dumps([{"action": {"content": "stored baseline"}}]),
        encoding="utf-8",
    )
    (prior_baseline / "metrics.json").write_text("{}\n", encoding="utf-8")
    (prior_replay_root / "request.json").write_text(
        json.dumps({"task_id": case_id}),
        encoding="utf-8",
    )
    (member_dir / "request.json").write_text(
        json.dumps(
            {
                "task_id": case_id,
                "baseline_replay_dir": str(prior_baseline),
            }
        ),
        encoding="utf-8",
    )
    (members_root / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "aworld.self_evolve.member_replay.v1",
                "members": [
                    {
                        "case_id": case_id,
                        "path": member_name,
                        "succeeded": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert _member_baseline_replay_dir(str(members_root), case_id) == str(
        prior_baseline
    )


@pytest.mark.asyncio
async def test_multi_member_replay_paths_do_not_collide_after_sanitization(
    tmp_path: Path,
) -> None:
    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": request.task_id}}],
        )

    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(case_id="task/a", input="Task A"),
            EvalCase(case_id="task?a", input="Task B"),
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 2},
            split_seed="seed",
            splits={"train": ["task/a", "task?a"], "validation": [], "held_out": []},
        ),
    )
    candidate = _candidate("---\nname: demo\n---\n# Demo\n")
    request = build_replay_request(
        run_id="run-collision",
        workspace_root=tmp_path,
        target=candidate.target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay",
        dataset=dataset,
    )
    await AWorldCliCandidateReplayBackend(executor=fake_executor).replay_candidate(
        request,
        candidate=candidate,
        dataset=dataset,
    )
    replay_dir = (
        tmp_path
        / ".aworld"
        / "self_evolve"
        / "run-collision"
        / "replay"
        / "cand-1"
    )

    loaded = load_candidate_replay_result(replay_dir)

    assert [member.case_id for member in loaded.member_results] == ["task/a", "task?a"]
    assert [
        member.candidate.trajectory[0]["action"]["content"]
        for member in loaded.member_results
    ] == ["task/a", "task?a"]


@pytest.mark.asyncio
async def test_replay_excludes_framework_advisory_members_from_paired_dataset(
    tmp_path: Path,
) -> None:
    calls: list[ReplayExecutionRequest] = []

    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        calls.append(request)
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": request.task_id}}],
        )

    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(case_id="user-task", input="Replay user task"),
            EvalCase(
                case_id="prior-run-summary",
                input={"status": "rejected"},
                source={"kind": "prior_self_evolve_run", "framework_generated": True},
            ),
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 2},
            split_seed="seed",
            splits={
                "train": ["user-task", "prior-run-summary"],
                "validation": [],
                "held_out": [],
            },
        ),
    )
    candidate = _candidate("---\nname: demo\n---\n# Demo\n")
    request = build_replay_request(
        run_id="run-advisory",
        workspace_root=tmp_path,
        target=candidate.target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay",
        dataset=dataset,
    )

    result = await AWorldCliCandidateReplayBackend(executor=fake_executor).replay_candidate(
        request,
        candidate=candidate,
        dataset=dataset,
    )
    paired = build_paired_replay_dataset(
        dataset=dataset,
        replay_result=result,
        candidate=candidate,
    )

    assert [(call.task_id, call.variant_id) for call in calls] == [
        ("user-task", "baseline"),
        ("user-task", "cand-1"),
    ]
    assert [member.case_id for member in result.member_results] == ["user-task"]
    assert [case.case_id for case in paired.cases] == ["user-task"]


@pytest.mark.asyncio
async def test_aworld_cli_candidate_replay_backend_aggregates_evidence_metrics(
    tmp_path: Path,
) -> None:
    async def fake_executor(request):
        compacted = request.variant_id == "cand-1-2"
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[
                {
                    "state": {"input": request.task_input},
                    "action": {"content": request.variant_id},
                    "reward": {"status": "ok"},
                }
            ],
            metrics={
                "evidence_compacted": compacted,
                "evidence_strategy_passed": not compacted,
                "evidence_compaction_signals": (
                    ["tool_output_compacted"] if compacted else []
                ),
            },
        )

    request = CandidateReplayRequest(
        run_id="run-evidence-metrics",
        task_id="task-1",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate_id="cand-1",
        overlay_skill_root=str(tmp_path / "overlay-skills"),
        task_input="Replay this task",
        baseline_repetitions=1,
        candidate_repetitions=3,
    )

    result = await AWorldCliCandidateReplayBackend(executor=fake_executor).replay_candidate(
        request,
        candidate=_candidate("---\nname: demo\n---\n# Demo\n", candidate_id="cand-1"),
        dataset=SelfEvolveDataset(
            cases=(EvalCase(case_id="task-1", input="Replay this task"),),
            recipe=DatasetRecipe(
                source={"kind": "test", "case_count": 1},
                split_seed="seed",
                splits={"train": ["task-1"], "validation": [], "held_out": []},
            ),
        ),
    )

    assert result.candidate.succeeded is True
    assert result.candidate.metrics["evidence_compacted"] is False
    assert result.candidate.metrics["evidence_strategy_passed"] is True
    assert result.candidate.metrics["evidence_retry_count"] == 1.0
    assert result.candidate.metrics["evidence_compaction_signals"] == [
        "tool_output_compacted"
    ]


@pytest.mark.asyncio
async def test_aworld_cli_candidate_replay_backend_fails_when_evidence_retries_still_compact(
    tmp_path: Path,
) -> None:
    async def fake_executor(request):
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[
                {
                    "state": {"input": request.task_input},
                    "action": {"content": request.variant_id},
                    "reward": {"status": "ok"},
                }
            ],
            metrics={
                "evidence_compacted": True,
                "evidence_strategy_passed": False,
                "evidence_compaction_signals": ["tool_output_compacted"],
            },
        )

    request = CandidateReplayRequest(
        run_id="run-evidence-hard-fail",
        task_id="task-1",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate_id="cand-1",
        overlay_skill_root=str(tmp_path / "overlay-skills"),
        task_input="Replay this task",
        baseline_repetitions=1,
        candidate_repetitions=1,
    )

    result = await AWorldCliCandidateReplayBackend(executor=fake_executor).replay_candidate(
        request,
        candidate=_candidate("---\nname: demo\n---\n# Demo\n", candidate_id="cand-1"),
        dataset=SelfEvolveDataset(
            cases=(EvalCase(case_id="task-1", input="Replay this task"),),
            recipe=DatasetRecipe(
                source={"kind": "test", "case_count": 1},
                split_seed="seed",
                splits={"train": ["task-1"], "validation": [], "held_out": []},
            ),
        ),
    )

    assert result.succeeded is False
    assert result.candidate.status == "failed"
    assert result.candidate.failure["reason"] == "evidence_quality_failed"
    assert result.candidate.metrics["evidence_retry_count"] == 1
    assert result.candidate.metrics["evidence_compaction_signals"] == [
        "tool_output_compacted"
    ]


@pytest.mark.asyncio
async def test_aworld_cli_candidate_replay_backend_runs_baseline_and_candidate_with_skill_roots(
    tmp_path: Path,
) -> None:
    calls = []

    async def fake_executor(request):
        calls.append(request)
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[
                {
                    "state": {"input": request.task_input},
                    "action": {"content": f"{request.variant_id} output"},
                    "reward": {"status": "ok"},
                }
            ],
            metrics={"score": 0.9 if request.variant_id == "cand-1" else 0.4},
            stdout=f"{request.variant_id} stdout",
            stderr="",
        )

    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="task-1", input={"content": "Replay this task"}),),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 1},
            split_seed="seed",
            splits={"train": ["task-1"], "validation": [], "held_out": []},
        ),
    )
    request = CandidateReplayRequest(
        run_id="run-1",
        task_id="task-1",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(
            target_type="skill",
            target_id="demo",
            path=str(tmp_path / "skills" / "demo" / "SKILL.md"),
        ),
        candidate_id="cand-1",
        overlay_skill_root=str(tmp_path / "overlay-skills"),
        baseline_skill_root=str(tmp_path / "skills"),
        task_input={"content": "Replay this task"},
        agent="Aworld",
        timeout_seconds=42,
        max_steps=5,
        max_tokens=100,
    )

    backend = AWorldCliCandidateReplayBackend(executor=fake_executor)

    result = await backend.replay_candidate(
        request,
        candidate=_candidate("---\nname: demo\n---\n# Demo\n", candidate_id="cand-1"),
        dataset=dataset,
    )

    assert result.succeeded is True
    assert [call.variant_id for call in calls] == ["baseline", "cand-1"]
    assert calls[0].skill_root == str(tmp_path / "skills")
    assert calls[1].skill_root == str(tmp_path / "overlay-skills")
    assert calls[0].task_text == "Replay this task"
    assert calls[1].agent == "Aworld"
    assert calls[1].timeout_seconds == 42
    assert result.baseline.trajectory[0]["action"]["content"] == "baseline output"
    assert result.candidate.trajectory[0]["action"]["content"] == "cand-1 output"

    replay_dir = tmp_path / ".aworld" / "self_evolve" / "run-1" / "replay" / "cand-1"
    assert (replay_dir / "request.json").exists()
    assert (replay_dir / "baseline" / "stdout.txt").read_text(encoding="utf-8") == "baseline stdout"
    assert (replay_dir / "cand-1" / "metrics.json").exists()


@pytest.mark.asyncio
async def test_aworld_cli_candidate_replay_backend_leaves_baseline_loader_default(
    tmp_path: Path,
) -> None:
    calls = []

    async def fake_executor(request):
        calls.append(request)
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": request.variant_id}}],
        )

    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="task-1", input={"content": "Replay this task"}),),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 1},
            split_seed="seed",
            splits={"train": ["task-1"], "validation": [], "held_out": []},
        ),
    )
    request = CandidateReplayRequest(
        run_id="run-1",
        task_id="task-1",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(
            target_type="skill",
            target_id="draft-skill",
            path=str(
                tmp_path
                / ".aworld"
                / "self_evolve"
                / "drafts"
                / "skills"
                / "draft-skill"
                / "SKILL.md"
            ),
        ),
        candidate_id="cand-1",
        overlay_skill_root=str(tmp_path / "overlay-skills"),
        baseline_skill_root=None,
        task_input={"content": "Replay this task"},
    )

    result = await AWorldCliCandidateReplayBackend(executor=fake_executor).replay_candidate(
        request,
        candidate=_candidate("---\nname: draft-skill\n---\n# Draft\n", candidate_id="cand-1"),
        dataset=dataset,
    )

    assert result.succeeded is True
    assert [call.variant_id for call in calls] == ["baseline", "cand-1"]
    assert calls[0].skill_root is None
    assert calls[1].skill_root == str(tmp_path / "overlay-skills")


@pytest.mark.asyncio
async def test_aworld_cli_candidate_replay_backend_logs_replay_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages = []
    monkeypatch.setattr(
        "aworld.self_evolve.replay.logger.info",
        messages.append,
    )

    async def fake_executor(request):
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[
                {
                    "state": {"input": request.task_input},
                    "action": {"content": request.variant_id},
                    "reward": {"status": "ok"},
                }
            ],
        )

    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="task-1", input="Replay this task"),),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 1},
            split_seed="seed",
            splits={"train": ["task-1"], "validation": [], "held_out": []},
        ),
    )
    request = CandidateReplayRequest(
        run_id="run-logs",
        task_id="task-1",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate_id="cand-1",
        overlay_skill_root=str(tmp_path / "overlay-skills"),
        task_input="Replay this task",
    )

    await AWorldCliCandidateReplayBackend(executor=fake_executor).replay_candidate(
        request,
        candidate=_candidate("---\nname: demo\n---\n# Demo\n", candidate_id="cand-1"),
        dataset=dataset,
    )

    assert any("self_evolve.replay.start" in message for message in messages)
    assert any(
        "self_evolve.replay.repetition.start" in message and "variant_id=baseline" in message
        for message in messages
    )
    assert any(
        "self_evolve.replay.repetition.end" in message and "variant_id=cand-1" in message
        for message in messages
    )
    assert any("self_evolve.replay.end" in message for message in messages)


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_requests_machine_readable_trajectory_and_disables_auto_drain(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "Aworld", "pre_agent": "runner"},
            "state": {"input": {"content": "Replay this task"}},
            "action": {"content": "Replay completed.", "is_agent_finished": "True"},
            "reward": {"status": "ok"},
        }
    ]

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="human output\n"
            + json.dumps(
                {
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay.subprocess.run", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
            agent="Aworld",
        )
    )

    assert result.succeeded is True
    assert result.trajectory == trajectory
    assert "--emit-trajectory" in captured["command"]
    task_index = captured["command"].index("--task") + 1
    task_text = captured["command"][task_index]
    assert task_text.startswith("Replay this task")
    assert "Self-evolve replay evidence requirements" in task_text
    assert "artifact-first" in task_text
    assert "bounded structured summaries" in task_text
    assert "compacted" in task_text
    assert "Self-evolve replay runtime contract" in task_text
    assert "Task-plane operations required by the original task are allowed" in task_text
    assert "explicitly authorizes a control-plane operation" in task_text
    assert "Do not terminate, restart, reconfigure, or replace externally managed prerequisites" in task_text
    assert "Do not copy or substitute credentials, sessions, profiles, or private state" in task_text
    assert "fail the replay with a prerequisite-unavailable reason" in task_text
    assert (
        "Once the requested output artifact and a valid evidence manifest exist, "
        "stop evidence collection and return the final answer"
    ) in task_text
    assert (
        "For bounded replay validation, prefer the smallest representative evidence path"
    ) in task_text
    assert (
        "After the first successful structured extraction, immediately persist replay "
        "artifacts"
    ) in task_text
    assert captured["kwargs"]["cwd"] == str(tmp_path)
    assert captured["kwargs"]["env"]["AWORLD_SELF_EVOLVE_AUTO_DRAIN"] == "0"
    assert captured["kwargs"]["env"]["AWORLD_SELF_EVOLVE_REPLAY_ARTIFACT_DIR"] == str(
        tmp_path / "artifacts"
    )
    assert captured["kwargs"]["env"]["AWORLD_SELF_EVOLVE_EVIDENCE_MANIFEST"] == str(
        tmp_path / "artifacts" / "evidence_manifest.jsonl"
    )
    assert captured["kwargs"]["env"]["AWORLD_LOG_PATH"] == str(
        tmp_path / "artifacts" / "logs"
    )
    assert captured["kwargs"]["env"]["AWORLD_TRAJECTORY_LOG_DISABLED"] == "1"
    assert captured["kwargs"]["env"]["AWORLD_TOOL_CALL_LIMIT"] == "24"
    assert "AWORLD_SELF_EVOLVE_REPLAY_ARTIFACT_DIR" in task_text
    assert str(tmp_path / "artifacts") in task_text
    assert str(tmp_path / "artifacts" / "evidence_manifest.jsonl") in task_text
    assert "evidence_manifest.jsonl" in task_text


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_normalizes_stale_workspace_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}
    workspace_root = tmp_path / "aworld"
    workspace_root.mkdir()
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "Aworld", "pre_agent": "runner"},
            "state": {"input": {"content": "Replay this task"}},
            "action": {"content": "Replay completed.", "is_agent_finished": "True"},
            "reward": {"status": "ok"},
        }
    ]

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay.subprocess.run", fake_run)

    await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(workspace_root),
            task_input={"content": "Replay this task"},
            task_text=(
                "Use /Users/manwu/Documents/workspace/aworld/examples/skill_agent/"
                "skills/x-scraper and write /Users/manwu/Documents/workspace/"
                "aworld/x_ai_daily_extra.json"
            ),
            skill_root=str(workspace_root / "skills"),
            artifact_dir=str(workspace_root / "artifacts"),
        )
    )

    task_index = captured["command"].index("--task") + 1
    task_text = captured["command"][task_index]
    assert "/Users/manwu/Documents/workspace/aworld" not in task_text
    assert str(workspace_root / "examples" / "skill_agent" / "skills" / "x-scraper") in task_text
    assert str(workspace_root / "x_ai_daily_extra.json") in task_text


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_accepts_compacted_markers_with_valid_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "Aworld", "pre_agent": "runner"},
            "state": {
                "messages": [
                    {
                        "role": "tool",
                        "content": "Tool output compacted for context reuse.",
                    }
                ]
            },
            "action": {"content": "Replay completed.", "is_agent_finished": "True"},
            "reward": {"status": "ok"},
        }
    ]

    def fake_run(command, **kwargs):
        artifact_dir = tmp_path / "artifacts"
        artifact_dir.mkdir(parents=True)
        evidence_path = artifact_dir / "episode_extract.txt"
        evidence_path.write_text("bounded non-compacted evidence excerpt", encoding="utf-8")
        (artifact_dir / "evidence_manifest.jsonl").write_text(
            json.dumps(
                {
                    "source_id": "episode_raw",
                    "artifact_path": "episode_extract.txt",
                    "extraction_method": "raw_download",
                    "size_bytes": evidence_path.stat().st_size,
                }
            )
            + "\n"
            + json.dumps(
                {
                    "source_id": "episode",
                    "artifact_path": "episode_extract.txt",
                    "extraction_method": "bounded_extract",
                    "bounded_excerpts": {
                        "summary": "bounded non-compacted evidence excerpt",
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="Tool output compacted for context reuse.\n"
            + json.dumps(
                {
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay.subprocess.run", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
        )
    )

    assert result.succeeded is True
    assert result.failure is None
    assert result.metrics["evidence_compacted"] is True
    assert result.metrics["evidence_strategy_passed"] is True
    assert result.metrics["evidence_manifest_present"] is True
    assert result.metrics["evidence_manifest_entry_count"] == 2
    assert "evidence_manifest_invalid_entry_count" not in result.metrics
    bundle = json.loads((tmp_path / "artifacts" / "evidence_bundle.json").read_text())
    assert bundle["valid"] is True
    assert bundle["entries"][0]["bounded_evidence"]["source"] == "artifact_preview"
    assert (
        bundle["entries"][0]["bounded_evidence"]["bounded_excerpt"]
        == "bounded non-compacted evidence excerpt"
    )
    assert bundle["entries"][0]["bounded_evidence"]["truncated"] is False


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_writes_canonical_evidence_bundle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "Aworld", "pre_agent": "runner"},
            "state": {
                "messages": [
                    {
                        "role": "tool",
                        "content": "Tool output compacted for context reuse.",
                    }
                ]
            },
            "action": {"content": "Replay completed.", "is_agent_finished": "True"},
            "reward": {"status": "ok"},
        }
    ]

    def fake_run(command, **kwargs):
        artifact_dir = tmp_path / "artifacts"
        artifact_dir.mkdir(parents=True)
        evidence_path = artifact_dir / "bounded_extract.txt"
        evidence_path.write_text("bounded non-compacted evidence excerpt", encoding="utf-8")
        (artifact_dir / "evidence_manifest.jsonl").write_text(
            json.dumps(
                {
                    "source_id": "source-1",
                    "evidence_type": "file",
                    "artifact_path": "bounded_extract.txt",
                    "extraction_method": "bounded_extract",
                    "bounded_excerpt": "bounded non-compacted evidence excerpt",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="Tool output compacted for context reuse.\n"
            + json.dumps(
                {
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay.subprocess.run", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
        )
    )

    bundle_path = tmp_path / "artifacts" / "evidence_bundle.json"
    evidence_path = tmp_path / "artifacts" / "bounded_extract.txt"
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))

    assert result.succeeded is True
    assert result.metrics["evidence_compacted"] is True
    assert result.metrics["evidence_strategy_passed"] is True
    assert result.metrics["evidence_bundle_present"] is True
    assert result.metrics["evidence_bundle_valid"] is True
    assert result.metrics["evidence_bundle_entry_count"] == 1
    assert result.metrics["evidence_bundle_path"] == str(bundle_path)
    assert bundle["format"] == "aworld.self_evolve.evidence_bundle"
    assert bundle["entries"][0]["source_id"] == "source-1"
    assert bundle["entries"][0]["artifact_path"] == str(evidence_path)
    assert bundle["entries"][0]["bounded_evidence"]["bounded_excerpt"] == (
        "bounded non-compacted evidence excerpt"
    )


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_accepts_non_file_evidence_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "Aworld", "pre_agent": "runner"},
            "state": {
                "messages": [
                    {
                        "role": "tool",
                        "content": "Tool output compacted for context reuse.",
                    }
                ]
            },
            "action": {"content": "Notification scheduled.", "is_agent_finished": "True"},
            "reward": {"status": "ok"},
        }
    ]

    def fake_run(command, **kwargs):
        artifact_dir = tmp_path / "artifacts"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "evidence_manifest.jsonl").write_text(
            json.dumps(
                {
                    "source_id": "scheduled_notification",
                    "evidence_type": "metadata",
                    "extraction_method": "scheduler_response",
                    "metadata": {
                        "operation": "schedule_notification",
                        "reference_id": "job-123",
                        "status": "scheduled",
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="Tool output compacted for context reuse.\n"
            + json.dumps(
                {
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay.subprocess.run", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
        )
    )

    assert result.succeeded is True
    assert result.metrics["evidence_bundle_valid"] is True
    bundle = json.loads((tmp_path / "artifacts" / "evidence_bundle.json").read_text())
    assert bundle["entries"] == [
        {
            "bounded_evidence": {},
            "evidence_type": "metadata",
            "extraction_method": "scheduler_response",
            "metadata": {
                "operation": "schedule_notification",
                "reference_id": "job-123",
                "status": "scheduled",
            },
            "source_id": "scheduled_notification",
        }
    ]


def test_replay_evidence_manifest_rejects_oversized_metadata(tmp_path: Path) -> None:
    reason = _invalid_evidence_manifest_entry_reason(
        {
            "source_id": "operation_result",
            "evidence_type": "metadata",
            "extraction_method": "structured_result",
            "metadata": {"value": "x" * 20_000},
        },
        artifact_dir=tmp_path,
    )

    assert reason == "metadata exceeds bounded evidence limit"


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_reports_compacted_argument_without_evidence_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "Aworld", "pre_agent": "runner"},
            "state": {
                "messages": [
                    {
                        "role": "tool",
                        "content": (
                            "replay_compacted_argument_unavailable: tool call argument "
                            "contains compacted_string_field"
                        ),
                    }
                ]
            },
            "action": {"content": "Replay stopped.", "is_agent_finished": "True"},
            "reward": {"status": "failed"},
        }
    ]

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay.subprocess.run", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
        )
    )

    assert result.succeeded is False
    assert result.failure == {
        "reason": "replay_compacted_argument_unavailable",
        "detail": "replay stopped before executing compacted tool arguments",
    }
    assert result.metrics["replay_compacted_argument_blocked"] is True


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_archives_workspace_manifest_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "Aworld", "pre_agent": "runner"},
            "state": {
                "messages": [
                    {
                        "role": "tool",
                        "content": "Tool output compacted for context reuse.",
                    }
                ]
            },
            "action": {"content": "Replay completed.", "is_agent_finished": "True"},
            "reward": {"status": "ok"},
        }
    ]

    def fake_run(command, **kwargs):
        workspace_root = tmp_path / "workspace"
        workspace_root.mkdir(parents=True, exist_ok=True)
        artifact_dir = workspace_root / "artifacts"
        artifact_dir.mkdir(parents=True)
        output_path = workspace_root / "x_ai_daily_extra.json"
        output_path.write_text(
            json.dumps({"meta": {"count": 1}, "tweets": [{"text": "AI news"}]}),
            encoding="utf-8",
        )
        (artifact_dir / "evidence_manifest.jsonl").write_text(
            json.dumps(
                {
                    "source_id": "workspace_output",
                    "artifact_path": str(output_path),
                    "extraction_method": "task_output_json",
                    "fields_used": ["content"],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="Tool output compacted for context reuse.\n"
            + json.dumps(
                {
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay.subprocess.run", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path / "workspace"),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "workspace" / "skills"),
            artifact_dir=str(tmp_path / "workspace" / "artifacts"),
        )
    )

    assert result.succeeded is True
    assert result.failure is None
    assert result.metrics["evidence_manifest_entry_count"] == 1
    assert result.metrics["evidence_manifest_archived_entry_count"] == 1
    assert "evidence_manifest_invalid_entry_count" not in result.metrics

    bundle = json.loads(
        (tmp_path / "workspace" / "artifacts" / "evidence_bundle.json").read_text(
            encoding="utf-8"
        )
    )
    archived_path = Path(bundle["entries"][0]["artifact_path"])
    assert archived_path.is_relative_to(tmp_path / "workspace" / "artifacts")
    assert archived_path.exists()
    assert bundle["entries"][0]["bounded_evidence"]["source"] == "artifact_preview"


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_rejects_untrusted_manifest_artifact_outside_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "Aworld", "pre_agent": "runner"},
            "state": {
                "messages": [
                    {
                        "role": "tool",
                        "content": "Tool output compacted for context reuse.",
                    }
                ]
            },
            "action": {"content": "Replay completed.", "is_agent_finished": "True"},
            "reward": {"status": "ok"},
        }
    ]

    def fake_run(command, **kwargs):
        workspace_root = tmp_path / "workspace"
        workspace_root.mkdir(parents=True, exist_ok=True)
        artifact_dir = workspace_root / "artifacts"
        artifact_dir.mkdir(parents=True)
        outside_path = tmp_path / "outside.txt"
        outside_path.write_text("secret should not be allowlisted", encoding="utf-8")
        (artifact_dir / "evidence_manifest.jsonl").write_text(
            json.dumps(
                {
                    "source_id": "outside",
                    "artifact_path": str(outside_path),
                    "extraction_method": "outside_file",
                    "fields_used": ["content"],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="Tool output compacted for context reuse.\n"
            + json.dumps(
                {
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay.subprocess.run", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path / "workspace"),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "workspace" / "skills"),
            artifact_dir=str(tmp_path / "workspace" / "artifacts"),
        )
    )

    assert result.succeeded is False
    assert result.failure["reason"] == "evidence_quality_failed"
    assert result.metrics["evidence_manifest_entry_count"] == 0
    assert result.metrics["evidence_manifest_invalid_entry_count"] == 1
    assert result.metrics["evidence_manifest_invalid_reasons"] == [
        "line 1: artifact_path is outside trusted replay/workspace directories"
    ]


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_accepts_bounded_excerpt_for_outside_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "Aworld", "pre_agent": "runner"},
            "state": {
                "messages": [
                    {
                        "role": "tool",
                        "content": "Tool output compacted for context reuse.",
                    }
                ]
            },
            "action": {"content": "Replay completed.", "is_agent_finished": "True"},
            "reward": {"status": "ok"},
        }
    ]

    def fake_run(command, **kwargs):
        workspace_root = tmp_path / "workspace"
        workspace_root.mkdir(parents=True, exist_ok=True)
        artifact_dir = workspace_root / "artifacts"
        artifact_dir.mkdir(parents=True)
        outside_path = tmp_path / "scrape_stderr.log"
        outside_path.write_text("large outside log should not be read", encoding="utf-8")
        (artifact_dir / "evidence_manifest.jsonl").write_text(
            json.dumps(
                {
                    "source_id": "scrape_stderr_log",
                    "artifact_path": str(outside_path),
                    "extraction_method": "stderr capture",
                    "fields": ["scroll_rounds", "final_total", "ai_count"],
                    "bounded_excerpt": (
                        "search flow: 10 scrolls, 121 raw -> 20 deduped; "
                        "RESULT: total=20, ai_count=16"
                    ),
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="Tool output compacted for context reuse.\n"
            + json.dumps(
                {
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay.subprocess.run", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path / "workspace"),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "workspace" / "skills"),
            artifact_dir=str(tmp_path / "workspace" / "artifacts"),
        )
    )

    assert result.succeeded is True
    assert result.failure is None
    assert result.metrics["evidence_manifest_entry_count"] == 1
    assert "evidence_manifest_invalid_entry_count" not in result.metrics
    bundle = json.loads(
        (tmp_path / "workspace" / "artifacts" / "evidence_bundle.json").read_text(
            encoding="utf-8"
        )
    )
    assert bundle["valid"] is True
    assert bundle["entries"][0]["bounded_evidence"]["bounded_excerpt"].startswith(
        "search flow"
    )
    assert bundle["entries"][0]["bounded_evidence"]["fields"] == [
        "scroll_rounds",
        "final_total",
        "ai_count",
    ]


def test_replay_aggregate_metrics_include_bundle_validity() -> None:
    from aworld.self_evolve.replay import _aggregate_variant_results

    artifact_dir = Path("/tmp/self-evolve-replay-aggregate")
    results = [
        ReplayVariantResult(
            variant_id="cand-1",
            status="succeeded",
            trajectory=[{"action": {"content": "answer 1"}}],
            metrics={
                "evidence_bundle_valid": True,
                "evidence_bundle_entry_count": 2,
                "evidence_bundle_path": "/tmp/bundle-1.json",
            },
        ),
        ReplayVariantResult(
            variant_id="cand-2",
            status="succeeded",
            trajectory=[{"action": {"content": "answer 2"}}],
            metrics={
                "evidence_bundle_valid": True,
                "evidence_bundle_entry_count": 4,
                "evidence_bundle_path": "/tmp/bundle-2.json",
            },
        ),
    ]

    aggregate = _aggregate_variant_results(
        base_variant_id="candidate",
        results=results,
        artifact_dir=artifact_dir,
    )

    assert aggregate.metrics["evidence_bundle_valid"] is True
    assert aggregate.metrics["evidence_bundle_valid_values"] == [True, True]
    assert aggregate.metrics["evidence_bundle_entry_count"] == 3.0
    assert aggregate.metrics["evidence_bundle_entry_count_values"] == [2.0, 4.0]
    assert aggregate.metrics["evidence_bundle_path"] == "/tmp/bundle-2.json"


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_rejects_compacted_evidence_without_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "Aworld", "pre_agent": "runner"},
            "state": {
                "messages": [
                    {
                        "role": "tool",
                        "content": "Tool output compacted for context reuse.",
                    }
                ]
            },
            "action": {"content": "Replay completed.", "is_agent_finished": "True"},
            "reward": {"status": "ok"},
        }
    ]

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="Tool output compacted for context reuse.\n"
            + json.dumps(
                {
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay.subprocess.run", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
        )
    )

    assert result.succeeded is False
    assert result.failure["reason"] == "evidence_quality_failed"
    assert result.metrics["evidence_compacted"] is True
    assert result.metrics["evidence_strategy_passed"] is False
    assert result.metrics["evidence_compaction_signals"] == [
        "tool_output_compacted"
    ]


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_rejects_summary_synthetic_trajectory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "trajectory": [
                        {
                            "state": {"input": {"content": "Replay this task"}},
                            "action": {"content": "summary only", "tool_calls": []},
                            "reward": {"status": "ok"},
                        }
                    ],
                    "trajectory_capture_mode": "summary_synthetic",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay.subprocess.run", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
            agent="Aworld",
        )
    )

    assert result.succeeded is False
    assert result.failure == {
        "reason": "trajectory_capture_mode_unsupported",
        "detail": "self-evolve replay requires TaskResponse.trajectory evidence",
        "trajectory_capture_mode": "summary_synthetic",
    }


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_decodes_timeout_output_bytes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=kwargs["timeout"],
            output=b"partial stdout",
            stderr=b"partial stderr",
        )

    monkeypatch.setattr("aworld.self_evolve.replay.subprocess.run", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
            timeout_seconds=1,
        )
    )

    assert result.succeeded is False
    assert result.stdout == "partial stdout"
    assert result.stderr == "partial stderr"
    assert result.failure == {"type": "TimeoutExpired", "reason": "replay timed out"}


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_recovers_timeout_with_valid_artifact_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(command, **kwargs):
        artifact_dir = tmp_path / "artifacts"
        artifact_dir.mkdir(parents=True)
        evidence_path = artifact_dir / "x_ai_daily_extra.json"
        evidence_path.write_text(
            json.dumps(
                {
                    "meta": {"count": 1, "ai_related_count": 1},
                    "tweets": [
                        {
                            "author_name": "A",
                            "author_handle": "@a",
                            "time": "now",
                            "text": "OpenAI agent update",
                            "link": "https://x.com/a/status/1",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (artifact_dir / "evidence_manifest.jsonl").write_text(
            json.dumps(
                {
                    "source_id": "final_output",
                    "artifact_path": "x_ai_daily_extra.json",
                    "extraction_method": "bounded_replay_extract",
                    "fields": ["meta.count", "meta.ai_related_count", "tweets[].link"],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=kwargs["timeout"],
            output=b"partial stdout",
            stderr=b"partial stderr",
        )

    monkeypatch.setattr("aworld.self_evolve.replay.subprocess.run", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
            timeout_seconds=1,
        )
    )

    assert result.succeeded is True
    assert result.failure is None
    assert result.stdout == "partial stdout"
    assert result.stderr == "partial stderr"
    assert result.metrics["timeout_recovered_with_artifact_evidence"] is True
    assert result.metrics["evidence_bundle_valid"] is True
    assert result.trajectory == [
        {
            "state": {"input": {"content": "Replay this task"}},
            "action": {
                "content": "Replay completed from artifact-backed evidence manifest.",
                "is_agent_finished": "True",
            },
            "reward": {"status": "ok"},
            "meta": {
                "trajectory_capture_mode": "artifact_manifest",
                "evidence_manifest_path": str(tmp_path / "artifacts" / "evidence_manifest.jsonl"),
                "evidence_bundle_path": str(tmp_path / "artifacts" / "evidence_bundle.json"),
            },
        }
    ]


@pytest.mark.asyncio
async def test_aworld_cli_candidate_replay_backend_returns_structured_failure(
    tmp_path: Path,
) -> None:
    async def failing_executor(request):
        if request.variant_id == "baseline":
            return ReplayExecutionResult(
                status="succeeded",
                trajectory=[{"action": {"content": "baseline"}}],
            )
        return ReplayExecutionResult(
            status="failed",
            trajectory=[],
            failure={"reason": "missing model configuration"},
            stdout="",
            stderr="No model configuration",
        )

    request = CandidateReplayRequest(
        run_id="run-failure",
        task_id="task-1",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate_id="cand-1",
        overlay_skill_root=str(tmp_path / "overlay-skills"),
        task_input="Replay this task",
    )

    result = await AWorldCliCandidateReplayBackend(executor=failing_executor).replay_candidate(
        request,
        candidate=_candidate("---\nname: demo\n---\n# Demo\n", candidate_id="cand-1"),
        dataset=SelfEvolveDataset(
            cases=(EvalCase(case_id="task-1", input="Replay this task"),),
            recipe=DatasetRecipe(
                source={"kind": "test", "case_count": 1},
                split_seed="seed",
                splits={"train": ["task-1"], "validation": [], "held_out": []},
            ),
        ),
    )

    assert result.succeeded is False
    assert result.candidate.status == "failed"
    assert result.candidate.failure == {"reason": "missing model configuration"}
    failure_path = (
        tmp_path
        / ".aworld"
        / "self_evolve"
        / "run-failure"
        / "replay"
        / "cand-1"
        / "cand-1"
        / "failure.json"
    )
    assert failure_path.exists()
