from __future__ import annotations

from types import SimpleNamespace

import pytest

from aworld.core.context.amni.local import LocalIsolatedApplicationContext
from aworld.core.task import Task
from aworld.config import RunConfig
from aworld.runner import Runners
from aworld.self_evolve.runtime import (
    SelfEvolveTaskRequest,
    SelfEvolveTaskRunner,
    build_self_evolve_task,
)


class _RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def run_explicit_target(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(marker="completed")


def test_offline_and_main_agent_subtask_use_the_same_outer_runner() -> None:
    request = SelfEvolveTaskRequest(
        runner=_RecordingRunner(),
        run_kwargs={"run_id": "run-1"},
    )
    offline = build_self_evolve_task(request, task_id="offline")
    parent_context = LocalIsolatedApplicationContext.create(
        task_id="parent",
        task_content="delegate optimize",
    )
    parent = Task(id="parent", input="delegate optimize", context=parent_context)
    subtask = build_self_evolve_task(
        request,
        task_id="subtask",
        parent_task=parent,
    )

    assert offline.runner_cls == "aworld.self_evolve.runtime.SelfEvolveTaskRunner"
    assert offline.is_sub_task is False
    assert subtask.runner_cls == offline.runner_cls
    assert subtask.is_sub_task is True
    assert subtask.parent_task is parent
    assert subtask.context is parent_context


@pytest.mark.asyncio
async def test_outer_task_runner_executes_deterministic_runner_request() -> None:
    runner = _RecordingRunner()
    request = SelfEvolveTaskRequest(
        runner=runner,
        run_kwargs={"run_id": "run-outer", "apply_policy": "proposal"},
    )
    task = build_self_evolve_task(request, task_id="outer")

    responses = await Runners.run_task(task)

    response = responses[task.id]
    assert response.success is True
    assert response.answer.marker == "completed"
    assert runner.calls == [
        {"run_id": "run-outer", "apply_policy": "proposal"}
    ]


def test_outer_request_copies_run_kwargs() -> None:
    source = {"run_id": "run-immutable"}
    request = SelfEvolveTaskRequest(
        runner=_RecordingRunner(),
        run_kwargs=source,
    )

    source["run_id"] = "mutated"

    assert request.run_kwargs == {"run_id": "run-immutable"}


def test_outer_request_rejects_non_local_runtime() -> None:
    task = build_self_evolve_task(
        SelfEvolveTaskRequest(
            runner=_RecordingRunner(),
            run_kwargs={"run_id": "run-local-only"},
        ),
        task_id="outer",
    )

    with pytest.raises(ValueError, match="local-only"):
        SelfEvolveTaskRunner(task, run_conf=RunConfig(engine_name="ray"))


def test_optimize_cli_submits_the_outer_aworld_task(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    from aworld.self_evolve.runner import optimize_from_cli_request

    skill_path = tmp_path / "aworld-skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n", encoding="utf-8")
    dataset_path = tmp_path / "eval.jsonl"
    dataset_path.write_text(
        '{"case_id":"case-1","input":"demo"}\n',
        encoding="utf-8",
    )
    captured: dict[str, Task] = {}
    original_sync_run_task = Runners.sync_run_task

    def recording_sync_run_task(task, run_conf=None):
        captured["task"] = task
        return original_sync_run_task(task, run_conf=run_conf)

    monkeypatch.setattr(Runners, "sync_run_task", recording_sync_run_task)

    optimize_from_cli_request(
        workspace_root=tmp_path,
        target="skill:demo",
        dataset=str(dataset_path),
        apply_policy="proposal",
    )

    outer_task = captured["task"]
    assert outer_task.runner_cls == "aworld.self_evolve.runtime.SelfEvolveTaskRunner"
    assert isinstance(outer_task.input, SelfEvolveTaskRequest)
