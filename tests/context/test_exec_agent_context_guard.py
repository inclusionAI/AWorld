from __future__ import annotations

import types

import pytest

from aworld.agents import parallel_llm_agent
from aworld.agents.parallel_llm_agent import ParallelizableAgent
from aworld.core.common import Observation
from aworld.utils.run_util import exec_agent


class _FakeAgent:
    """Stand-in for a sub-agent: the guard must run before any agent attribute is read."""

    def id(self) -> str:
        return "fake-sub-agent"


class _FakeResponse:
    success = True
    answer = "ok"


def _make_parallelizable(agents: list) -> ParallelizableAgent:
    """Build the agent without running ``Agent.__init__``: only the guard path is under test."""
    agent = object.__new__(ParallelizableAgent)
    agent.agents = agents
    agent.aggregate_func = None
    agent._id = "parallelizable-test"
    return agent


@pytest.mark.asyncio
async def test_exec_agent_rejects_none_context_with_descriptive_error() -> None:
    with pytest.raises(ValueError) as excinfo:
        await exec_agent("hello", agent=_FakeAgent(), context=None)

    assert "context" in str(excinfo.value).lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [None, types.SimpleNamespace(context=None)],
    ids=["message-is-none", "message-context-is-none"],
)
async def test_parallelizable_async_policy_skips_spawn_without_context(monkeypatch, message) -> None:
    spawned: list[dict] = []

    async def _record_spawn(*args, **kwargs):
        spawned.append(kwargs)
        return _FakeResponse()

    monkeypatch.setattr(parallel_llm_agent, "exec_agent", _record_spawn)
    agent = _make_parallelizable([_FakeAgent()])

    result = await ParallelizableAgent.async_policy(
        agent, observation=Observation(content="hello"), message=message
    )

    assert result == []
    assert spawned == []


@pytest.mark.asyncio
async def test_parallelizable_async_policy_spawns_with_context(monkeypatch) -> None:
    spawned: list[dict] = []
    context = object()

    async def _record_spawn(*args, **kwargs):
        spawned.append(kwargs)
        return _FakeResponse()

    monkeypatch.setattr(parallel_llm_agent, "exec_agent", _record_spawn)
    agent = _make_parallelizable([_FakeAgent()])

    await ParallelizableAgent.async_policy(
        agent,
        observation=Observation(content="hello"),
        message=types.SimpleNamespace(context=context),
    )

    assert len(spawned) == 1
    assert spawned[0]["context"] is context
