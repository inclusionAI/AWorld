from __future__ import annotations

import json

from aworld_cli.atif import (
    AtifExportStatus,
    build_atif_trajectory,
    try_write_atif_trajectory,
    write_atif_trajectory,
)


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


def test_build_failed_partial_atif_preserves_authoritative_counts():
    trajectory = build_atif_trajectory(
        {
            "trajectory_capture_mode": "task_response",
            "trajectory_fidelity": "partial",
            "llm_calls": [
                {"request_id": "request-1"},
                {"request_id": "request-2"},
                {"request_id": "request-3"},
            ],
            "trajectory": [
                {
                    "meta": {"session_id": "session-partial", "step": 1},
                    "action": {
                        "content": "I inspected the workspace before the run failed.",
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "function": {
                                    "name": "execute_command",
                                    "arguments": {"command": "ls"},
                                },
                            }
                        ],
                    },
                }
            ],
        },
        prompt="Do the task",
        agent_name="Aworld",
        agent_version="dev",
        run_outcome={
            "schema_version": "aworld.run.outcome.v1",
            "semantic_status": "task_failed",
            "process_exit_code": 1,
            "trajectory_fidelity": "partial",
            "llm_call_count": 3,
            "tool_call_count": 1,
        },
    )

    assert sum(step.get("llm_call_count", 0) for step in trajectory["steps"]) == 3
    assert trajectory["final_metrics"]["extra"] == {
        "llm_call_count": 3,
        "tool_call_count": 1,
        "action_count": 1,
    }
    projection = trajectory["extra"]["aworld"]
    assert projection["completion_state"] == "incomplete"
    assert projection["trajectory_fidelity"] == "partial"
    assert projection["run_outcome"]["semantic_status"] == "task_failed"


def test_build_no_step_failure_does_not_invent_completed_agent_message():
    trajectory = build_atif_trajectory(
        {
            "trajectory_capture_mode": "task_response",
            "trajectory_fidelity": "partial",
            "llm_calls": [
                {"request_id": "request-1"},
                {"request_id": "request-2"},
            ],
            "trajectory": [],
        },
        prompt="Do the task",
        agent_name="Aworld",
        agent_version="dev",
        run_outcome={
            "schema_version": "aworld.run.outcome.v1",
            "semantic_status": "infrastructure_failed",
            "process_exit_code": 1,
            "trajectory_fidelity": "partial",
            "llm_call_count": 2,
            "tool_call_count": 0,
        },
    )

    # ATIF-v1.7 requires at least one step, not a fabricated assistant response.
    assert trajectory["steps"] == [
        {"step_id": 1, "source": "user", "message": "Do the task"}
    ]
    assert trajectory["extra"]["aworld"]["llm_call_count"] == 2
    assert trajectory["extra"]["aworld"]["completion_state"] == "incomplete"


def test_try_write_atif_returns_failure_receipt_without_raising(monkeypatch, tmp_path):
    def fail_write(*_args, **_kwargs):
        raise PermissionError("sensitive filesystem detail")

    monkeypatch.setattr("aworld_cli.atif.write_atif_trajectory", fail_write)

    receipt = try_write_atif_trajectory(
        tmp_path / "trajectory.json",
        {"schema_version": "ATIF-v1.7"},
        trajectory_fidelity="partial",
    )

    assert receipt.status is AtifExportStatus.FAILED
    assert receipt.trajectory_fidelity == "partial"
    assert receipt.error_type == "PermissionError"
    assert "sensitive" not in json.dumps(receipt.to_dict())
