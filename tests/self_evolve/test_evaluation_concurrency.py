from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from aworld.runner import Runners
from aworld.runners.batch import DeterministicTaskBatchExecutor
from aworld.self_evolve.datasets import EvalCase, SelfEvolveDataset
from aworld.self_evolve.evaluation import (
    _run_evaluator_cli_subprocess,
    evaluate_baseline_and_candidate,
)
from aworld.self_evolve.types import (
    CandidateVariant,
    DatasetRecipe,
    EvaluationSummary,
    SelfEvolveTargetRef,
)


def _dataset() -> SelfEvolveDataset:
    return SelfEvolveDataset(
        cases=(EvalCase(case_id="case-1", input="task"),),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 1},
            split_seed="seed",
            splits={"train": ["case-1"], "validation": [], "held_out": []},
            trainable_case_ids=("case-1",),
            held_out_case_ids=(),
        ),
    )


def _candidate() -> CandidateVariant:
    return CandidateVariant(
        candidate_id="candidate",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        content="# Candidate",
        rationale="test",
    )


@pytest.mark.asyncio
async def test_task_local_baseline_and_candidate_judges_overlap_and_reduce_stably() -> None:
    active = 0
    max_active = 0
    runner_classes: list[str | None] = []
    original_run_task = Runners.run_task

    class Backend:
        task_local_runtime = True

        async def evaluate_variant(self, request):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.02 if request.candidate is None else 0.001)
            active -= 1
            return EvaluationSummary(
                variant_id=request.variant_id,
                dataset_split=request.dataset_split,
                metrics={"score": 0.5 if request.candidate is None else 0.9},
            )

    async def recording_run_task(task, run_conf=None):
        runner_classes.append(task.runner_cls)
        return await original_run_task(task, run_conf=run_conf)

    baseline, candidate = await evaluate_baseline_and_candidate(
        Backend(),
        dataset=_dataset(),
        candidate=_candidate(),
        task_batch_executor=DeterministicTaskBatchExecutor(
            run_task=recording_run_task
        ),
        max_concurrency=2,
    )

    assert max_active == 2
    assert (baseline.variant_id, candidate.variant_id) == ("baseline", "candidate")
    assert all(
        item == "aworld.self_evolve.runtime.SelfEvolveEvaluationTaskRunner"
        for item in runner_classes
    )


@pytest.mark.asyncio
async def test_unsafe_in_process_backend_is_serialized_by_resource_claim() -> None:
    active = 0
    max_active = 0

    class Backend:
        async def evaluate_variant(self, request):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            return EvaluationSummary(
                variant_id=request.variant_id,
                dataset_split=request.dataset_split,
                metrics={"score": 0.5},
            )

    await evaluate_baseline_and_candidate(
        Backend(),
        dataset=_dataset(),
        candidate=_candidate(),
        task_batch_executor=DeterministicTaskBatchExecutor(),
        max_concurrency=2,
    )

    assert max_active == 1


@pytest.mark.asyncio
async def test_cli_judge_subprocesses_receive_distinct_env_without_parent_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWORLD_LOG_PATH", "parent-log-path")
    captured_environments: list[dict[str, str]] = []

    def fake_run(command, **kwargs):
        captured_environments.append(dict(kwargs["env"]))
        output_index = command.index("--output") + 1
        Path(command[output_index]).write_text(
            json.dumps({"summary": {}, "gate": {"status": "fail"}}),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr("aworld.self_evolve.evaluation.subprocess.run", fake_run)
    kwargs_a = {
        "input": str(tmp_path / "a.log"),
        "kind": "trajectory",
        "judge_agent_name": "judge",
        "out_dir": str(tmp_path / "a-out"),
        "output": str(tmp_path / "a-report.json"),
        "judge_timeout_seconds": 10,
    }
    kwargs_b = {
        **kwargs_a,
        "input": str(tmp_path / "b.log"),
        "out_dir": str(tmp_path / "b-out"),
        "output": str(tmp_path / "b-report.json"),
    }

    reports = await asyncio.gather(
        asyncio.to_thread(
            _run_evaluator_cli_subprocess,
            runner_kwargs=kwargs_a,
            log_path=tmp_path / "a-logs",
            workspace_root=tmp_path,
        ),
        asyncio.to_thread(
            _run_evaluator_cli_subprocess,
            runner_kwargs=kwargs_b,
            log_path=tmp_path / "b-logs",
            workspace_root=tmp_path,
        ),
    )

    assert len(reports) == 2
    assert {item["AWORLD_LOG_PATH"] for item in captured_environments} == {
        str(tmp_path / "a-logs"),
        str(tmp_path / "b-logs"),
    }
    assert all(
        item["AWORLD_TRAJECTORY_LOG_DISABLED"] == "1"
        for item in captured_environments
    )
    assert os.environ["AWORLD_LOG_PATH"] == "parent-log-path"
