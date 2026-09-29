from types import SimpleNamespace

import pytest

import aworld.runner as runner_module
from aworld.runner import Runners
from aworld.config import RunConfig


@pytest.mark.asyncio
async def test_run_propagates_timeout_to_task(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}
    response = object()
    agent = SimpleNamespace(task=None)

    class FakeSwarm:
        event_driven = True

        def __init__(self, root):
            self.root = root

    async def fake_run_task(task, run_conf=None):
        captured["task"] = task
        return {task.id: response}

    monkeypatch.setattr(runner_module, "Swarm", FakeSwarm)
    monkeypatch.setattr(Runners, "run_task", fake_run_task)

    result = await Runners.run("work", agent=agent, timeout=123.0)

    assert result is response
    assert captured["task"].timeout == 123.0
    assert captured["task"].remaining_seconds() <= 123.0


@pytest.mark.asyncio
async def test_run_keeps_legacy_positional_run_config(monkeypatch) -> None:
    captured = {}
    response = object()
    swarm = SimpleNamespace(event_driven=True)
    run_config = RunConfig()

    async def fake_run_task(task, run_conf=None):
        captured["task"] = task
        captured["run_conf"] = run_conf
        return {task.id: response}

    monkeypatch.setattr(Runners, "run_task", fake_run_task)

    result = await Runners.run(
        "work",
        None,
        swarm,
        [],
        None,
        run_config,
    )

    assert result is response
    assert captured["run_conf"] is run_config
    assert captured["task"].timeout is None
