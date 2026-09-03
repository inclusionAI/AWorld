from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from aworld.agents.llm_agent import LLMAgent
from aworld.core.common import ActionModel, ActionResult, Observation
from aworld.core.context.base import Context
from aworld.core.context.compiler import (
    AdaptiveCheckpointReason,
    ArtifactEvidence,
    ArtifactRequirement,
    CompletionContract,
    CompletionMode,
    SelfCheckEvidence,
    ValidationCommand,
    compact_message_history,
    evaluate_adaptive_checkpoint,
    semantic_fingerprint,
)
from aworld.runners.post_tool_progress import (
    acknowledge_semantic_checkpoint,
    record_semantic_tool_progress,
    semantic_progress_for_agent,
)


@pytest.mark.asyncio
async def test_completion_evidence_resolver_survives_context_deep_copy():
    calls = []

    async def resolver(context, contract):
        calls.append((context, contract))

    context = Context(task_id="completion-resolver-copy")
    contract = CompletionContract(
        required_artifacts=(),
        immutable_inputs=(),
        validation_commands=(),
        max_evidence_age_seconds=None,
        required_final_evidence=(),
    )
    context.configure_completion_contract(
        contract,
        mode=CompletionMode.ENFORCE,
        evidence_resolver=resolver,
    )

    cloned = context.deep_copy()
    await cloned.resolve_completion_evidence()

    assert calls == [(cloned, contract)]


def test_semantic_progress_ignores_transport_ids_and_timing():
    left = {
        "tool_call_id": "one",
        "metadata": {"execution_time": 1.2, "return_code": 0},
        "content": "same result",
    }
    right = {
        "tool_call_id": "two",
        "metadata": {"execution_time": 99.0, "return_code": 0},
        "content": "same result",
    }
    assert semantic_fingerprint(left) == semantic_fingerprint(right)


def test_semantic_progress_detects_repetition_and_low_information_gain():
    context = Context(task_id="semantic-progress")
    observation = Observation(
        content="unchanged",
        action_result=[ActionResult(content="unchanged", success=True)],
    )
    for call_id in ("one", "two", "three"):
        record_semantic_tool_progress(
            context,
            tool_name="terminal",
            agent_id="agent",
            actions=[
                ActionModel(
                    tool_name="terminal",
                    action_name="run_code",
                    tool_call_id=call_id,
                    params={"code": "cat status"},
                )
            ],
            observation=observation,
        )
    state = semantic_progress_for_agent(context, agent_id="agent")
    assert state["repetition_count"] == 3
    assert state["low_information_gain_count"] == 3
    assert state["no_goal_progress_count"] == 3
    acknowledge_semantic_checkpoint(context, agent_id="agent")
    state = semantic_progress_for_agent(context, agent_id="agent")
    assert state["repetition_count"] == 0
    assert state["low_information_gain_count"] == 0
    assert state["no_goal_progress_count"] == 0


def test_semantic_progress_resets_when_task_artifact_changes():
    context = Context(task_id="artifact-progress")
    unchanged = Observation(
        action_result=[
            ActionResult(
                content="same",
                success=True,
                metadata={
                    "context_management": {
                        "artifact_changed": False,
                        "artifact_fingerprint_after": "before",
                    }
                },
            )
        ]
    )
    changed = Observation(
        action_result=[
            ActionResult(
                content="same",
                success=True,
                metadata={
                    "context_management": {
                        "artifact_changed": True,
                        "artifact_fingerprint_after": "after",
                    }
                },
            )
        ]
    )
    action = ActionModel(
        tool_name="terminal", action_name="run_code", params={"code": "make"}
    )
    record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[action],
        observation=unchanged,
    )
    record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[action],
        observation=unchanged,
    )
    state = record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[action],
        observation=changed,
    )
    assert state["repetition_count"] == 1
    assert state["low_information_gain_count"] == 1
    assert state["artifact_fingerprint"] == "after"
    assert (
        context.context_info["post_tool_progress_metrics"]["task_artifact_change_count"]
        == 1
    )


def test_diverse_tool_results_without_goal_evidence_trigger_progress_window():
    context = Context(task_id="goal-progress")
    action = ActionModel(
        tool_name="terminal", action_name="run_code", params={"code": "inspect"}
    )
    for index in range(6):
        state = record_semantic_tool_progress(
            context,
            tool_name="terminal",
            agent_id="agent",
            actions=[action.model_copy(update={"tool_call_id": f"call-{index}"})],
            observation=Observation(
                action_result=[
                    ActionResult(content=f"novel result {index}", success=True)
                ]
            ),
        )

    assert state["low_information_gain_count"] == 1
    assert state["no_goal_progress_count"] == 6
    decision = evaluate_adaptive_checkpoint(
        policy_name="adaptive",
        prompt_tokens=10,
        input_budget=100,
        repetition_count=state["repetition_count"],
        low_information_gain_count=state["low_information_gain_count"],
        no_goal_progress_count=state["no_goal_progress_count"],
        turn_epoch=7,
        last_checkpoint_turn=None,
    )
    assert AdaptiveCheckpointReason.NO_GOAL_PROGRESS in decision.reasons


def test_completion_progress_requires_positive_goal_evidence():
    context = Context(task_id="completion-progress")
    context.configure_completion_contract(
        CompletionContract(
            required_artifacts=(
                ArtifactRequirement(requirement_id="output", path="/workspace/out"),
            ),
            immutable_inputs=(),
            validation_commands=(
                ValidationCommand(command_id="check", argv=("verify",)),
            ),
            max_evidence_age_seconds=None,
            required_final_evidence=("final",),
        ),
        mode=CompletionMode.ENFORCE,
    )
    observed_at = datetime.now(timezone.utc)
    context.record_completion_artifact(
        ArtifactEvidence(
            requirement_id="output",
            exists=False,
            content_hash=None,
            observed_at=observed_at,
        )
    )
    context.record_completion_self_check(
        SelfCheckEvidence(
            command_id="check",
            exit_code=1,
            output_hash=None,
            observed_at=observed_at,
        )
    )
    action = ActionModel(tool_name="terminal", action_name="run_code")
    failed = record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[action],
        observation=Observation(action_result=[ActionResult(success=False)]),
    )
    assert failed["completion_advanced"] is False
    assert failed["no_goal_progress_count"] == 1

    context.record_completion_artifact(
        ArtifactEvidence(
            requirement_id="output",
            exists=True,
            content_hash=None,
            observed_at=observed_at,
        )
    )
    context.record_completion_self_check(
        SelfCheckEvidence(
            command_id="check",
            exit_code=0,
            output_hash=None,
            observed_at=observed_at,
        )
    )
    context.record_completion_final_evidence("final")
    satisfied = record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[action],
        observation=Observation(action_result=[ActionResult(success=True)]),
    )
    assert satisfied["completion_advanced"] is True
    assert satisfied["completion_score"][0] == 3
    assert satisfied["no_goal_progress_count"] == 0


def test_adaptive_policy_has_cooldown_and_budget_pressure_modes():
    decision = evaluate_adaptive_checkpoint(
        policy_name="adaptive",
        prompt_tokens=10,
        input_budget=100,
        repetition_count=3,
        low_information_gain_count=3,
        turn_epoch=5,
        last_checkpoint_turn=None,
    )
    assert decision.checkpoint
    assert set(decision.reasons) == {
        AdaptiveCheckpointReason.REPEATED_OPERATION,
        AdaptiveCheckpointReason.LOW_INFORMATION_GAIN,
    }
    cooled_down = evaluate_adaptive_checkpoint(
        policy_name="adaptive",
        prompt_tokens=90,
        input_budget=100,
        repetition_count=4,
        low_information_gain_count=4,
        turn_epoch=6,
        last_checkpoint_turn=5,
    )
    assert not cooled_down.checkpoint
    pressure_only = evaluate_adaptive_checkpoint(
        policy_name="budget_pressure",
        prompt_tokens=80,
        input_budget=100,
        repetition_count=99,
        low_information_gain_count=99,
        turn_epoch=8,
        last_checkpoint_turn=None,
    )
    assert pressure_only.reasons == (AdaptiveCheckpointReason.BUDGET_PRESSURE,)


def test_compaction_retains_task_system_policy_and_recent_turns():
    messages = [
        {"role": "system", "content": "policy"},
        {"role": "user", "content": "original task"},
        *[
            {"role": "tool" if index % 2 else "assistant", "content": f"turn {index}"}
            for index in range(12)
        ],
    ]
    compacted, receipt = compact_message_history(messages, keep_recent=4)
    assert receipt is not None
    assert {message["content"] for message in compacted} >= {
        "policy",
        "original task",
        "turn 11",
    }
    assert receipt["removed_message_count"] == 8
    marker = next(
        message
        for message in compacted
        if "AWorld compacted earlier" in message["content"]
    )
    assert marker["role"] == "user"
    assert receipt["removed_messages_hash"] not in marker["content"]


def test_compaction_never_splits_assistant_tool_atomic_group():
    messages = [
        {"role": "system", "content": "policy"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "old"},
        {"role": "tool", "tool_call_id": "older", "content": "old result"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call-a", "type": "function", "function": {"name": "a"}},
                {"id": "call-b", "type": "function", "function": {"name": "b"}},
            ],
        },
        {"role": "tool", "tool_call_id": "call-a", "content": "a"},
        {"role": "tool", "tool_call_id": "call-b", "content": "b"},
    ]

    compacted, receipt = compact_message_history(messages, keep_recent=1)

    assert receipt is not None
    roles_and_ids = [
        (message["role"], message.get("tool_call_id")) for message in compacted
    ]
    assert ("assistant", None) in roles_and_ids
    assert ("tool", "call-a") in roles_and_ids
    assert ("tool", "call-b") in roles_and_ids
    marker_index = next(
        index
        for index, message in enumerate(compacted)
        if "AWorld compacted earlier" in message.get("content", "")
    )
    assistant_index = next(
        index for index, message in enumerate(compacted) if message.get("tool_calls")
    )
    assert marker_index < assistant_index


@pytest.mark.asyncio
async def test_agent_adaptive_policy_performs_checkpoint_and_compaction(monkeypatch):
    agent = LLMAgent.__new__(LLMAgent)
    agent._id = "agent"
    agent._llm = SimpleNamespace(
        _context_checkpoint_policy="adaptive",
        _context_input_budget=10_000,
    )
    context = Context(task_id="adaptive-runtime")
    context.advance_context_lifecycle("next_turn")
    checkpoint_calls = []

    async def snapshot():
        checkpoint_calls.append(True)
        context.advance_context_lifecycle("checkpoint")
        return SimpleNamespace(id="checkpoint-1")

    monkeypatch.setattr(context, "snapshot", snapshot)
    progress = {
        "agent": {
            "repetition_count": 3,
            "low_information_gain_count": 3,
        }
    }
    context.context_info["context_semantic_progress"] = progress
    messages = [
        {"role": "system", "content": "policy"},
        {"role": "user", "content": "task"},
        *[{"role": "tool", "content": f"old {index}"} for index in range(12)],
    ]
    compacted = await agent._apply_adaptive_context_policy(
        context=context,
        messages=messages,
        context_compiler_mode="enforce",
    )
    assert checkpoint_calls == [True]
    assert len(compacted) < len(messages)
    assert "insufficient semantic progress" in compacted[-1]["content"]
    assert compacted[-1]["role"] == "user"
    assert "repeated_operation" not in compacted[-1]["content"]
    state = context.context_info["adaptive_context_state:agent"]
    assert state["last_checkpoint_id"] == "checkpoint-1"


@pytest.mark.asyncio
async def test_agent_compacts_diverse_history_after_no_goal_progress(monkeypatch):
    agent = LLMAgent.__new__(LLMAgent)
    agent._id = "agent"
    agent._llm = SimpleNamespace(
        _context_checkpoint_policy="adaptive",
        _context_input_budget=100_000,
    )
    context = Context(task_id="goal-window-runtime")
    context.advance_context_lifecycle("next_turn")

    async def snapshot():
        context.advance_context_lifecycle("checkpoint")
        return SimpleNamespace(id="goal-window-checkpoint")

    monkeypatch.setattr(context, "snapshot", snapshot)
    context.context_info["context_semantic_progress"] = {
        "agent": {
            "repetition_count": 1,
            "low_information_gain_count": 1,
            "no_goal_progress_count": 6,
        }
    }
    messages = [
        {"role": "system", "content": "policy"},
        {"role": "user", "content": "task"},
        *[
            {
                "role": "assistant" if index % 2 == 0 else "tool",
                "content": f"unique {index}",
            }
            for index in range(20)
        ],
    ]

    compacted = await agent._apply_adaptive_context_policy(
        context=context,
        messages=messages,
        context_compiler_mode="enforce",
    )

    assert len(compacted) <= 10
    state = context.context_info["adaptive_context_state:agent"]
    assert state["last_reasons"] == ["no_goal_progress"]
    assert state["compaction_active"] is True
    assert state["last_effective_prompt_tokens"] < state["last_prompt_tokens"]
    assert state["last_estimated_saved_prompt_tokens"] > 0
    assert state["decisions"][-1]["estimated_saved_prompt_tokens"] > 0
