import json
from types import SimpleNamespace

import pytest

from aworld.agents.llm_agent import LLMAgent
from aworld.config.conf import AgentConfig, ContextCompilerRuntimeConfig
from aworld.core.context.base import Context
from aworld.core.context.compiler import estimate_canonical_json_tokens
from aworld_cli.core.runtime_completion import configure_runtime_completion
from tests.core.agent.test_agent_loop_budget import LoopBudgetAgent, _agent_message


@pytest.mark.asyncio
async def test_default_completion_never_runs_injected_delivery_checks(monkeypatch, tmp_path):
    monkeypatch.delenv("AWORLD_COMPLETION_MODE", raising=False)
    monkeypatch.setenv("AWORLD_REQUIRED_ARTIFACTS_JSON", '["missing.json"]')
    monkeypatch.setenv("AWORLD_VALIDATION_COMMANDS_JSON", json.dumps([
        {"command_id": "check", "argv": ["this-command-must-not-run"]}
    ]))
    context = Context(task_id="model-completion")
    assert configure_runtime_completion(
        context, request="Write missing.json", workspace_path=tmp_path,
    ) is None
    await context.resolve_completion_evidence()
    assert context.completion_contract is None
    assert context.assess_completion_contract(agent_claimed_finished=True) is None


@pytest.mark.asyncio
async def test_default_agent_does_not_stop_at_a_step_count():
    agent = LoopBudgetAgent(
        name="model-decides", conf=AgentConfig(llm_provider="mock", llm_model_name="mock"),
    )
    agent.loop_step = 10_000
    context = Context(task_id="unbounded-steps")
    assert not await agent.should_terminate_loop(_agent_message(agent, context, None))
    assert agent._elastic_step_budget_policy is None


@pytest.mark.asyncio
async def test_repetition_below_context_capacity_does_not_change_model_input(monkeypatch):
    agent = LLMAgent.__new__(LLMAgent)
    agent._id = "agent"
    agent._llm = SimpleNamespace(
        _context_checkpoint_policy=ContextCompilerRuntimeConfig().checkpoint_policy,
        _context_input_budget=100_000,
    )
    context = Context(task_id="repeated-inspection")
    context.context_info["context_semantic_progress"] = {
        "agent": {"repetition_count": 50, "low_information_gain_count": 50,
                  "no_goal_progress_count": 50},
    }
    async def unexpected_snapshot():
        pytest.fail("progress counters must not trigger compaction or recovery")
    monkeypatch.setattr(context, "snapshot", unexpected_snapshot)
    messages = [{"role": "user", "content": "Parse the input"}] + [
        {"role": "assistant", "content": f"inspection {index}"} for index in range(30)
    ]
    result = await agent._apply_adaptive_context_policy(
        context=context, messages=messages, context_compiler_mode="enforce",
    )
    assert result == messages


@pytest.mark.asyncio
async def test_capacity_compaction_allows_later_history_to_grow(monkeypatch):
    agent = LLMAgent.__new__(LLMAgent)
    agent._id = "agent"
    messages = [{"role": "user", "content": "Complete the task"}] + [
        {"role": "assistant", "content": f"fact {index}: " + "evidence " * 40}
        for index in range(40)
    ]
    agent._llm = SimpleNamespace(
        _context_checkpoint_policy="budget_pressure",
        _context_input_budget=int(estimate_canonical_json_tokens(messages).value),
    )
    context = Context(task_id="capacity-continuation")
    async def snapshot():
        context.advance_context_lifecycle("checkpoint")
        return SimpleNamespace(id="capacity-checkpoint")
    monkeypatch.setattr(context, "snapshot", snapshot)
    compacted = await agent._apply_adaptive_context_policy(
        context=context, messages=messages, context_compiler_mode="enforce",
    )
    assert len(compacted) < len(messages)
    assert len(compacted) > 8
    assert not any("recovery mode" in item.get("content", "") for item in compacted)
    new_messages = [{"role": "assistant", "content": f"new fact {i}"} for i in range(10)]
    result = await agent._apply_adaptive_context_policy(
        context=context, messages=messages + new_messages, context_compiler_mode="enforce",
    )
    assert result == compacted + new_messages
