from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from aworld.agents.llm_agent import LLMAgent
from aworld.core.common import ActionModel, ActionResult, Observation
from aworld.core.context.amni import ApplicationContext
from aworld.core.context.base import Context
from aworld.core.context.compiler import (
    ADAPTIVE_WORK_STATE_KEY,
    ADAPTIVE_WORK_STATE_MAX_TOKENS,
    ADAPTIVE_WORK_STATE_PREFIX,
    AdaptiveCheckpointReason,
    AdaptiveEscalationStage,
    ArtifactEvidence,
    ArtifactRequirement,
    CompletionContract,
    CompletionMode,
    SelfCheckEvidence,
    ValidationCommand,
    advance_adaptive_escalation,
    advance_adaptive_work_state,
    attach_adaptive_work_state,
    compact_message_history,
    evaluate_adaptive_checkpoint,
    semantic_fingerprint,
    estimate_canonical_json_tokens,
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
    assert state["goal_progress_observable"] is False
    assert state["no_goal_progress_count"] == 0
    acknowledge_semantic_checkpoint(context, agent_id="agent")
    state = semantic_progress_for_agent(context, agent_id="agent")
    assert state["repetition_count"] == 0
    assert state["low_information_gain_count"] == 0
    assert state["no_goal_progress_count"] == 0


def test_semantic_progress_fans_in_across_context_transport_copies():
    root = Context(task_id="semantic-progress-fan-in")
    first = root.deep_copy()
    second = root.deep_copy()
    action = ActionModel(
        tool_name="terminal", action_name="run_code", params={"code": "status"}
    )
    observation = Observation(
        action_result=[ActionResult(content="unchanged", success=True)]
    )

    record_semantic_tool_progress(
        first,
        tool_name="terminal",
        agent_id="agent",
        actions=[action],
        observation=observation,
    )
    record_semantic_tool_progress(
        second,
        tool_name="terminal",
        agent_id="agent",
        actions=[action],
        observation=observation,
    )

    assert semantic_progress_for_agent(root, agent_id="agent")[
        "goal_progress_observable"
    ] is False
    assert (
        semantic_progress_for_agent(root, agent_id="agent")["no_goal_progress_count"]
        == 0
    )
    assert semantic_progress_for_agent(first, agent_id="agent")["repetition_count"] == 2


def test_semantic_progress_records_bounded_work_state_for_checkpoint_resume():
    context = Context(task_id="adaptive-work-state")
    action = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        tool_call_id="call-work",
        params={
            "code": "inspect --current-state",
            "api_key": "must-not-enter-context",
        },
    )
    record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[action],
        observation=Observation(
            action_result=[
                ActionResult(
                    tool_name="terminal",
                    action_name="run_code",
                    tool_call_id="call-work",
                    content="verified-current-state",
                    success=True,
                    metadata={
                        "context_management": {
                            "artifact_changed": True,
                            "artifact_fingerprint_after": "artifact-v2",
                        }
                    },
                )
            ]
        ),
    )

    state = context.read_task_runtime_state("agent", ADAPTIVE_WORK_STATE_KEY)
    projected = attach_adaptive_work_state(
        [
            {"role": "system", "content": "policy"},
            {"role": "user", "content": "task"},
        ],
        state,
    )

    assert state["revision"] == 1
    assert state["milestones"] == []
    assert state["recent_operations"][0]["artifact_fingerprint"] == "artifact-v2"
    continuation = projected[2]["content"]
    assert continuation.startswith(ADAPTIVE_WORK_STATE_PREFIX)
    assert "inspect --current-state" in continuation
    assert "verified-current-state" in continuation
    assert "must-not-enter-context" not in continuation
    assert "<redacted>" in continuation


def test_work_state_preserves_only_typed_retrievable_artifact_refs():
    context = Context(task_id="adaptive-artifact-registry")
    action = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        tool_call_id="call-artifact",
        params={"code": "produce-large-output"},
    )
    record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[action],
        observation=Observation(
            action_result=[
                ActionResult(
                    tool_name="terminal",
                    action_name="run_code",
                    tool_call_id="call-artifact",
                    content={
                        "content_sha256": "not-a-capability",
                        "artifact_ref": None,
                    },
                    success=True,
                    metadata={
                        "tool_output_policy": {
                            "upstream_artifacts": [
                                {
                                    "ref": "/artifacts/exact.bin",
                                    "content_hash": "sha256:" + "a" * 64,
                                    "byte_count": 8192,
                                    "owner_tool": "terminal",
                                    "retrieval_action": "read_output_artifact",
                                }
                            ]
                        }
                    },
                )
            ]
        ),
    )

    state = context.read_task_runtime_state("agent", ADAPTIVE_WORK_STATE_KEY)
    continuation = attach_adaptive_work_state([], state)[0]["content"]

    assert state["available_artifacts"] == [
        {
            "ref": "/artifacts/exact.bin",
            "content_hash": "sha256:" + "a" * 64,
            "byte_count": 8192,
            "tool": "terminal",
            "action": "read_output_artifact",
        }
    ]
    assert '"retrievable_artifacts"' in continuation
    assert "/artifacts/exact.bin" in continuation
    assert "never construct an artifact path from a checksum" in continuation


def test_work_state_projection_replaces_older_projection():
    first = {
        "revision": 1,
        "recent_operations": [{"sequence": 1, "actions": [], "results": []}],
    }
    second = {
        "revision": 2,
        "recent_operations": [{"sequence": 2, "actions": [], "results": []}],
    }
    messages = attach_adaptive_work_state([{"role": "user", "content": "task"}], first)
    replaced = attach_adaptive_work_state(messages, second)

    assert (
        sum(
            isinstance(item.get("content"), str)
            and item["content"].startswith(ADAPTIVE_WORK_STATE_PREFIX)
            for item in replaced
        )
        == 1
    )
    assert '"revision":2' in replaced[1]["content"]


def test_work_state_projection_has_deterministic_total_token_budget():
    artifact = {
        "ref": "/artifacts/latest.bin",
        "content_hash": "sha256:" + "a" * 64,
        "byte_count": 65_536,
        "tool": "terminal",
        "action": "read_output_artifact",
    }
    operations = []
    for sequence in range(1, 13):
        operations.append(
            {
                "sequence": sequence,
                "operation_hash": f"operation-{sequence}",
                "result_hash": f"result-{sequence}",
                "actions": [
                    {
                        "tool": "terminal",
                        "action": "run_code",
                        "arguments": {"code": "x" * 720},
                    }
                    for _ in range(12)
                ],
                "results": [
                    {
                        "tool": "terminal",
                        "action": "run_code",
                        "success": True,
                        "error": None,
                        "evidence": "result" * 150,
                    }
                    for _ in range(12)
                ],
                "artifact_changed": sequence == 12,
                "artifact_fingerprint": f"artifact-{sequence}",
                "rollback_performed": False,
                "implicit_artifact_loss": False,
                "goal_progress": sequence == 12,
                "available_artifacts": [artifact] if sequence == 12 else [],
            }
        )
    state = {
        "schema_version": "aworld.context.adaptive-work-state/v1",
        "revision": 12,
        "observation_count": 12,
        "artifact_fingerprint": "artifact-12",
        "recent_operations": operations[-8:],
        "milestones": operations[-4:],
        "attempted_operation_hashes": [f"operation-{value}" for value in range(24)],
        "available_artifacts": [artifact],
    }

    first = attach_adaptive_work_state([], state)[0]
    second = attach_adaptive_work_state([], state)[0]

    assert first == second
    assert estimate_canonical_json_tokens(first).value <= ADAPTIVE_WORK_STATE_MAX_TOKENS
    assert '"operation_hash":"operation-12"' in first["content"]
    assert '"result_hash":"result-12"' in first["content"]
    assert "/artifacts/latest.bin" in first["content"]
    assert '"projection"' in first["content"]


def test_work_state_omits_unusable_oversized_capability_ref():
    oversized_ref = "/artifacts/" + "x" * 40_000
    state = {
        "revision": 1,
        "observation_count": 1,
        "recent_operations": [
            {
                "sequence": 1,
                "operation_hash": "operation-latest",
                "result_hash": "result-latest",
                "actions": [],
                "results": [],
            }
        ],
        "available_artifacts": [
            {
                "ref": oversized_ref,
                "content_hash": "sha256:" + "b" * 64,
                "byte_count": 1,
                "tool": "terminal",
                "action": "read_output_artifact",
            }
        ],
    }

    message = attach_adaptive_work_state([], state)[0]

    assert (
        estimate_canonical_json_tokens(message).value <= ADAPTIVE_WORK_STATE_MAX_TOKENS
    )
    assert oversized_ref not in message["content"]
    assert "omitted_retrievable_artifact_hashes" in message["content"]
    assert '"operation_hash":"operation-latest"' in message["content"]


def test_adaptive_work_state_uses_amni_working_state_checkpoint_surface():
    context = ApplicationContext.create(
        session_id="adaptive-amni-session",
        task_id="adaptive-amni-task",
        task_content="generic task",
    )
    record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[
            ActionModel(
                tool_name="terminal",
                action_name="run_code",
                tool_call_id="call-amni",
                params={"code": "inspect"},
            )
        ],
        observation=Observation(
            action_result=[
                ActionResult(
                    tool_name="terminal",
                    action_name="run_code",
                    tool_call_id="call-amni",
                    content="observed",
                    success=True,
                )
            ]
        ),
    )

    stored = context.get(f"{ADAPTIVE_WORK_STATE_KEY}:agent")
    cloned = context.deep_copy()

    assert stored["revision"] == 1
    assert cloned.get(f"{ADAPTIVE_WORK_STATE_KEY}:agent") == stored


def test_semantic_progress_distinguishes_work_artifact_from_goal_progress():
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
    assert state["artifact_advanced"] is True
    assert state["goal_progress_observable"] is False
    assert state["goal_progress"] is False
    assert state["no_goal_progress_count"] == 0
    assert (
        context.context_info["post_tool_progress_metrics"]["task_artifact_change_count"]
        == 1
    )
    assert state["last_goal_progress_agent_step"] is None


def test_unverified_artifact_change_is_not_promoted_to_durable_milestone():
    state = advance_adaptive_work_state(
        {},
        {
            "operation_hash": "operation-1",
            "result_hash": "result-1",
            "actions": [],
            "results": [],
            "artifact_changed": True,
            "artifact_fingerprint": "artifact-1",
            "goal_progress": False,
            "rollback_performed": False,
            "implicit_artifact_loss": False,
            "available_artifacts": [],
        },
    )

    assert state["recent_operations"][0]["artifact_changed"] is True
    assert state["milestones"] == []

    state = advance_adaptive_work_state(
        state,
        {
            "operation_hash": "operation-2",
            "result_hash": "result-2",
            "actions": [],
            "results": [],
            "artifact_changed": False,
            "artifact_fingerprint": "artifact-1",
            "goal_progress": True,
            "rollback_performed": False,
            "implicit_artifact_loss": False,
            "available_artifacts": [],
        },
    )

    assert [item["operation_hash"] for item in state["milestones"]] == ["operation-2"]


def test_diverse_tool_results_with_goal_contract_trigger_progress_window():
    context = Context(task_id="goal-progress")
    context.configure_completion_contract(
        CompletionContract(
            required_artifacts=(),
            immutable_inputs=(),
            validation_commands=(),
            max_evidence_age_seconds=None,
            required_final_evidence=("done",),
        ),
        mode=CompletionMode.OBSERVE,
    )
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
    assert state["goal_progress_observable"] is True
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
    assert failed["goal_progress_observable"] is True
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


def test_adaptive_escalation_advances_across_checkpoints_and_resets_on_progress():
    first = advance_adaptive_escalation(
        previous_no_progress_checkpoints=0,
        checkpoint_reasons=(AdaptiveCheckpointReason.NO_GOAL_PROGRESS,),
        goal_progress=False,
    )
    second = advance_adaptive_escalation(
        previous_no_progress_checkpoints=first.no_progress_checkpoint_count,
        checkpoint_reasons=(AdaptiveCheckpointReason.REPEATED_OPERATION,),
        goal_progress=False,
    )
    third = advance_adaptive_escalation(
        previous_no_progress_checkpoints=second.no_progress_checkpoint_count,
        checkpoint_reasons=(AdaptiveCheckpointReason.LOW_INFORMATION_GAIN,),
        goal_progress=False,
    )
    saturated = advance_adaptive_escalation(
        previous_no_progress_checkpoints=9,
        checkpoint_reasons=(AdaptiveCheckpointReason.NO_GOAL_PROGRESS,),
        goal_progress=False,
    )
    reset = advance_adaptive_escalation(
        previous_no_progress_checkpoints=saturated.no_progress_checkpoint_count,
        checkpoint_reasons=(),
        goal_progress=True,
    )

    assert first.stage is AdaptiveEscalationStage.REASSESS
    assert second.stage is AdaptiveEscalationStage.DIVERSIFY
    assert third.stage is AdaptiveEscalationStage.RECOVER
    assert saturated.stage is AdaptiveEscalationStage.RECOVER
    assert saturated.no_progress_checkpoint_count == 10
    assert reset.stage is AdaptiveEscalationStage.NONE
    assert reset.no_progress_checkpoint_count == 0
    assert reset.progress_reset is True


def test_budget_pressure_does_not_manufacture_no_progress_escalation():
    decision = advance_adaptive_escalation(
        previous_no_progress_checkpoints=2,
        checkpoint_reasons=(AdaptiveCheckpointReason.BUDGET_PRESSURE,),
        goal_progress=False,
    )

    assert decision.stage is AdaptiveEscalationStage.DIVERSIFY
    assert decision.no_progress_checkpoint_count == 2
    assert decision.progress_reset is False


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
    assert receipt["latest_tool_atomic_group_retained"] is True
    assert receipt["latest_tool_atomic_group_size"] == 3
    assert isinstance(receipt["latest_tool_atomic_group_hash"], str)


def test_compaction_retains_latest_tool_group_behind_framework_sidecars():
    messages = [
        {"role": "system", "content": "policy"},
        {"role": "user", "content": "task"},
        *[{"role": "assistant", "content": f"old {index}"} for index in range(8)],
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "latest", "type": "function"}],
        },
        {"role": "tool", "tool_call_id": "latest", "content": "state"},
        {"role": "user", "content": "framework sidecar 1"},
        {"role": "user", "content": "framework sidecar 2"},
    ]

    compacted, receipt = compact_message_history(messages, keep_recent=1)

    assert receipt is not None
    assert any(message.get("tool_call_id") == "latest" for message in compacted)
    assert any(
        any(call.get("id") == "latest" for call in message.get("tool_calls", []))
        for message in compacted
    )
    assert receipt["latest_tool_atomic_group_retained"] is True


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

    snapshot_state = []

    async def snapshot():
        checkpoint_calls.append(True)
        snapshot_state.append(
            {
                "adaptive": dict(context.context_info["adaptive_context_state:agent"]),
                "continuation": list(
                    context.context_info["adaptive_continuation_capsule:agent"]
                ),
            }
        )
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
    assert state["checkpoint_snapshot_state"] == "captured"
    assert snapshot_state[0]["adaptive"]["checkpoint_snapshot_state"] == "prepared"
    assert snapshot_state[0]["adaptive"]["last_reasons"] == [
        "repeated_operation",
        "low_information_gain",
    ]
    assert snapshot_state[0]["continuation"] == compacted
    assert context.context_lifecycle_state.checkpoint_revision == 1

    # Once compaction is active, later turns append to the same cache epoch
    # until another checkpoint decision is justified.
    reused = await agent._apply_adaptive_context_policy(
        context=context,
        messages=compacted,
        context_compiler_mode="enforce",
    )
    assert reused
    assert checkpoint_calls == [True]
    assert context.context_lifecycle_state.checkpoint_revision == 1


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


@pytest.mark.asyncio
async def test_recovery_checkpoint_without_rewrite_preserves_cache_epoch(monkeypatch):
    agent = LLMAgent.__new__(LLMAgent)
    agent._id = "agent"
    agent._llm = SimpleNamespace(
        _context_checkpoint_policy="adaptive",
        _context_input_budget=100_000,
    )
    context = Context(
        task_id="recovery-only-checkpoint",
        session=SimpleNamespace(session_id="recovery-only-session"),
    )
    context.context_info["context_semantic_progress"] = {
        "agent": {
            "repetition_count": 3,
            "low_information_gain_count": 3,
        }
    }
    observed_cache_boundaries = []
    original_snapshot = context.snapshot

    async def snapshot(*, cache_boundary=True):
        observed_cache_boundaries.append(cache_boundary)
        return await original_snapshot(cache_boundary=cache_boundary)

    monkeypatch.setattr(context, "snapshot", snapshot)

    result = await agent._apply_adaptive_context_policy(
        context=context,
        messages=[
            {"role": "system", "content": "policy"},
            {"role": "user", "content": "short task"},
        ],
        context_compiler_mode="enforce",
    )

    assert result[-1]["role"] == "user"
    assert observed_cache_boundaries == [False]
    assert context.context_lifecycle_state.checkpoint_revision == 0
    assert context.get_pending_cache_break_reasons() == ()


@pytest.mark.asyncio
async def test_adaptive_compaction_restores_verified_continuation_from_sidecar(
    monkeypatch,
):
    agent = LLMAgent.__new__(LLMAgent)
    agent._id = "agent"
    agent._llm = SimpleNamespace(
        _context_checkpoint_policy="adaptive",
        _context_input_budget=100_000,
    )
    context = Context(task_id="adaptive-continuation")
    context.context_info["context_semantic_progress"] = {
        "agent": {
            "repetition_count": 3,
            "low_information_gain_count": 3,
            "no_goal_progress_count": 6,
            "goal_progress": False,
        }
    }

    async def snapshot():
        return SimpleNamespace(id="continuation-checkpoint")

    monkeypatch.setattr(context, "snapshot", snapshot)
    messages = [
        {"role": "system", "content": "policy"},
        {"role": "user", "content": "task"},
        *[{"role": "assistant", "content": f"old {index}"} for index in range(8)],
        {
            "role": "assistant",
            "content": "inspect artifact",
            "tool_calls": [
                {
                    "id": "call-latest",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-latest",
            "content": "verified result",
        },
    ]
    await agent._apply_adaptive_context_policy(
        context=context,
        messages=messages,
        context_compiler_mode="enforce",
    )

    context.context_info["context_semantic_progress"]["agent"].update(
        {
            "repetition_count": 0,
            "low_information_gain_count": 0,
            "no_goal_progress_count": 0,
        }
    )
    restored = await agent._apply_adaptive_context_policy(
        context=context,
        messages=[
            {"role": "system", "content": "policy"},
            {"role": "user", "content": "task"},
        ],
        context_compiler_mode="enforce",
    )

    assert any(message.get("tool_call_id") == "call-latest" for message in restored)
    assert any(
        any(call.get("id") == "call-latest" for call in message.get("tool_calls", []))
        for message in restored
    )
    assert "verified result" not in repr(
        context.context_info["adaptive_context_state:agent"]
    )


@pytest.mark.asyncio
async def test_agent_escalates_repeated_no_progress_checkpoints_and_resets(monkeypatch):
    agent = LLMAgent.__new__(LLMAgent)
    agent._id = "agent"
    agent._llm = SimpleNamespace(
        _context_checkpoint_policy="adaptive",
        _context_input_budget=100_000,
    )
    context = Context(task_id="adaptive-escalation")

    async def snapshot():
        context.advance_context_lifecycle("checkpoint")
        return SimpleNamespace(
            id=f"checkpoint-{context.context_lifecycle_state.turn_epoch}"
        )

    monkeypatch.setattr(context, "snapshot", snapshot)
    messages = [
        {"role": "system", "content": "policy"},
        {"role": "user", "content": "task"},
        *[{"role": "tool", "content": f"evidence {index}"} for index in range(12)],
    ]

    signals = []
    for _ in range(3):
        context.advance_context_lifecycle("next_turn")
        context.advance_context_lifecycle("next_turn")
        context.context_info["context_semantic_progress"] = {
            "agent": {
                "repetition_count": 0,
                "low_information_gain_count": 0,
                "no_goal_progress_count": 6,
                "goal_progress": False,
            }
        }
        compacted = await agent._apply_adaptive_context_policy(
            context=context,
            messages=messages,
            context_compiler_mode="enforce",
        )
        signals.append(compacted[-1]["content"])

    state = context.context_info["adaptive_context_state:agent"]
    assert state["no_progress_checkpoint_count"] == 3
    assert state["escalation_stage"] == "recover"
    assert "Reassess" in signals[0]
    assert "materially different" in signals[1]
    assert "recovery mode" in signals[2]
    assert "evidence 0" not in repr(state)
    metrics = context.context_info["post_tool_progress_metrics"]
    assert metrics["adaptive_checkpoint_count"] == 3
    assert metrics["adaptive_no_progress_checkpoint_count"] == 3
    assert metrics["adaptive_escalation_count"] == 3
    assert metrics["adaptive_escalation_level_max"] == 3

    context.advance_context_lifecycle("next_turn")
    context.context_info["context_semantic_progress"] = {
        "agent": {
            "repetition_count": 0,
            "low_information_gain_count": 0,
            "no_goal_progress_count": 0,
            "goal_progress": True,
        }
    }
    await agent._apply_adaptive_context_policy(
        context=context,
        messages=messages,
        context_compiler_mode="enforce",
    )

    state = context.context_info["adaptive_context_state:agent"]
    assert state["no_progress_checkpoint_count"] == 0
    assert state["escalation_stage"] == "none"
    assert state["goal_progress_reset_count"] == 1
    assert metrics["adaptive_goal_progress_reset_count"] == 1


@pytest.mark.asyncio
async def test_adaptive_escalation_state_is_shared_across_runtime_context_copies(
    monkeypatch,
):
    agent = LLMAgent.__new__(LLMAgent)
    agent._id = "agent"
    agent._llm = SimpleNamespace(
        _context_checkpoint_policy="adaptive",
        _context_input_budget=100_000,
    )
    root = Context(task_id="adaptive-root")
    messages = [
        {"role": "system", "content": "policy"},
        {"role": "user", "content": "task"},
        *[{"role": "tool", "content": str(index)} for index in range(12)],
    ]

    for checkpoint_index in range(2):
        child = root.deep_copy()
        child.event_manager = SimpleNamespace(context=root)
        child.advance_context_lifecycle("next_turn")
        child.advance_context_lifecycle("next_turn")
        child.update_agent_step("agent")
        child.update_agent_step("agent")
        root.context_info["context_semantic_progress"] = {
            "agent": {
                "repetition_count": 0,
                "low_information_gain_count": 0,
                "no_goal_progress_count": 6,
                "goal_progress": False,
            }
        }

        async def snapshot(index=checkpoint_index):
            return SimpleNamespace(id=f"copy-checkpoint-{index}")

        monkeypatch.setattr(child, "snapshot", snapshot)
        await agent._apply_adaptive_context_policy(
            context=child,
            messages=messages,
            context_compiler_mode="enforce",
        )

    state = root.context_info["adaptive_context_state:agent"]
    assert state["no_progress_checkpoint_count"] == 2
    assert state["escalation_stage"] == "diversify"


@pytest.mark.asyncio
async def test_adaptive_runtime_sanitizes_corrupt_resumed_counters(monkeypatch):
    agent = LLMAgent.__new__(LLMAgent)
    agent._id = "agent"
    agent._llm = SimpleNamespace(
        _context_checkpoint_policy="adaptive",
        _context_input_budget=100_000,
    )
    context = Context(task_id="adaptive-corrupt-resume")
    context.context_info["adaptive_context_state:agent"] = {
        "last_checkpoint_turn": "invalid",
        "no_progress_checkpoint_count": "invalid",
        "goal_progress_reset_count": "invalid",
    }
    context.context_info["post_tool_progress_metrics"] = {
        "adaptive_checkpoint_count": "invalid"
    }
    context.context_info["context_semantic_progress"] = {
        "agent": {
            "repetition_count": 0,
            "low_information_gain_count": 0,
            "no_goal_progress_count": 6,
            "goal_progress": False,
        }
    }

    async def snapshot():
        return SimpleNamespace(id="sanitized-checkpoint")

    monkeypatch.setattr(context, "snapshot", snapshot)
    messages = [
        {"role": "system", "content": "policy"},
        {"role": "user", "content": "task"},
        *[{"role": "tool", "content": str(index)} for index in range(12)],
    ]
    compacted = await agent._apply_adaptive_context_policy(
        context=context,
        messages=messages,
        context_compiler_mode="enforce",
    )

    assert "Reassess" in compacted[-1]["content"]
    assert (
        context.context_info["adaptive_context_state:agent"][
            "no_progress_checkpoint_count"
        ]
        == 1
    )
    assert (
        context.context_info["post_tool_progress_metrics"]["adaptive_checkpoint_count"]
        == 1
    )
