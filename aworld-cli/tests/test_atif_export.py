from __future__ import annotations

import json

from aworld_cli.atif import build_atif_trajectory, write_atif_trajectory


def test_build_atif_trajectory_preserves_tools_and_observations():
    payload = {
        "trajectory_capture_mode": "task_response",
        "trajectory": [
            {
                "meta": {
                    "session_id": "session-1",
                    "task_id": "task-1",
                    "agent_id": "Aworld",
                    "step": 1,
                    "execute_time": 1_700_000_000,
                },
                "action": {
                    "content": "<think>inspect the workspace</think>Running ls.",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "function": {
                                "name": "mcp",
                                "arguments": '{"command": "ls"}',
                            },
                        }
                    ],
                },
            },
            {
                "meta": {
                    "session_id": "session-1",
                    "task_id": "task-1",
                    "agent_id": "Aworld",
                    "step": 2,
                },
                "state": {
                    "input": {
                        "action_result": [
                            {
                                "tool_call_id": "call-1",
                                "content": "report.json",
                            }
                        ]
                    }
                },
                "action": {"content": "Done.", "tool_calls": []},
            },
        ],
    }

    trajectory = build_atif_trajectory(
        payload,
        prompt="Create report.json",
        agent_name="Aworld",
        agent_version="0.2.8",
        model_name="test-model",
    )

    assert trajectory["schema_version"] == "ATIF-v1.7"
    assert trajectory["session_id"] == "session-1"
    assert trajectory["steps"][0] == {
        "step_id": 1,
        "source": "user",
        "message": "Create report.json",
    }
    first_agent_step = trajectory["steps"][1]
    assert first_agent_step["message"] == "Running ls."
    assert first_agent_step["reasoning_content"] == "inspect the workspace"
    assert first_agent_step["tool_calls"][0] == {
        "tool_call_id": "call-1",
        "function_name": "mcp",
        "arguments": {"command": "ls"},
    }
    assert first_agent_step["observation"]["results"][0] == {
        "source_call_id": "call-1",
        "content": "report.json",
    }
    assert trajectory["final_metrics"]["total_steps"] == 3


def test_build_atif_trajectory_has_valid_fallback_step():
    trajectory = build_atif_trajectory(
        {"trajectory": [], "trajectory_capture_mode": "summary_synthetic"},
        prompt="Do the task",
        agent_name="Aworld",
        agent_version="dev",
    )

    assert [step["step_id"] for step in trajectory["steps"]] == [1, 2]
    assert trajectory["steps"][1]["llm_call_count"] == 0


def test_write_atif_trajectory_creates_parent_directory(tmp_path):
    output_path = tmp_path / "logs" / "agent" / "trajectory.json"
    payload = {"schema_version": "ATIF-v1.7"}

    write_atif_trajectory(output_path, payload)

    assert json.loads(output_path.read_text(encoding="utf-8")) == payload
