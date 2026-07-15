from __future__ import annotations

from types import SimpleNamespace

import pytest

from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig, RunConfig
from aworld.core.common import StreamingMode
from aworld.core.context.amni.local import LocalIsolatedApplicationContext
from aworld.core.task import Task
from aworld.memory.main import MEMORY_HOLDER, MemoryFactory
from aworld.runners.event_runner import TaskEventRunner
from aworld.runners.utils import choose_runners
from aworld.self_evolve.runtime import (
    SelfEvolveCandidateTaskRunner,
    SelfEvolveEvaluationTaskRunner,
    SelfEvolveReplayTaskRunner,
    SelfEvolveTaskRunner,
)


def _candidate_task() -> Task:
    context = LocalIsolatedApplicationContext.create(
        task_id="candidate-task",
        task_content="generate",
    )
    return Task(
        id="candidate-task",
        input="generate",
        agent=Agent(name="candidate", conf=AgentConfig(), tool_names=[]),
        context=context,
        runner_cls="aworld.self_evolve.runtime.SelfEvolveCandidateTaskRunner",
    )


@pytest.mark.asyncio
async def test_choose_runners_constructs_all_self_evolve_adapters_with_run_conf() -> None:
    run_conf = RunConfig()
    tasks = [
        Task(
            id="outer",
            input="outer",
            runner_cls="aworld.self_evolve.runtime.SelfEvolveTaskRunner",
        ),
        _candidate_task(),
        Task(
            id="replay",
            input="replay",
            runner_cls="aworld.self_evolve.runtime.SelfEvolveReplayTaskRunner",
        ),
        Task(
            id="evaluation",
            input="evaluation",
            runner_cls="aworld.self_evolve.runtime.SelfEvolveEvaluationTaskRunner",
        ),
    ]

    runners = await choose_runners(tasks, run_conf=run_conf)

    assert [type(runner) for runner in runners] == [
        SelfEvolveTaskRunner,
        SelfEvolveCandidateTaskRunner,
        SelfEvolveReplayTaskRunner,
        SelfEvolveEvaluationTaskRunner,
    ]
    assert all(runner.run_conf is run_conf for runner in runners)
    assert runners[0].agent_oriented is False
    assert runners[1].agent_oriented is True
    assert runners[2].agent_oriented is False
    assert runners[3].agent_oriented is False


@pytest.mark.asyncio
async def test_candidate_runner_memory_scope_wraps_parent_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _candidate_task()
    runner = SelfEvolveCandidateTaskRunner(task)
    previous = MEMORY_HOLDER.get("instance")
    global_memory = object()
    MEMORY_HOLDER["instance"] = global_memory
    calls: list[str] = []

    async def fake_pre_run(self):
        calls.append("pre")
        assert MemoryFactory.instance() is task.context.local_memory

    async def fake_post_run(self):
        calls.append("post")
        assert MemoryFactory.instance() is task.context.local_memory

    monkeypatch.setattr(TaskEventRunner, "pre_run", fake_pre_run)
    monkeypatch.setattr(TaskEventRunner, "post_run", fake_post_run)
    try:
        await runner.pre_run()
        assert MemoryFactory.instance() is task.context.local_memory
        await runner.post_run()
        assert MemoryFactory.instance() is global_memory
    finally:
        if previous is None:
            MEMORY_HOLDER.pop("instance", None)
        else:
            MEMORY_HOLDER["instance"] = previous

    assert calls == ["pre", "post"]


def test_candidate_runner_rejects_context_without_owned_local_memory() -> None:
    task = Task(
        id="candidate-task",
        input="generate",
        agent=Agent(name="candidate", conf=AgentConfig(), tool_names=[]),
        context=SimpleNamespace(),
    )
    runner = SelfEvolveCandidateTaskRunner(task)

    with pytest.raises(ValueError, match="local memory"):
        runner._candidate_memory()


@pytest.mark.asyncio
async def test_non_agent_self_evolve_runner_accepts_streaming_task() -> None:
    task = Task(
        id="outer-streaming",
        input="outer",
        streaming_mode=StreamingMode.CORE,
        runner_cls="aworld.self_evolve.runtime.SelfEvolveTaskRunner",
    )

    runners = await choose_runners([task])

    assert isinstance(runners[0], SelfEvolveTaskRunner)
    assert runners[0].task.streaming_mode == StreamingMode.CORE
