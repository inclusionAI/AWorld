from aworld.core.common import ActionModel, ActionResult, Observation
from aworld.core.context.amni import ApplicationContext
from aworld.core.context.compiler import (
    ADAPTIVE_WORK_STATE_KEY,
    advance_adaptive_work_state,
    adaptive_work_state_message,
)
from aworld.runners.post_tool_progress import record_semantic_tool_progress
from aworld.sandbox.tool_observation import classify_tool_effect


def _sandbox_receipt(action: ActionModel, generation: int, **values):
    effect = classify_tool_effect(action)
    return {
        "schema_version": "aworld.sandbox-tool-observation/v1",
        "tool_call_id": action.tool_call_id,
        "canonical_tool": effect.identity,
        "operation_hash": effect.operation_hash,
        "workspace_generation": generation,
        **values,
    }


def test_amni_work_state_retains_sandbox_observation_identity_and_cache_state() -> None:
    context = ApplicationContext.create(
        session_id="observation-cache-session",
        task_id="observation-cache-task",
        task_content="inspect the workspace",
    )
    observation_id = "sha256:" + "a" * 64
    content_sha256 = "sha256:" + "b" * 64
    action = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        tool_call_id="call-cache",
        params={"code": "cat state.txt"},
    )

    record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[action],
        observation=Observation(
            action_result=[
                ActionResult(
                    success=True,
                    tool_name="terminal",
                    action_name="run_code",
                    tool_call_id="call-cache",
                    content="current state\n",
                    metadata={
                        "sandbox_observation": _sandbox_receipt(
                            action,
                            7,
                            observation_id=observation_id,
                            content_sha256=content_sha256,
                            cache_hit=True,
                            cache_state="rehydrated",
                            content_rehydrated=True,
                            exact_replay_cached=True,
                            cache_validation="exact_generation",
                        )
                    },
                )
            ]
        ),
    )

    state = context.read_task_runtime_state("agent", ADAPTIVE_WORK_STATE_KEY)
    stored = context.get(f"{ADAPTIVE_WORK_STATE_KEY}:agent")
    expected = {
        "observation_id": observation_id,
        "content_sha256": content_sha256,
        "cache_state": "rehydrated",
        "cache_validation": "exact_generation",
        "workspace_generation": 7,
        "cache_hit": True,
        "content_rehydrated": True,
        "exact_replay_cached": True,
    }

    assert state["workspace_generation"] == 7
    assert state["latest_sandbox_observations"] == [expected]
    assert state["recent_operations"][0]["sandbox_observations"] == [expected]
    assert state["recent_operations"][0]["results"][0][
        "sandbox_observation"
    ] == expected
    assert stored == state
    message = adaptive_work_state_message(state)
    assert message is not None
    assert observation_id in message["content"]
    assert content_sha256 in message["content"]
    assert '"cache_state":"rehydrated"' in message["content"]


def test_work_state_generation_and_last_mutation_survive_later_control_failure() -> None:
    mutation_observation = {
        "observation_id": "sha256:" + "c" * 64,
        "workspace_generation": 3,
        "cache_hit": False,
    }
    state = advance_adaptive_work_state(
        {},
        {
            "operation_hash": "sha256:write",
            "result_hash": "sha256:write-result",
            "actions": [{"tool": "terminal", "action": "run_code"}],
            "results": [{"success": True}],
            "artifact_changed": True,
            "artifact_fingerprint": None,
            "workspace_generation": 3,
            "mutation_workspace_generation": 3,
            "sandbox_observations": [mutation_observation],
            "goal_progress": False,
            "rollback_performed": False,
            "implicit_artifact_loss": False,
            "available_artifacts": [],
        },
    )
    state = advance_adaptive_work_state(
        state,
        {
            "operation_hash": "sha256:review",
            "result_hash": "sha256:review-failed",
            "actions": [{"tool": "reviewer", "action": "review"}],
            "results": [{"success": False, "error": "unavailable"}],
            "artifact_changed": False,
            "artifact_fingerprint": None,
            # A transported control Tool may have no Sandbox observation and
            # may project the local default. Neither can rewind task state.
            "workspace_generation": 0,
            "sandbox_observations": [],
            "goal_progress": False,
            "rollback_performed": False,
            "implicit_artifact_loss": False,
            "available_artifacts": [],
        },
    )

    assert state["workspace_generation"] == 3
    assert state["latest_sandbox_observations"] == [mutation_observation]
    assert state["latest_workspace_mutation"]["operation_hash"] == "sha256:write"
    message = adaptive_work_state_message(state)
    assert message is not None
    assert '"workspace_generation":3' in message["content"]
    assert '"latest_workspace_mutation"' in message["content"]
    assert '"operation_hash":"sha256:write"' in message["content"]

    for index in range(9):
        state = advance_adaptive_work_state(
            state,
            {
                "operation_hash": f"sha256:control-{index}",
                "result_hash": f"sha256:control-result-{index}",
                "actions": [],
                "results": [],
                "artifact_changed": False,
                "workspace_generation": 3,
                "sandbox_observations": [],
                "goal_progress": False,
                "rollback_performed": False,
                "implicit_artifact_loss": False,
                "available_artifacts": [],
            },
        )
    compacted = adaptive_work_state_message(state)
    assert compacted is not None
    assert '"operation_hash":"sha256:write"' in compacted["content"]
    assert '"mutation_workspace_generation":3' in compacted["content"]


def test_semantic_progress_never_rewinds_generation_without_sandbox_receipt() -> None:
    context = ApplicationContext.create(
        session_id="generation-high-water-session",
        task_id="generation-high-water-task",
        task_content="write and review the candidate",
    )
    write = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        tool_call_id="write-candidate",
        params={"code": "printf candidate > result.txt"},
    )
    first = record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[write],
        observation=Observation(
            action_result=[
                ActionResult(
                    success=True,
                    tool_name="terminal",
                    action_name="run_code",
                    tool_call_id="write-candidate",
                    content="",
                    metadata={
                        "context_management": {
                            "schema_version": "aworld.sandbox-artifact-progress/v1",
                            "artifact_changed": True,
                            "artifact_fingerprint_after": "fresh-fingerprint",
                        },
                        "sandbox_observation": _sandbox_receipt(
                            write,
                            5,
                            workspace_mutated=True,
                            effect="mutating",
                        )
                    },
                )
            ]
        ),
    )
    assert first["workspace_generation"] == 5

    stale = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        tool_call_id="stale-write",
        params={"code": "printf stale > result.txt"},
    )
    stale_state = record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[stale],
        observation=Observation(
            action_result=[
                ActionResult(
                    success=True,
                    tool_name="terminal",
                    action_name="run_code",
                    tool_call_id="stale-write",
                    content="",
                    metadata={
                        "context_management": {
                            "schema_version": "aworld.sandbox-artifact-progress/v1",
                            "artifact_changed": True,
                            "artifact_fingerprint_after": "stale-fingerprint",
                        },
                        "sandbox_observation": _sandbox_receipt(
                            stale,
                            3,
                            workspace_mutated=True,
                            effect="mutating",
                        ),
                    },
                )
            ]
        ),
    )
    assert stale_state["workspace_generation"] == 5
    assert stale_state["artifact_fingerprint"] == "fresh-fingerprint"

    review = ActionModel(
        tool_name="reviewer",
        action_name="review_candidate",
        tool_call_id="failed-review",
        params={},
    )
    second = record_semantic_tool_progress(
        context,
        tool_name="reviewer",
        agent_id="agent",
        actions=[review],
        observation=Observation(
            action_result=[
                ActionResult(
                    success=False,
                    tool_name="reviewer",
                    action_name="review_candidate",
                    tool_call_id="failed-review",
                    content="unavailable",
                    error="reviewer_unavailable",
                )
            ]
        ),
    )

    assert second["workspace_generation"] == 5
    state = context.read_task_runtime_state("agent", ADAPTIVE_WORK_STATE_KEY)
    assert state["workspace_generation"] == 5
    assert state["latest_workspace_mutation"]["operation_hash"] == (
        first["operation_hash"]
    )


def test_unbound_sandbox_receipt_cannot_advance_workspace_generation() -> None:
    context = ApplicationContext.create(
        session_id="unbound-receipt-session",
        task_id="unbound-receipt-task",
        task_content="inspect state",
    )
    action = ActionModel(
        tool_name="reviewer",
        action_name="review_candidate",
        tool_call_id="actual-call",
        params={},
    )

    state = record_semantic_tool_progress(
        context,
        tool_name="reviewer",
        agent_id="agent",
        actions=[action],
        observation=Observation(
            action_result=[
                ActionResult(
                    success=True,
                    tool_call_id="actual-call",
                    content="ok",
                    metadata={
                        "sandbox_observation": {
                            "schema_version": "aworld.sandbox-tool-observation/v1",
                            "tool_call_id": "different-call",
                            "canonical_tool": "terminal:run_code",
                            "operation_hash": "sha256:" + "f" * 64,
                            "workspace_generation": 999_999,
                            "workspace_mutated": True,
                            "effect": "mutating",
                        }
                    },
                )
            ]
        ),
    )

    assert state["workspace_generation"] == 0
    work_state = context.read_task_runtime_state("agent", ADAPTIVE_WORK_STATE_KEY)
    assert work_state["workspace_generation"] == 0
    assert work_state["latest_workspace_mutation"] is None
