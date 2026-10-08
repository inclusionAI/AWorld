from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from aworld.agents.llm_agent import Agent, LLMAgent
from aworld.config.conf import AgentConfig
from aworld.core.common import ActionModel, ActionResult, Observation
from aworld.core.context.amni import ApplicationContext
from aworld.core.context.base import Context
from aworld.core.context.session import Session
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
    canonical_json_hash,
    compact_duplicate_tool_results,
    compact_message_history,
    advance_adaptive_continuation_sequence,
    evaluate_adaptive_checkpoint,
    restore_adaptive_continuation,
    semantic_fingerprint,
    estimate_canonical_json_tokens,
)
from aworld.runners.post_tool_progress import (
    acknowledge_semantic_checkpoint,
    record_semantic_tool_progress,
    semantic_progress_for_agent,
)
from aworld.core.event.base import Constants, Message
from aworld.core.memory import MemoryConfig
from aworld.core.task import Task
from aworld.memory.main import MemoryFactory
from aworld.memory.models import MemoryAIMessage, MemoryHumanMessage, MessageMetadata


@pytest.fixture(autouse=True)
def _legacy_semantic_progress_mode(monkeypatch):
    """Legacy assertions below explicitly exercise the pre-ledger behavior."""
    monkeypatch.setenv("AWORLD_SEMANTIC_PROGRESS_LEDGER", "false")


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
    assert state["progress_guard_required"] is True
    assert len(state["recent_operation_result_hashes"]) == 3
    assert state["goal_progress_observable"] is False
    assert state["no_goal_progress_count"] == 0
    acknowledge_semantic_checkpoint(context, agent_id="agent")
    state = semantic_progress_for_agent(context, agent_id="agent")
    assert state["repetition_count"] == 0
    assert state["low_information_gain_count"] == 0
    assert state["no_goal_progress_count"] == 0
    assert state["recent_operation_result_hashes"] == []
    assert state["recent_result_hashes"] == []
    assert state["progress_guard_required"] is False

    state = record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[
            ActionModel(
                tool_name="terminal",
                action_name="run_code",
                tool_call_id="after-checkpoint",
                params={"code": "cat status"},
            )
        ],
        observation=observation,
    )
    assert state["repetition_count"] == 1
    assert state["progress_guard_required"] is False


def test_semantic_progress_detects_abab_operation_result_loop():
    context = Context(task_id="semantic-progress-abab")

    def record(label: str, call_id: str):
        return record_semantic_tool_progress(
            context,
            tool_name="terminal",
            agent_id="agent",
            actions=[
                ActionModel(
                    tool_name="terminal",
                    action_name="run_code",
                    tool_call_id=call_id,
                    params={
                        "code": (
                            "sed -n '214,330p' ars.R"
                            if label == "a"
                            else "sed -n '331,420p' ars.R"
                        )
                    },
                )
            ],
            observation=Observation(
                action_result=[
                    ActionResult(content=f"stable-range-{label}", success=True)
                ]
            ),
        )

    for index, label in enumerate(("a", "b", "a", "b", "a"), start=1):
        state = record(label, f"call-{index}")

    assert state["repetition_count"] == 3
    assert state["low_information_gain_count"] == 3
    assert state["progress_guard_required"] is True
    assert len(state["recent_operation_result_hashes"]) == 5
    assert len(set(state["recent_operation_result_hashes"])) == 2
    assert "sed -n" not in repr(state)
    assert "stable-range" not in repr(state)


def test_unverified_artifact_advance_does_not_reset_recent_pair_window():
    context = Context(task_id="semantic-progress-artifact-reset")
    action = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "cat artifact"},
    )
    unchanged = Observation(
        action_result=[ActionResult(content="same", success=True)]
    )
    for index in range(3):
        state = record_semantic_tool_progress(
            context,
            tool_name="terminal",
            agent_id="agent",
            actions=[action.model_copy(update={"tool_call_id": f"read-{index}"})],
            observation=unchanged,
        )
    assert state["progress_guard_required"] is True

    advanced = record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[action.model_copy(update={"tool_call_id": "mutated"})],
        observation=Observation(
            action_result=[
                ActionResult(
                    content="same",
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

    assert advanced["artifact_advanced"] is True
    assert advanced["diagnostic_progress_observable"] is True
    assert advanced["durable_milestone_advanced"] is False
    assert advanced["progress_guard_reset"] is False
    assert advanced["repetition_count"] == 1
    assert advanced["low_information_gain_count"] == 1
    assert len(advanced["recent_operation_result_hashes"]) == 4
    assert advanced["progress_guard_required"] is False


def test_semantic_progress_new_validation_evidence_resets_recent_pair_window():
    context = Context(task_id="semantic-progress-validation-reset")
    context.configure_completion_contract(
        CompletionContract(
            required_artifacts=(),
            immutable_inputs=(),
            validation_commands=(),
            max_evidence_age_seconds=None,
            required_final_evidence=(),
            required_self_check_ids=("focused-check",),
        ),
        mode=CompletionMode.ENFORCE,
    )
    observed_at = datetime.now(timezone.utc)
    context.record_completion_self_check(
        SelfCheckEvidence(
            command_id="focused-check",
            exit_code=1,
            output_hash="sha256:failed",
            observed_at=observed_at,
        )
    )
    action = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        params={"code": "verify artifact"},
    )
    observation = Observation(
        action_result=[ActionResult(content="unchanged", success=True)]
    )
    for index in range(3):
        state = record_semantic_tool_progress(
            context,
            tool_name="terminal",
            agent_id="agent",
            actions=[action.model_copy(update={"tool_call_id": f"verify-{index}"})],
            observation=observation,
        )
    assert state["progress_guard_required"] is True

    context.record_completion_self_check(
        SelfCheckEvidence(
            command_id="focused-check",
            exit_code=0,
            output_hash="sha256:passed",
            observed_at=observed_at,
        )
    )
    advanced = record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[action.model_copy(update={"tool_call_id": "verify-new"})],
        observation=observation,
    )

    assert advanced["validation_evidence_advanced"] is True
    assert advanced["progress_guard_reset"] is True
    assert advanced["repetition_count"] == 1
    assert advanced["progress_guard_required"] is False


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
    assert "\\u003credacted\\u003e" in continuation


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
        prompt_tokens=95,
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


def test_duplicate_tool_compaction_keeps_newest_complete_observation():
    repeated = "same file range\n" * 80
    messages = [
        {"role": "system", "content": "policy"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "read-1", "type": "function"}]},
        {"role": "tool", "tool_call_id": "read-1", "content": repeated},
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "read-2", "type": "function"}]},
        {"role": "tool", "tool_call_id": "read-2", "content": repeated},
    ]

    compacted, receipt = compact_duplicate_tool_results(messages)

    assert receipt is not None
    assert receipt["duplicate_group_count"] == 1
    assert receipt["compacted_message_count"] == 1
    assert receipt["saved_chars"] > 0
    assert compacted[3]["content"].startswith(
        "AWorld cached duplicate tool observation"
    )
    assert compacted[5]["content"] == repeated
    assert messages[3]["content"] == repeated


def test_duplicate_tool_compaction_requires_exact_substantial_content():
    messages = [
        {"role": "tool", "tool_call_id": "short-1", "content": "same"},
        {"role": "tool", "tool_call_id": "short-2", "content": "same"},
        {"role": "tool", "tool_call_id": "long-1", "content": "a" * 600},
        {"role": "tool", "tool_call_id": "long-2", "content": "b" * 600},
    ]

    compacted, receipt = compact_duplicate_tool_results(messages)

    assert receipt is None
    assert compacted == messages


def test_adaptive_continuation_preserves_committed_prefix_with_new_occurrences():
    previous = [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "prior"},
    ]
    new_system = {"role": "system", "content": "dynamic update"}
    new_result = {"role": "assistant", "content": "new result"}
    current = [new_system, *previous, new_result]

    restored = restore_adaptive_continuation(
        current,
        previous,
        continuation_delta=[new_system, new_result],
        keep_recent=None,
    )

    assert restored[: len(previous)] == previous
    assert restored[len(previous) :] == [new_system, new_result]


def test_adaptive_continuation_uses_sequence_high_water_for_duplicate_occurrence():
    raw_at_checkpoint = [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "same observation"},
        {"role": "assistant", "content": "old work"},
    ]
    capsule = [
        raw_at_checkpoint[0],
        raw_at_checkpoint[1],
        {"role": "user", "content": "AWorld compacted earlier messages."},
        raw_at_checkpoint[-1],
    ]
    _, sequence_state = advance_adaptive_continuation_sequence(
        raw_at_checkpoint,
        None,
        occurrence_ids=[f"memory-{index}" for index in range(4)],
        scope={"task_id": "task", "task_epoch": 0},
    )
    raw_after_checkpoint = [
        *raw_at_checkpoint,
        # This is a new occurrence, even though its bytes equal an old message.
        {"role": "assistant", "content": "same observation"},
        {"role": "tool", "tool_call_id": "new-call", "content": "new result"},
    ]

    delta, next_state = advance_adaptive_continuation_sequence(
        raw_after_checkpoint,
        sequence_state,
        occurrence_ids=[f"memory-{index}" for index in range(6)],
        scope={"task_id": "task", "task_epoch": 0},
    )
    restored = restore_adaptive_continuation(
        raw_after_checkpoint,
        capsule,
        continuation_delta=delta,
        keep_recent=None,
    )

    assert delta == raw_after_checkpoint[-2:]
    assert restored == [*capsule, *raw_after_checkpoint[-2:]]
    assert restored.count({"role": "assistant", "content": "same observation"}) == 1
    assert next_state["high_water_occurrence_id"] == "memory-5"
    assert len(next_state["recent_occurrence_ids"]) <= 32


def test_adaptive_sequence_tail_survives_sliding_memory_window():
    previous_raw = [
        {"role": "assistant", "content": "byte-identical observation"}
        for _ in range(100)
    ]
    previous_ids = [f"memory-{index}" for index in range(100)]
    scope = {"task_id": "task", "task_epoch": 0}
    _, previous_state = advance_adaptive_continuation_sequence(
        previous_raw,
        None,
        occurrence_ids=previous_ids,
        scope=scope,
    )
    appended_duplicate = dict(previous_raw[42])
    # Mirror get_last_n(history_rounds): the oldest occurrence falls out while
    # the newest occurrence may have byte-identical content.
    current_window = [*previous_raw[1:], appended_duplicate]

    delta, next_state = advance_adaptive_continuation_sequence(
        current_window,
        previous_state,
        occurrence_ids=[*previous_ids[1:], "memory-100"],
        scope=scope,
    )

    assert delta == [appended_duplicate]
    assert next_state["high_water_occurrence_id"] == "memory-100"
    assert len(next_state["recent_occurrence_ids"]) == 32


def test_adaptive_sequence_resyncs_after_rewrite_then_resumes_appends():
    scope = {"task_id": "task", "task_epoch": 0}
    old_messages = [
        {"role": "assistant", "content": f"old {index}"} for index in range(10)
    ]
    _, previous_state = advance_adaptive_continuation_sequence(
        old_messages,
        None,
        occurrence_ids=[f"old-{index}" for index in range(10)],
        scope=scope,
    )
    rewritten = [
        {"role": "assistant", "content": f"summary {index}"}
        for index in range(3)
    ]

    delta, recovered_state = advance_adaptive_continuation_sequence(
        rewritten,
        previous_state,
        occurrence_ids=[f"rewrite-{index}" for index in range(3)],
        scope=scope,
    )
    resumed = [*rewritten, {"role": "assistant", "content": "fresh"}]
    resumed_delta, resumed_state = advance_adaptive_continuation_sequence(
        resumed,
        recovered_state,
        occurrence_ids=["rewrite-0", "rewrite-1", "rewrite-2", "rewrite-3"],
        scope=scope,
    )

    assert delta == []
    assert recovered_state["reset_reason"] == "source_rewrite"
    assert resumed_delta == [resumed[-1]]
    assert resumed_state["high_water_occurrence_id"] == "rewrite-3"


def test_adaptive_sequence_migrates_v2_from_retained_causal_occurrence():
    scope = {"task_id": "task", "task_epoch": 0}
    retained = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "checkpoint-call",
                    "type": "function",
                    "function": {"name": "run_code", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "checkpoint-call",
            "content": "checkpoint result",
        },
    ]
    current = [
        {"role": "user", "content": "old task"},
        *retained,
        {"role": "user", "content": "new user delta"},
        {
            "role": "tool",
            "tool_call_id": "new-call",
            "content": "new tool delta",
        },
    ]

    delta, state = advance_adaptive_continuation_sequence(
        current,
        {"schema_version": "aworld.context.adaptive-state/v2"},
        occurrence_ids=[f"memory-{index}" for index in range(len(current))],
        scope=scope,
        legacy_retained_messages=retained,
    )

    assert delta == current[-2:]
    assert state["reset_reason"] == "legacy_causal_migration"
    assert state["high_water_occurrence_id"] == "memory-4"


def test_adaptive_sequence_sanitizes_untrusted_cursor_state():
    scope = {"task_id": "task", "task_epoch": 0}
    previous = {
        "schema_version": "aworld.context.adaptive-continuation-sequence/v2",
        "scope": scope,
        "high_water_occurrence_id": "missing",
        "recent_occurrence_ids": ["x" * 10_000] * 100_000,
        "arbitrary": object(),
    }

    delta, state = advance_adaptive_continuation_sequence(
        [{"role": "assistant", "content": "rewrite"}],
        previous,
        occurrence_ids=["current-1"],
        scope=scope,
    )

    assert delta == []
    assert set(state) == {
        "schema_version",
        "scope",
        "high_water_occurrence_id",
        "recent_occurrence_ids",
        "reset_reason",
    }
    assert state["recent_occurrence_ids"] == ["current-1"]
    assert len(__import__("json").dumps(state)) < 2_000


@pytest.mark.asyncio
async def test_agent_memory_replay_appends_only_post_checkpoint_occurrences(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aworld.memory.main as memory_main

    agent = Agent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="fake-model",
            llm_api_key="fake-key",
        ),
    )
    agent.llm._context_checkpoint_policy = "adaptive"
    agent.llm._context_input_budget = 100_000
    context = Context(
        task_id="adaptive-memory-sequence",
        session=Session(session_id="adaptive-memory-session"),
    )
    context.set_task(
        Task(
            id="adaptive-memory-sequence",
            name="adaptive-memory-sequence",
            session_id="adaptive-memory-session",
            input="task",
        )
    )
    metadata = MessageMetadata(
        agent_id=agent.id(),
        agent_name=agent.name(),
        session_id="adaptive-memory-session",
        task_id="adaptive-memory-sequence",
        user_id="user",
    )
    prior_memory_holder = dict(memory_main.MEMORY_HOLDER)
    memory_main.MEMORY_HOLDER.clear()
    try:
        MemoryFactory.init(
            custom_memory_store=memory_main.InMemoryMemoryStore(),
            config=MemoryConfig(provider="aworld"),
        )
        await MemoryFactory.instance().add(
            MemoryHumanMessage(content="task", metadata=metadata),
            agent_memory_config=agent.memory_config,
        )
        for index in range(12):
            await MemoryFactory.instance().add(
                MemoryAIMessage(content=f"old {index}", metadata=metadata),
                agent_memory_config=agent.memory_config,
            )

        async def skip_memory(*args, **kwargs):
            return None

        async def snapshot(**kwargs):
            context.advance_context_lifecycle("checkpoint")
            return SimpleNamespace(id="memory-sequence-checkpoint")

        monkeypatch.setattr(agent, "_add_message_to_memory", skip_memory)
        monkeypatch.setattr(context, "snapshot", snapshot)
        message = Message(category=Constants.AGENT, headers={"context": context})
        raw = await agent.async_messages_transform(
            observation=Observation(content="task"),
            message=message,
        )
        assert "__aworld_internal_memory_occurrence_id" not in repr(raw)
        projection = context.context_info[
            f"adaptive_memory_projection:{agent.id()}"
        ]
        assert projection["occurrences"]
        assert len(projection["occurrences"]) <= 128
        context.context_info["context_semantic_progress"] = {
            agent.id(): {"repetition_count": 3, "low_information_gain_count": 0}
        }
        capsule = await agent._apply_adaptive_context_policy(
            context=context,
            messages=raw,
            context_compiler_mode="enforce",
        )
        assert not any(item.get("content") == "old 0" for item in capsule)

        await MemoryFactory.instance().add(
            MemoryAIMessage(content="old 0", metadata=metadata),
            agent_memory_config=agent.memory_config,
        )
        await MemoryFactory.instance().add(
            MemoryHumanMessage(
                content="fresh delta",
                metadata=metadata,
                memory_type="message",
            ),
            agent_memory_config=agent.memory_config,
        )
        replayed_raw = await agent.async_messages_transform(
            observation=Observation(content="task"),
            message=message,
        )
        assert "__aworld_internal_memory_occurrence_id" not in repr(replayed_raw)
        context.context_info["context_semantic_progress"] = {
            agent.id(): {"repetition_count": 0, "low_information_gain_count": 0}
        }
        continued = await agent._apply_adaptive_context_policy(
            context=context,
            messages=replayed_raw,
            context_compiler_mode="enforce",
        )

        assert continued[: len(capsule)] == capsule
        assert [item.get("content") for item in continued[-2:]] == [
            "old 0",
            "fresh delta",
        ]
        assert not any(item.get("content") == "old 1" for item in continued)
        state = context.context_info[f"adaptive_context_state:{agent.id()}"]
        assert state["continuation_sequence"][
            "high_water_occurrence_id"
        ].startswith("memory:")
    finally:
        memory_main.MEMORY_HOLDER.clear()
        memory_main.MEMORY_HOLDER.update(prior_memory_holder)


@pytest.mark.asyncio
async def test_public_deliverable_augmentation_preserves_adaptive_occurrence_cursor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    import aworld.memory.main as memory_main

    agent = Agent(
        name="Aworld",
        conf=AgentConfig(
            llm_provider="openai",
            llm_model_name="fake-model",
            llm_api_key="fake-key",
        ),
    )
    agent.llm._context_checkpoint_policy = "adaptive"
    agent.llm._context_input_budget = 100_000
    context = Context(
        task_id="adaptive-public-deliverable",
        session=Session(session_id="adaptive-public-session"),
    )
    context.set_task(
        Task(
            id="adaptive-public-deliverable",
            name="adaptive-public-deliverable",
            session_id="adaptive-public-session",
            input="create result.json with jq",
        )
    )
    context.context_info["public_deliverable_contract"] = {
        "schema_version": "aworld.public-deliverables/v1",
        "authority": "public_task_advisory",
        "source": "public_task_text",
        "artifacts": [
            {
                "deliverable_id": "result",
                "path": str(tmp_path / "result.json"),
                "display_path": "result.json",
                "kind": "file",
                "authority": "public_task_advisory",
            }
        ],
    }
    context.context_info["public_capability_hints"] = {
        "schema_version": "aworld.public-capabilities/v1",
        "authority": "public_task_advisory",
        "source": "public_task_text",
        "executables": [
            {"executable": "jq", "authority": "public_task_advisory"}
        ],
    }
    metadata = MessageMetadata(
        agent_id=agent.id(),
        agent_name=agent.name(),
        session_id="adaptive-public-session",
        task_id="adaptive-public-deliverable",
        user_id="user",
    )
    prior_memory_holder = dict(memory_main.MEMORY_HOLDER)
    memory_main.MEMORY_HOLDER.clear()
    try:
        MemoryFactory.init(
            custom_memory_store=memory_main.InMemoryMemoryStore(),
            config=MemoryConfig(provider="aworld"),
        )
        await MemoryFactory.instance().add(
            MemoryHumanMessage(content="create result.json with jq", metadata=metadata),
            agent_memory_config=agent.memory_config,
        )
        for index in range(12):
            await MemoryFactory.instance().add(
                MemoryAIMessage(content=f"old {index}", metadata=metadata),
                agent_memory_config=agent.memory_config,
            )

        async def skip_memory(*args, **kwargs):
            return None

        async def skip_desc(*args, **kwargs):
            return None

        async def snapshot(**kwargs):
            context.advance_context_lifecycle("checkpoint")
            return SimpleNamespace(id="public-deliverable-checkpoint")

        monkeypatch.setattr(agent, "_add_message_to_memory", skip_memory)
        monkeypatch.setattr(agent, "async_desc_transform", skip_desc)
        monkeypatch.setattr(context, "snapshot", snapshot)
        message = Message(category=Constants.AGENT, headers={"context": context})
        first_raw = await agent.build_llm_input(
            Observation(content="create result.json with jq"),
            message=message,
        )
        assert "AWorld public deliverable milestones" in first_raw[0]["content"]
        assert "__aworld_internal_memory_occurrence_id" not in repr(first_raw)
        projection = context.context_info[
            f"adaptive_memory_projection:{agent.id()}"
        ]
        assert projection["messages_hash"] == canonical_json_hash(first_raw)
        assert all(entry["index"] > 0 for entry in projection["occurrences"])
        context.context_info["context_semantic_progress"] = {
            agent.id(): {"repetition_count": 3, "low_information_gain_count": 0}
        }
        capsule = await agent._apply_adaptive_context_policy(
            context=context,
            messages=first_raw,
            context_compiler_mode="enforce",
        )
        assert not any(item.get("content") == "old 0" for item in capsule)

        await MemoryFactory.instance().add(
            MemoryAIMessage(content="fresh after checkpoint", metadata=metadata),
            agent_memory_config=agent.memory_config,
        )
        second_raw = await agent.build_llm_input(
            Observation(content="continue"),
            message=message,
        )
        context.context_info["context_semantic_progress"] = {
            agent.id(): {"repetition_count": 0, "low_information_gain_count": 0}
        }
        continued = await agent._apply_adaptive_context_policy(
            context=context,
            messages=second_raw,
            context_compiler_mode="enforce",
        )

        assert continued[: len(capsule)] == capsule
        assert continued[-1]["content"] == "fresh after checkpoint"
        assert not any(item.get("content") == "old 0" for item in continued)
        assert "__aworld_internal_memory_occurrence_id" not in repr(continued)
    finally:
        memory_main.MEMORY_HOLDER.clear()
        memory_main.MEMORY_HOLDER.update(prior_memory_holder)


@pytest.mark.asyncio
async def test_adaptive_state_rejects_cross_task_context_fallback():
    agent = LLMAgent.__new__(LLMAgent)
    agent._id = "agent"
    agent._llm = SimpleNamespace(
        _context_checkpoint_policy="adaptive",
        _context_input_budget=100_000,
    )
    context = Context(task_id="new-task")
    context.context_info["adaptive_context_state:agent"] = {
        "schema_version": "aworld.context.adaptive-state/v3",
        "scope": {
            "session_id": None,
            "task_id": "old-task",
            "task_epoch": 0,
            "agent_id": "agent",
        },
        "compaction_active": True,
        "continuation_sequence": {
            "schema_version": "aworld.context.adaptive-continuation-sequence/v2",
            "scope": {"task_id": "old-task", "task_epoch": 0},
            "high_water_occurrence_id": "old-id",
            "recent_occurrence_ids": ["old-id"],
        },
    }
    context.context_info["adaptive_continuation_capsule:agent"] = {
        "schema_version": "aworld.context.adaptive-continuation-capsule/v2",
        "scope": {
            "session_id": None,
            "task_id": "old-task",
            "task_epoch": 0,
            "agent_id": "agent",
        },
        "messages": [{"role": "assistant", "content": "old task secret"}],
    }
    context.context_info["context_semantic_progress"] = {
        "agent": {"repetition_count": 0, "low_information_gain_count": 0}
    }
    current = [{"role": "user", "content": "new task request"}]

    result = await agent._apply_adaptive_context_policy(
        context=context,
        messages=current,
        context_compiler_mode="enforce",
    )

    assert result == current
    assert "old task secret" not in str(result)
    assert "adaptive_context_state:agent" not in context.context_info
    assert "adaptive_continuation_capsule:agent" not in context.context_info


@pytest.mark.asyncio
async def test_agent_defers_duplicate_rewrite_until_explicit_checkpoint(monkeypatch):
    agent = LLMAgent.__new__(LLMAgent)
    agent._id = "agent"
    agent._llm = SimpleNamespace(
        _context_checkpoint_policy="adaptive",
        _context_input_budget=100_000,
    )
    context = Context(task_id="duplicate-epoch-boundary")
    repeated = "same file range\n" * 80
    messages = [
        {"role": "system", "content": "policy"},
        {"role": "user", "content": "task"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "read-1", "type": "function"}],
        },
        {"role": "tool", "tool_call_id": "read-1", "content": repeated},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "read-2", "type": "function"}],
        },
        {"role": "tool", "tool_call_id": "read-2", "content": repeated},
    ]
    context.context_info["context_semantic_progress"] = {
        "agent": {"repetition_count": 0, "low_information_gain_count": 0}
    }

    unchanged = await agent._apply_adaptive_context_policy(
        context=context,
        messages=messages,
        context_compiler_mode="enforce",
    )

    assert unchanged == messages

    async def snapshot(**_kwargs):
        context.advance_context_lifecycle("checkpoint")
        return SimpleNamespace(id="duplicate-checkpoint")

    monkeypatch.setattr(context, "snapshot", snapshot)
    context.context_info["context_semantic_progress"] = {
        "agent": {"repetition_count": 3, "low_information_gain_count": 0}
    }
    checkpointed = await agent._apply_adaptive_context_policy(
        context=context,
        messages=messages,
        context_compiler_mode="enforce",
    )

    assert any(
        message.get("role") == "tool"
        and str(message.get("content", "")).startswith(
            "AWorld cached duplicate tool observation"
        )
        for message in checkpointed
    )
    assert context.context_lifecycle_state.checkpoint_revision == 1


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
                "continuation": dict(
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

    def record_projection(values, occurrence_ids):
        context.context_info["adaptive_memory_projection:agent"] = {
            "schema_version": "aworld.context.adaptive-memory-projection/v2",
            "scope": agent._adaptive_state_scope(context),
            "messages_hash": canonical_json_hash(values),
            "message_count": len(values),
            "occurrences": [
                {"index": index, "occurrence_id": occurrence_id}
                for index, occurrence_id in enumerate(occurrence_ids)
            ],
        }

    occurrence_ids = [f"memory-{index}" for index in range(len(messages))]
    record_projection(messages, occurrence_ids)
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
    assert snapshot_state[0]["continuation"]["messages"] == compacted
    assert snapshot_state[0]["continuation"]["scope"]["task_id"] == (
        "adaptive-runtime"
    )
    assert context.context_lifecycle_state.checkpoint_revision == 1

    # Once compaction is active, later turns append to the same cache epoch
    # until another checkpoint decision is justified.
    record_projection(messages, occurrence_ids)
    reused = await agent._apply_adaptive_context_policy(
        context=context,
        messages=messages,
        context_compiler_mode="enforce",
    )
    assert reused
    assert checkpoint_calls == [True]
    assert context.context_lifecycle_state.checkpoint_revision == 1

    extended_messages = [*messages, {"role": "assistant", "content": "new delta"}]
    record_projection(extended_messages, [*occurrence_ids, "memory-new"])
    extended = await agent._apply_adaptive_context_policy(
        context=context,
        messages=extended_messages,
        context_compiler_mode="enforce",
    )
    assert extended[: len(reused)] == reused
    assert extended[-1] == {"role": "assistant", "content": "new delta"}
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
    scope = agent._adaptive_state_scope(context)
    context.context_info["adaptive_memory_projection:agent"] = {
        "schema_version": "aworld.context.adaptive-memory-projection/v2",
        "scope": scope,
        "messages_hash": canonical_json_hash(messages),
        "message_count": len(messages),
        "occurrences": [
            {"index": index, "occurrence_id": f"memory-{index}"}
            for index in range(len(messages))
        ],
    }
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
    rewritten = [
        {"role": "system", "content": "policy"},
        {"role": "user", "content": "task"},
    ]
    context.context_info["adaptive_memory_projection:agent"] = {
        "schema_version": "aworld.context.adaptive-memory-projection/v2",
        "scope": scope,
        "messages_hash": canonical_json_hash(rewritten),
        "message_count": len(rewritten),
        "occurrences": [
            {"index": 0, "occurrence_id": "rewrite-system"},
            {"index": 1, "occurrence_id": "rewrite-task"},
        ],
    }
    restored = await agent._apply_adaptive_context_policy(
        context=context,
        messages=rewritten,
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
