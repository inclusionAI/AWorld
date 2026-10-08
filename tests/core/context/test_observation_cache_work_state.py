from aworld.core.common import ActionModel, ActionResult, Observation
from aworld.core.context.amni import ApplicationContext
from aworld.core.context.compiler import (
    ADAPTIVE_WORK_STATE_KEY,
    adaptive_work_state_message,
)
from aworld.runners.post_tool_progress import record_semantic_tool_progress


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
                        "sandbox_observation": {
                            "observation_id": observation_id,
                            "content_sha256": content_sha256,
                            "workspace_generation": 7,
                            "cache_hit": True,
                            "cache_state": "rehydrated",
                            "content_rehydrated": True,
                            "exact_replay_cached": True,
                            "cache_validation": "exact_generation",
                        }
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
