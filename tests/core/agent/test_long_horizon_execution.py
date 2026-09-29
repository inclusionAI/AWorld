from __future__ import annotations

import asyncio

import pytest

from aworld.agents.llm_agent import Agent, _LongHorizonReviewContinuation
from aworld.config.conf import AgentConfig
from aworld.core.common import ActionModel, Observation
from aworld.core.context.base import Context
from aworld.core.context.execution_state import get_execution_state
from aworld.core.event.base import Constants, Message
from aworld.core.execution_protocol import ExecutionProtocolPolicy, ProtocolMode
from aworld.core.task import Task
from aworld.models.model_response import Function, ModelResponse, ToolCall
from aworld.runners.execution_protocol import (
    configure_execution_protocol,
    record_candidate_final,
    record_tool_protocol_event,
)


def _agent(context: Context, policy: ExecutionProtocolPolicy) -> Agent:
    agent = Agent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="offline",
            llm_api_key="offline",
        ),
        execution_protocol_policy=policy,
        max_loop_steps=0,
    )
    agent._llm = object()
    agent.context = context
    return agent


@pytest.mark.asyncio
async def test_review_model_error_returns_original_candidate_as_successful_execution() -> None:
    context = Context(task_id="review-error")
    context.set_task(Task(id="review-error", input="finish the task"))
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE, activation_event_threshold=1
    )
    agent = _agent(context, policy)
    configure_execution_protocol(context, agent.id(), policy)
    record_tool_protocol_event(
        context,
        agent.id(),
        {
            "goal_progress_observable": False,
            "goal_progress": False,
            "validation_evidence_advanced": True,
        },
    )
    record_candidate_final(context, agent.id())
    fallback = ActionModel(agent_name=agent.id(), policy_info="best current result")
    calls = 0

    async def attempt(observation, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return _LongHorizonReviewContinuation(
                observation=Observation(content="review current evidence"),
                kwargs={},
                fallback_actions=(fallback,),
            )
        raise RuntimeError("review provider unavailable")

    agent._async_policy_once = attempt
    message = Message(category=Constants.AGENT, headers={"context": context})

    result = await agent.async_policy(
        Observation(content="candidate"), message=message
    )

    assert result == [fallback]
    assert agent.finished is True
    assert get_execution_state(context)["status"] == "succeeded"
    assert get_execution_state(context)["reason"] == "long_horizon_review_fail_open"


@pytest.mark.asyncio
async def test_review_timeout_returns_original_candidate() -> None:
    context = Context(task_id="review-timeout")
    context.set_task(Task(id="review-timeout", input="finish the task", timeout=60))
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        activation_event_threshold=1,
        final_review_timeout_seconds=0.01,
    )
    agent = _agent(context, policy)
    fallback = ActionModel(agent_name=agent.id(), policy_info="safe candidate")
    calls = 0

    async def attempt(observation, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return _LongHorizonReviewContinuation(
                observation=Observation(content="review current evidence"),
                kwargs={},
                fallback_actions=(fallback,),
            )
        await asyncio.sleep(1)
        return [ActionModel(agent_name=agent.id(), policy_info="too late")]

    agent._async_policy_once = attempt
    message = Message(category=Constants.AGENT, headers={"context": context})

    result = await agent.async_policy(Observation(content="candidate"), message=message)

    assert result == [fallback]
    assert calls == 2
    assert get_execution_state(context)["reason"] == "long_horizon_review_fail_open"


def test_agent_rejects_untyped_execution_protocol_policy() -> None:
    context = Context(task_id="bad-policy")

    with pytest.raises(TypeError, match="ExecutionProtocolPolicy"):
        Agent(
            name="Aworld",
            conf=AgentConfig(
                llm_provider="openai",
                llm_model_name="offline",
                llm_api_key="offline",
            ),
            execution_protocol_policy={"mode": "guide"},
            context=context,
        )


def test_agent_uses_existing_skill_activation_as_protocol_switch() -> None:
    agent = Agent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="offline",
            llm_api_key="offline",
        ),
    )
    agent.skill_configs = {"long-running-agent": {"active": True}}

    assert agent._resolve_execution_protocol_policy().mode is ProtocolMode.GUIDE
    agent.skill_configs["long-running-agent"]["active"] = False
    assert agent._resolve_execution_protocol_policy().mode is ProtocolMode.OFF


@pytest.mark.parametrize(
    ("total,external,expected"),
    [
        (30.0, 3.0, 7.05),
        (60.0, 6.0, 14.1),
        (360.0, 36.0, 81.0),
        (3753.0, 60.0, 105.0),
    ],
)
def test_default_protocol_derives_three_part_finalization_budget(
    total: float,
    external: float,
    expected: float,
) -> None:
    context = Context(task_id="adaptive-reserve")
    context.set_task(Task(
        id="adaptive-reserve",
        input="complete the task",
        timeout=total,
        completion_reserve_seconds=external,
    ))
    agent = Agent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="offline",
            llm_api_key="offline",
        ),
    )
    agent.skill_configs = {"long-running-agent": {"active": True}}

    policy = agent._resolve_execution_protocol_policy(context)

    assert policy.mode is ProtocolMode.GUIDE
    assert policy.finalization_reserve_seconds == pytest.approx(expected)
    assert policy.finalization_reserve_seconds > external
    assert policy.finalization_reserve_seconds < total


def test_explicit_protocol_policy_is_not_rewritten_by_task_budget() -> None:
    context = Context(task_id="explicit-reserve")
    context.set_task(Task(
        id="explicit-reserve",
        timeout=30,
        completion_reserve_seconds=3,
    ))
    explicit = ExecutionProtocolPolicy(
        mode=ProtocolMode.OBSERVE,
        finalization_reserve_seconds=12,
    )
    agent = _agent(context, explicit)

    assert agent._resolve_execution_protocol_policy(context) is explicit


def test_explicit_execution_protocol_policy_survives_agent_round_trip() -> None:
    context = Context(task_id="round-trip")
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.OBSERVE,
        activation_event_threshold=7,
        final_review_timeout_seconds=12,
    )
    agent = _agent(context, policy)

    restored = Agent.from_dict(agent.to_dict())

    assert restored._resolve_execution_protocol_policy() == policy


@pytest.mark.asyncio
async def test_final_review_guidance_reaches_the_second_model_request() -> None:
    captured_messages = []

    class CapturingAgent(Agent):
        async def _add_message_to_memory(self, *args, **kwargs):
            return None

        async def build_llm_input(self, observation, info=None, message=None, **kwargs):
            return [
                {"role": "system", "content": "rules"},
                {"role": "user", "content": str(observation.content or "")},
            ]

        async def _filter_tools(self, context=None):
            return None

        async def invoke_model(self, messages=None, message=None, **kwargs):
            captured_messages.append(messages)
            ordinal = len(captured_messages)
            content = "candidate" if ordinal == 1 else "reviewed final"
            return ModelResponse(
                id=f"response-{ordinal}",
                model="offline",
                content=content,
                message={"role": "assistant", "content": content},
                finish_reason="stop",
                usage={"prompt_tokens": 1, "completion_tokens": 1},
            )

    context = Context(task_id="review-guidance")
    context.set_task(Task(id="review-guidance", input="finish the task"))
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        activation_event_threshold=1,
    )
    agent = CapturingAgent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="offline",
            llm_api_key="offline",
        ),
        execution_protocol_policy=policy,
        max_loop_steps=0,
    )
    configure_execution_protocol(context, agent.id(), policy)
    record_tool_protocol_event(
        context,
        agent.id(),
        {
            "goal_progress_observable": False,
            "goal_progress": False,
            "validation_evidence_advanced": True,
        },
    )
    message = Message(category=Constants.AGENT, headers={"context": context})

    result = await agent.async_policy(
        Observation(content="initial request"), message=message
    )

    assert result[0].policy_info == "reviewed final"
    assert len(captured_messages) == 2
    assert "long-horizon final review" in captured_messages[1][-1]["content"]


@pytest.mark.asyncio
async def test_final_review_rejects_multi_tool_repair_and_keeps_candidate() -> None:
    calls = 0

    class MultiToolReviewAgent(Agent):
        async def _add_message_to_memory(self, *args, **kwargs):
            return None

        async def build_llm_input(self, observation, info=None, message=None, **kwargs):
            return [{"role": "user", "content": str(observation.content or "")}]

        async def _filter_tools(self, context=None):
            return None

        async def invoke_model(self, messages=None, message=None, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return ModelResponse(
                    id="candidate",
                    model="offline",
                    content="best candidate",
                    message={"role": "assistant", "content": "best candidate"},
                    finish_reason="stop",
                    usage={"prompt_tokens": 1, "completion_tokens": 1},
                )
            tool_calls = [
                ToolCall(
                    id=f"call-{index}",
                    function=Function(name="run_code", arguments='{"code":"true"}'),
                )
                for index in (1, 2)
            ]
            return ModelResponse(
                id="over-broad-repair",
                model="offline",
                content="",
                message={"role": "assistant", "content": "", "tool_calls": tool_calls},
                tool_calls=tool_calls,
                finish_reason="tool_calls",
                usage={"prompt_tokens": 1, "completion_tokens": 1},
            )

    context = Context(task_id="multi-tool-review")
    context.set_task(Task(id="multi-tool-review", input="finish the task"))
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        activation_event_threshold=1,
    )
    agent = MultiToolReviewAgent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="offline",
            llm_api_key="offline",
        ),
        execution_protocol_policy=policy,
        max_loop_steps=0,
    )
    configure_execution_protocol(context, agent.id(), policy)
    record_tool_protocol_event(
        context,
        agent.id(),
        {"validation_evidence_advanced": True},
    )
    message = Message(category=Constants.AGENT, headers={"context": context})

    result = await agent.async_policy(Observation(content="start"), message=message)

    assert calls == 2
    assert len(result) == 1
    assert result[0].policy_info == "best candidate"
    assert agent.finished is True


@pytest.mark.asyncio
async def test_single_review_repair_forces_next_turn_to_finalize_without_tools() -> None:
    calls = 0
    prepared_tools = []

    class SingleRepairAgent(Agent):
        async def _add_message_to_memory(self, *args, **kwargs):
            return None

        async def build_llm_input(self, observation, info=None, message=None, **kwargs):
            return [{"role": "user", "content": str(observation.content or "")}]

        async def _filter_tools(self, context=None):
            return [
                {
                    "type": "function",
                    "function": {
                        "name": "run_code",
                        "description": "run one command",
                        "parameters": {"type": "object"},
                    },
                }
            ]

        async def invoke_model(self, messages=None, message=None, **kwargs):
            nonlocal calls
            calls += 1
            prepared_tools.append(kwargs.get("prepared_tools"))
            if calls == 1:
                content = "candidate before repair"
                return ModelResponse(
                    id="candidate",
                    model="offline",
                    content=content,
                    message={"role": "assistant", "content": content},
                    finish_reason="stop",
                    usage={"prompt_tokens": 1, "completion_tokens": 1},
                )
            if calls == 2:
                tool_calls = [
                    ToolCall(
                        id="repair-call",
                        function=Function(
                            name="run_code", arguments='{"code":"true"}'
                        ),
                    )
                ]
                return ModelResponse(
                    id="repair",
                    model="offline",
                    content="",
                    message={
                        "role": "assistant",
                        "content": "",
                        "tool_calls": tool_calls,
                    },
                    tool_calls=tool_calls,
                    finish_reason="tool_calls",
                    usage={"prompt_tokens": 1, "completion_tokens": 1},
                )
            content = "final after bounded repair"
            return ModelResponse(
                id="final",
                model="offline",
                content=content,
                message={"role": "assistant", "content": content},
                finish_reason="stop",
                usage={"prompt_tokens": 1, "completion_tokens": 1},
            )

    context = Context(task_id="single-repair")
    context.set_task(Task(id="single-repair", input="finish the task"))
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        activation_event_threshold=1,
    )
    agent = SingleRepairAgent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="offline",
            llm_api_key="offline",
        ),
        execution_protocol_policy=policy,
        max_loop_steps=0,
    )
    configure_execution_protocol(context, agent.id(), policy)
    record_tool_protocol_event(
        context,
        agent.id(),
        {"validation_evidence_advanced": True},
    )
    message = Message(category=Constants.AGENT, headers={"context": context})

    repair = await agent.async_policy(Observation(content="start"), message=message)
    final = await agent.async_policy(
        Observation(content="repair succeeded"), message=message
    )

    assert len(repair) == 1
    assert repair[0].tool_name == "run_code"
    assert final[0].policy_info == "final after bounded repair"
    assert calls == 3
    assert prepared_tools[-1] is None


@pytest.mark.asyncio
async def test_tool_only_repair_finalization_preserves_candidate_fallback() -> None:
    calls = 0

    class ToolOnlyFinalizationAgent(Agent):
        async def _add_message_to_memory(self, *args, **kwargs):
            return None

        async def build_llm_input(self, observation, info=None, message=None, **kwargs):
            return [{"role": "user", "content": str(observation.content or "")}]

        async def _filter_tools(self, context=None):
            return [
                {
                    "type": "function",
                    "function": {
                        "name": "run_code",
                        "description": "run one command",
                        "parameters": {"type": "object"},
                    },
                }
            ]

        async def invoke_model(self, messages=None, message=None, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return ModelResponse(
                    id="candidate",
                    model="offline",
                    content="safe original candidate",
                    message={
                        "role": "assistant",
                        "content": "safe original candidate",
                    },
                    finish_reason="stop",
                    usage={"prompt_tokens": 1, "completion_tokens": 1},
                )
            tool_calls = [
                ToolCall(
                    id=f"repair-{calls}",
                    function=Function(
                        name="run_code", arguments='{"code":"true"}'
                    ),
                )
            ]
            return ModelResponse(
                id=f"tool-only-{calls}",
                model="offline",
                content="",
                message={
                    "role": "assistant",
                    "content": "",
                    "tool_calls": tool_calls,
                },
                tool_calls=tool_calls,
                finish_reason="tool_calls",
                usage={"prompt_tokens": 1, "completion_tokens": 1},
            )

    context = Context(task_id="tool-only-finalization")
    context.set_task(Task(id="tool-only-finalization", input="finish the task"))
    policy = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        activation_event_threshold=1,
    )
    agent = ToolOnlyFinalizationAgent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="offline",
            llm_api_key="offline",
        ),
        execution_protocol_policy=policy,
        max_loop_steps=0,
    )
    configure_execution_protocol(context, agent.id(), policy)
    record_tool_protocol_event(
        context,
        agent.id(),
        {"validation_evidence_advanced": True},
    )
    message = Message(category=Constants.AGENT, headers={"context": context})

    repair = await agent.async_policy(Observation(content="start"), message=message)
    final = await agent.async_policy(
        Observation(content="repair completed"), message=message
    )

    assert repair[0].tool_name == "run_code"
    assert final[0].policy_info == "safe original candidate"
    assert calls == 3
