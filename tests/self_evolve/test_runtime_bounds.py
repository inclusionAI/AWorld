from __future__ import annotations

import argparse
import asyncio

import pytest

from aworld.self_evolve.datasets import EvalCase, SelfEvolveDataset
from aworld.self_evolve.replay import (
    AWorldCliCandidateReplayBackend,
    CandidateReplayRequest,
    ReplayExecutionResult,
)
from aworld.self_evolve.types import CandidateVariant, DatasetRecipe, SelfEvolveTargetRef
from aworld_cli.top_level_commands.optimize_cmd import (
    OptimizeTopLevelCommand,
    run_optimize_cli,
)


def _dataset() -> SelfEvolveDataset:
    return SelfEvolveDataset(
        cases=(EvalCase(case_id="case-1", input={"content": "replay task"}),),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 1},
            split_seed="seed",
            splits={"train": ["case-1"], "validation": [], "held_out": []},
        ),
    )


def _candidate() -> CandidateVariant:
    return CandidateVariant(
        candidate_id="cand-1",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        content="# Demo\n",
        rationale="test",
    )


@pytest.mark.asyncio
async def test_replay_backend_runs_repetitions_with_bounded_concurrency(tmp_path) -> None:
    active = 0
    max_active = 0
    artifact_dirs: list[str] = []

    async def executor(request):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        artifact_dirs.append(request.artifact_dir)
        await asyncio.sleep(0.01)
        active -= 1
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": request.variant_id}}],
        )

    request = CandidateReplayRequest(
        run_id="run-replay-concurrency",
        task_id="case-1",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate_id="cand-1",
        overlay_skill_root=str(tmp_path / "overlay"),
        task_input={"content": "replay task"},
        baseline_repetitions=2,
        candidate_repetitions=2,
    )
    backend = AWorldCliCandidateReplayBackend(
        executor=executor,
        replay_concurrency=2,
    )

    result = await backend.replay_candidate(
        request,
        candidate=_candidate(),
        dataset=_dataset(),
    )

    assert max_active == 2
    assert result.baseline.metrics["successful_repetition_count"] == 2
    assert result.candidate.metrics["successful_repetition_count"] == 2
    assert len(set(artifact_dirs)) == 4
    assert [item.variant_id for item in result.baseline.repetition_results] == [
        "baseline-1",
        "baseline-2",
    ]


def test_optimize_cli_exposes_runtime_bound_options() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    OptimizeTopLevelCommand().register_parser(subparsers)

    args = parser.parse_args(
        [
            "optimize",
            "--apply",
            "auto_verified",
            "--fast-verified",
            "--replay-concurrency",
            "3",
            "--judge-concurrency",
            "2",
            "--judge-failure-retries",
            "0",
            "--max-optimize-seconds",
            "45",
        ]
    )

    assert args.fast_verified is True
    assert args.replay_concurrency == 3
    assert args.judge_concurrency == 2
    assert args.judge_failure_retries == 0
    assert args.max_optimize_seconds == 45


def test_run_optimize_cli_propagates_runtime_bound_options(monkeypatch, tmp_path) -> None:
    captured = {}

    def fake_optimize_from_cli_request(**kwargs):
        captured.update(kwargs)
        return {"status": "rejected", "run_id": "run-test"}

    import aworld.self_evolve as self_evolve

    monkeypatch.setattr(
        self_evolve,
        "optimize_from_cli_request",
        fake_optimize_from_cli_request,
    )

    run_optimize_cli(
        agent="Aworld",
        task="task",
        target="skill:demo",
        dataset=None,
        from_session=None,
        from_trajectory="trajectory.log",
        from_run=None,
        rerun_evaluator=False,
        batch_config=None,
        iterations=None,
        apply="auto_verified",
        infer_target=False,
        workspace_root=str(tmp_path),
        judge_agent_name="judge",
        replay_concurrency=2,
        judge_concurrency=1,
        judge_failure_retries=0,
        max_optimize_seconds=90,
        fast_verified=True,
    )

    assert captured["replay_concurrency"] == 2
    assert captured["judge_concurrency"] == 1
    assert captured["judge_failure_retries"] == 0
    assert captured["max_optimize_seconds"] == 90
    assert captured["replay_candidate_limit"] == 1
    assert captured["candidate_replay_repetitions"] == 1
