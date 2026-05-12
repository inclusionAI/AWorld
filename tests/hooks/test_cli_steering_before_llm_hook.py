from types import SimpleNamespace

import pytest

from aworld.core.event.base import Message
from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig
from aworld.core.agent.swarm import Swarm
from aworld_cli.executors.hooks import ExecutorHookPoint
from aworld_cli.executors.local import LocalAgentExecutor
from aworld_cli.steering.coordinator import SteeringCoordinator


@pytest.mark.asyncio
async def test_before_llm_hook_appends_pending_steering_messages():
    from aworld_cli.executors.steering_before_llm_hook import SteeringBeforeLlmHook

    coordinator = SteeringCoordinator()
    coordinator.begin_task("sess-1", "task-1")
    coordinator.enqueue_text("sess-1", "Focus on failing tests first.")
    context = SimpleNamespace(session_id="sess-1", _aworld_cli_steering=coordinator)
    hook = SteeringBeforeLlmHook()
    message = Message(
        category="agent_hook",
        payload={
            "messages": [
                {"role": "user", "content": "Initial task"},
            ]
        },
        sender="llm_model",
        headers={},
    )

    result = await hook.exec(message, context=context)

    updated = result.headers["updated_input"]["messages"]
    assert updated[-1] == {"role": "user", "content": "Focus on failing tests first."}
    assert coordinator.snapshot("sess-1")["pending_count"] == 0


@pytest.mark.asyncio
async def test_before_llm_hook_skips_drain_when_payload_has_no_messages():
    from aworld_cli.executors.steering_before_llm_hook import SteeringBeforeLlmHook

    coordinator = SteeringCoordinator()
    coordinator.begin_task("sess-1", "task-1")
    coordinator.enqueue_text("sess-1", "Focus on failing tests first.")
    context = SimpleNamespace(session_id="sess-1", _aworld_cli_steering=coordinator)
    hook = SteeringBeforeLlmHook()
    message = Message(
        category="agent_hook",
        payload={"event": "before_llm_call"},
        sender="llm_model",
        headers={},
    )

    result = await hook.exec(message, context=context)

    assert "updated_input" not in result.headers
    assert coordinator.snapshot("sess-1")["pending_count"] == 1


class _DummyContext:
    def __init__(self, task_input):
        self.task_id = task_input.task_id
        self.user_id = task_input.user_id
        self.session_id = task_input.session_id
        self.workspace_path = None
        self._config = SimpleNamespace(debug_mode=False)

    def get_config(self):
        return self._config

    async def init_swarm_state(self, _swarm):
        return None


@pytest.mark.asyncio
async def test_local_executor_reattaches_steering_after_post_build_context_replacement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    captured = {}

    async def _fake_from_input(task_input, workspace=None, context_config=None):
        return _DummyContext(task_input)

    async def _fake_create_workspace(_session_id):
        return tmp_path / "workspace"

    async def _fake_execute_hooks(hook_point, **hook_kwargs):
        if hook_point == ExecutorHookPoint.POST_BUILD_CONTEXT:
            captured["hook_context_steering"] = getattr(
                hook_kwargs["context"], "_aworld_cli_steering", None
            )
            hook_kwargs["context"] = _DummyContext(hook_kwargs["task_input"])
        return None

    agent = Agent(name="developer", conf=AgentConfig(skill_configs={}))
    executor = LocalAgentExecutor(Swarm(agent))
    monkeypatch.setattr(
        "aworld_cli.executors.local.ApplicationContext.from_input",
        _fake_from_input,
    )
    monkeypatch.setattr(executor, "_create_workspace", _fake_create_workspace)
    monkeypatch.setattr(executor, "_execute_hooks", _fake_execute_hooks)

    coordinator = SteeringCoordinator()
    executor._base_runtime = SimpleNamespace(_steering=coordinator)

    task = await executor._build_task(
        "open docs in browser",
        session_id="session-1",
        task_id="task-1",
    )

    assert captured["hook_context_steering"] is coordinator
    assert task.context._aworld_cli_steering is coordinator
