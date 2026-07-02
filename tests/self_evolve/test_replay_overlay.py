from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from aworld.self_evolve.datasets import EvalCase, SelfEvolveDataset
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
    assert captured["kwargs"]["cwd"] == str(tmp_path)
    assert captured["kwargs"]["env"]["AWORLD_SELF_EVOLVE_AUTO_DRAIN"] == "0"
    assert captured["kwargs"]["env"]["AWORLD_SELF_EVOLVE_REPLAY_ARTIFACT_DIR"] == str(
        tmp_path / "artifacts"
    )
    assert captured["kwargs"]["env"]["AWORLD_SELF_EVOLVE_EVIDENCE_MANIFEST"] == str(
        tmp_path / "artifacts" / "evidence_manifest.jsonl"
    )
    assert "AWORLD_SELF_EVOLVE_REPLAY_ARTIFACT_DIR" in task_text
    assert "evidence_manifest.jsonl" in task_text


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_marks_compacted_evidence(
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
