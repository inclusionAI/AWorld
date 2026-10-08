from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from aworld.core.context.base import Context
from aworld.core.context.session import Session
from aworld.core.tool_action_journal import append_tool_action_event

from aworld_cli.atif import (
    AtifExportStatus,
    build_atif_trajectory,
    try_write_atif_trajectory,
    write_atif_trajectory,
)
from aworld_cli.main import (
    _live_provider_call_records,
    _live_trajectory_from_llm_calls,
)
from aworld_cli.top_level_commands.run_cmd import (
    _cleanup_transient_trajectory_journals,
    _configure_transient_trajectory_journals,
)


def test_atif_exports_complete_provider_usage_without_counting_mirrored_calls():
    call = {
        "request_id": "r1",
        "usage_available": True,
        "usage_raw": {"prompt_tokens": 7, "completion_tokens": 3},
    }
    trajectory = build_atif_trajectory(
        {"llm_calls": [call, dict(call)]},
        prompt="work",
        agent_name="Aworld",
        agent_version="dev",
        run_outcome={"llm_call_count": 1},
    )
    assert trajectory["final_metrics"]["total_prompt_tokens"] == 7
    assert trajectory["final_metrics"]["total_completion_tokens"] == 3
    assert trajectory["final_metrics"]["total_cached_tokens"] is None


def test_atif_exports_exact_cached_tokens_without_fabricating_missing_usage():
    trajectory = build_atif_trajectory(
        {
            "llm_calls": [
                {
                    "request_id": "r1",
                    "usage_available": True,
                    "usage_reported": True,
                    "usage_raw": {
                        "prompt_tokens": 100,
                        "completion_tokens": 3,
                        "total_tokens": 103,
                        "prompt_tokens_details": {"cached_tokens": 80},
                    },
                    "usage_normalized": {
                        "prompt_tokens": 100,
                        "completion_tokens": 3,
                        "total_tokens": 103,
                        "cache_hit_tokens": 80,
                    },
                }
            ]
        },
        prompt="work",
        agent_name="Aworld",
        agent_version="dev",
        run_outcome={"llm_call_count": 1},
    )

    assert trajectory["final_metrics"]["total_cached_tokens"] == 80
    assert trajectory["final_metrics"]["extra"]["llm_diagnostics"]["cache"] == {
        "reported": True,
        "reported_call_count": 1,
        "unreported_call_count": 0,
        "write_reported_call_count": 0,
        "measured_cache_read_tokens": 80,
        "cache_read_tokens": 80,
        "cache_read_ratio": 0.8,
    }


def test_atif_preserves_provider_reported_zero_cache_hit() -> None:
    trajectory = build_atif_trajectory(
        {
            "llm_calls": [
                {
                    "request_id": "r1",
                    "usage_available": True,
                    "usage_reported": True,
                    "usage_raw": {
                        "prompt_tokens": 10,
                        "completion_tokens": 2,
                        "total_tokens": 12,
                        "prompt_tokens_details": {"cached_tokens": 0},
                    },
                    "usage_normalized": {
                        "prompt_tokens": 10,
                        "completion_tokens": 2,
                        "total_tokens": 12,
                        "cache_hit_tokens": 0,
                    },
                }
            ]
        },
        prompt="work",
        agent_name="Aworld",
        agent_version="dev",
        run_outcome={"llm_call_count": 1},
    )

    assert trajectory["final_metrics"]["total_cached_tokens"] == 0
    assert (
        trajectory["final_metrics"]["extra"]["llm_diagnostics"]["cache"]["reported"]
        is True
    )


@pytest.mark.parametrize(
    "missing",
    [
        {
            "request_id": "r2",
            "usage_available": False,
            "usage_raw": {"prompt_tokens": 0, "completion_tokens": 0},
        },
        {"request_id": "r2", "usage_raw": {"prompt_tokens": 0, "completion_tokens": 0}},
        {
            "request_id": "r2",
            "usage_available": True,
            "usage_raw": {"prompt_tokens": 7},
        },
    ],
)
def test_atif_does_not_turn_partial_or_missing_usage_into_run_totals(missing):
    trajectory = build_atif_trajectory(
        {
            "llm_calls": [
                {
                    "request_id": "r1",
                    "usage_raw": {"prompt_tokens": 7, "completion_tokens": 3},
                },
                missing,
            ]
        },
        prompt="work",
        agent_name="Aworld",
        agent_version="dev",
        run_outcome={"llm_call_count": 2},
    )
    assert "total_prompt_tokens" not in trajectory["final_metrics"]
    assert "total_completion_tokens" not in trajectory["final_metrics"]
    assert trajectory["final_metrics"]["total_cached_tokens"] is None


def test_atif_keeps_explicit_zero_usage_distinct_from_unknown():
    trajectory = build_atif_trajectory(
        {
            "llm_calls": [
                {
                    "request_id": "r1",
                    "usage_available": True,
                    "usage_raw": {"prompt_tokens": 0, "completion_tokens": 0},
                }
            ]
        },
        prompt="work",
        agent_name="Aworld",
        agent_version="dev",
        run_outcome={"llm_call_count": 1},
    )
    assert trajectory["final_metrics"]["total_prompt_tokens"] == 0
    assert trajectory["final_metrics"]["total_completion_tokens"] == 0
    assert trajectory["final_metrics"]["total_cached_tokens"] is None


def test_atif_exports_bounded_execution_protocol_telemetry():
    telemetry = {
        "schema_version": "aworld.execution-protocol-telemetry/v1",
        "mode": "guide",
        "phase": "complete",
        "armed": True,
        "event_count": 9,
        "tool_observation_count": 7,
        "stagnant_observations": 0,
        "replan_count": 1,
        "candidate_final_count": 2,
        "final_review_count": 1,
        "repair_count": 1,
        "finalization_entered": True,
        "model_horizon": "long",
        "last_delivery_intent": "validate_candidate",
        "implicit_acceptance_created": True,
        "acceptance_attempt": 2,
        "acceptance_continuation_count": 1,
        "acceptance_disposition": "complete",
        "acceptance_reason": "acceptance_satisfied",
        "acceptance_satisfied": True,
    }
    trajectory = build_atif_trajectory(
        {"execution_protocol": telemetry},
        prompt="work",
        agent_name="Aworld",
        agent_version="dev",
    )

    assert trajectory["final_metrics"]["extra"]["execution_protocol"] == telemetry


def test_atif_drops_malformed_execution_protocol_telemetry():
    trajectory = build_atif_trajectory(
        {
            "execution_protocol": {
                "schema_version": "aworld.execution-protocol-telemetry/v1",
                "mode": "guide",
                "prompt": "must not escape into metrics",
            }
        },
        prompt="work",
        agent_name="Aworld",
        agent_version="dev",
    )

    assert "execution_protocol" not in trajectory["final_metrics"]["extra"]


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
                                "name": "run_code",
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
        "function_name": "run_code",
        "arguments": {"command": "ls"},
    }
    assert first_agent_step["observation"]["results"][0] == {
        "source_call_id": "call-1",
        "content": "report.json",
    }
    assert trajectory["final_metrics"]["total_steps"] == 3


def test_task_response_and_live_recovery_export_the_same_tool_name():
    raw_call = {
        "id": "call-1",
        "function": {
            "name": "run_code",
            "arguments": '{"code": "pwd"}',
        },
    }
    task_response_payload = {
        "trajectory_capture_mode": "task_response",
        "trajectory": [
            {
                "meta": {
                    "session_id": "session-1",
                    "task_id": "task-1",
                    "agent_id": "Aworld",
                    "step": 1,
                },
                "action": {"content": "", "tool_calls": [raw_call]},
            }
        ],
    }
    live_payload = {
        "trajectory_capture_mode": "live_context",
        "trajectory": _live_trajectory_from_llm_calls(
            [
                {
                    "task_id": "task-1",
                    "agent_id": "Aworld",
                    "response": {"message": {"content": "", "tool_calls": [raw_call]}},
                }
            ],
            context=SimpleNamespace(task_id="task-1", session_id="session-1"),
        ),
    }

    task_response = build_atif_trajectory(
        task_response_payload,
        prompt="Inspect the workspace",
        agent_name="Aworld",
        agent_version="dev",
    )
    live_recovery = build_atif_trajectory(
        live_payload,
        prompt="Inspect the workspace",
        agent_name="Aworld",
        agent_version="dev",
    )

    assert (
        task_response["steps"][1]["tool_calls"]
        == live_recovery["steps"][1]["tool_calls"]
    )
    assert task_response["steps"][1]["tool_calls"][0]["function_name"] == "run_code"


def test_atif_labels_tool_call_only_turn_without_claiming_empty_response():
    trajectory = build_atif_trajectory(
        {
            "trajectory": [
                {
                    "meta": {"task_id": "task-1", "step": 1},
                    "action": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "function": {
                                    "name": "run_code",
                                    "arguments": {"code": "pwd"},
                                },
                            }
                        ],
                    },
                }
            ]
        },
        prompt="Inspect the workspace",
        agent_name="Aworld",
        agent_version="dev",
    )

    step = trajectory["steps"][1]
    assert step["message"] == "(tool call only)"
    assert step["extra"]["assistant_response_kind"] == "tool_call_only"
    assert "empty response" not in step["message"]


def test_live_atif_distinguishes_reasoning_only_framework_retry():
    raw_call = {
        "id": "call-2",
        "function": {"name": "run_code", "arguments": {"code": "pwd"}},
    }
    native = _live_trajectory_from_llm_calls(
        [
            {
                "request_id": "request-1",
                "task_id": "task-1",
                "agent_id": "Aworld",
                "diagnostics": {
                    "stream": {"reported": True, "reasoning_chars_observed": 42}
                },
                "response": {
                    "message": {"content": "", "tool_calls": []},
                    "finish_reason": "stop",
                },
            },
            {
                "request_id": "request-2",
                "task_id": "task-1",
                "agent_id": "Aworld",
                "turn_economics": {
                    "schema_version": "aworld.context.turn-economics.v1",
                    "turn_kind": "model",
                    "cause": "framework_retry",
                },
                "response": {
                    "message": {"content": "", "tool_calls": [raw_call]},
                    "finish_reason": "tool_calls",
                },
            },
        ],
        context=SimpleNamespace(task_id="task-1", session_id="session-1"),
    )

    trajectory = build_atif_trajectory(
        {"trajectory_capture_mode": "live_context", "trajectory": native},
        prompt="Inspect the workspace",
        agent_name="Aworld",
        agent_version="dev",
    )

    first = trajectory["steps"][1]
    assert first["message"] == "(reasoning-only response; framework retry followed)"
    assert first["extra"]["assistant_response_kind"] == "reasoning_only_retry"
    assert trajectory["steps"][2]["extra"]["assistant_response_kind"] == (
        "tool_call_only"
    )


def test_deadline_recovery_uses_durable_llm_call_without_live_context(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    llm_journal = tmp_path / "llm-calls.journal.jsonl"
    monkeypatch.setenv("AWORLD_LLM_CALL_JOURNAL_PATH", str(llm_journal))
    context = Context(
        task_id="task-deadline",
        session=Session(session_id="session-deadline"),
        trace_id="run-deadline",
    )
    context.append_llm_call(
        {
            "request_id": "request-deadline",
            "task_id": "task-deadline",
            "agent_id": "Aworld",
            "provider_invoked": True,
            "status": "success",
            "started_at": 1_700_000_000,
            "finished_at": 1_700_000_001,
            "response": {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-deadline",
                            "function": {
                                "name": "run_code",
                                "arguments": {"code": "printf complete"},
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            },
        }
    )
    detached = SimpleNamespace(
        task_id="task-deadline",
        session_id="session-deadline",
        task_epoch=0,
        trace_id="run-deadline",
        get_reconciled_llm_calls=lambda: [],
    )

    calls = _live_provider_call_records(detached)
    native = _live_trajectory_from_llm_calls(calls, context=detached)

    assert [call["request_id"] for call in calls] == ["request-deadline"]
    assert native[0]["meta"]["llm_request_id"] == "request-deadline"
    assert native[0]["action"]["tool_calls"][0]["id"] == "call-deadline"


def test_cli_transient_journals_are_private_and_restore_environment(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("AWORLD_LLM_CALL_JOURNAL_PATH", raising=False)
    monkeypatch.delenv("AWORLD_TOOL_ACTION_JOURNAL_PATH", raising=False)

    state = _configure_transient_trajectory_journals(enabled=True)
    assert state is not None
    directory, owned = state
    assert set(owned) == {
        "AWORLD_LLM_CALL_JOURNAL_PATH",
        "AWORLD_TOOL_ACTION_JOURNAL_PATH",
    }
    assert directory.parent.resolve() == Path(tempfile.gettempdir()).resolve()
    assert all(
        Path(path).parent.resolve() == directory.resolve() for path in owned.values()
    )

    for path in owned.values():
        Path(path).write_text("durable evidence", encoding="utf-8")
    _cleanup_transient_trajectory_journals(state)

    assert not directory.exists()
    assert "AWORLD_LLM_CALL_JOURNAL_PATH" not in os.environ
    assert "AWORLD_TOOL_ACTION_JOURNAL_PATH" not in os.environ


@pytest.mark.parametrize("capture_mode", ["task_response", "live_context"])
def test_atif_recovers_bounded_typed_tool_results_from_durable_journal(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capture_mode: str
):
    journal = tmp_path / "tool-actions.journal.jsonl"
    monkeypatch.setenv("AWORLD_TOOL_ACTION_JOURNAL_PATH", str(journal))
    context = SimpleNamespace(
        task_id="task-journal",
        session_id="session-journal",
        task_epoch="epoch-journal",
        trace_id="run-journal",
    )
    actions = [
        {
            "tool_call_id": "call-inline",
            "tool_name": "terminal",
            "action_name": "run_code",
            "params": {
                "code": "printf ok",
                "api_key": "sk-argumentsecretvalue123",
            },
        },
        {
            "tool_call_id": "call-offloaded",
            "tool_name": "terminal",
            "action_name": "run_code",
            "params": {"code": "large-output"},
        },
        {
            "tool_call_id": "call-cache",
            "tool_name": "terminal",
            "action_name": "run_code",
            "params": {"code": "cat report.txt"},
        },
        {
            "tool_call_id": "call-intercepted",
            "tool_name": "terminal",
            "action_name": "run_code",
            "params": {"code": "cat secret.txt"},
        },
        {
            "tool_call_id": "call-failed",
            "tool_name": "terminal",
            "action_name": "run_code",
            "params": {"code": "false"},
        },
        {
            "tool_call_id": "call-transport-failed",
            "tool_name": "terminal",
            "action_name": "run_code",
            "params": {"code": "transport-failure"},
        },
    ]
    append_tool_action_event(
        context=context,
        event_type="tool_observation_recorded",
        actions=actions[:-1],
        results=[
            {
                "tool_call_id": "call-inline",
                "success": True,
                "content": (
                    "ok\n"
                    + "x" * 20_000
                    + "\napi_key=sk-supersecretvalue123"
                ),
                "metadata": {},
            },
            {
                "tool_call_id": "call-offloaded",
                "success": True,
                "content": {
                    "head": "first lines",
                    "tail": "last lines",
                    "omitted_chars": 90_000,
                    "artifact_ref": "context-artifact://private-location",
                },
                "metadata": {
                    "tool_output_policy": {
                        "reason_code": "oversized_output",
                        "raw_byte_count": 100_000,
                        "raw_checksum": "sha256:" + "a" * 64,
                        "inline_tokens": 64,
                        "offloaded_tokens": 25_000,
                        "artifact_ref": "context-artifact://private-location",
                    }
                },
            },
            {
                "tool_call_id": "call-cache",
                "success": True,
                "content": "cached report",
                "metadata": {
                    "sandbox_observation": {
                        "schema_version": "aworld.sandbox-tool-observation/v1",
                        "effect": "read_only",
                        "cache_hit": True,
                        "changed": False,
                        "workspace_mutated": False,
                        "workspace_generation": 7,
                        "observation_id": "sha256:" + "b" * 64,
                        "terminal_execution_receipt": {
                            "schema_version": "aworld.terminal-execution-receipt/v2",
                            "effective_language": "shell",
                            "effect": "read_only",
                            "executed": True,
                            "timed_out": False,
                            "exit_code": 0,
                            "read_paths": ["report.txt"]
                            + [f"part-{index}.txt" for index in range(39)],
                            "write_paths": [
                                "/tmp/api_key=sk-pathsecretvalue123"
                            ],
                            "workspace_generation_delta": 0,
                        },
                    }
                },
            },
            {
                "tool_call_id": "call-intercepted",
                "success": False,
                "content": {"type": "convergence_gate", "message": "blocked"},
                "error": "tool_call_intercepted",
                "metadata": {
                    "sandbox_observation": {
                        "schema_version": "aworld.sandbox-tool-observation/v1",
                        "effect": "blocked_read_only",
                        "cache_hit": False,
                        "changed": False,
                        "workspace_mutated": False,
                        "workspace_generation": 7,
                        "hook_interception": {
                            "schema_version": "aworld.tool-interception/v1",
                            "kind": "block",
                            "error_code": "tool_call_intercepted",
                            "message": "private policy details",
                        },
                    }
                },
            },
            {
                "tool_call_id": "call-failed",
                "success": False,
                "content": "command failed",
                "error": "nonzero_exit",
                "metadata": {},
            },
        ],
        status="completed",
        path=journal,
    )
    append_tool_action_event(
        context=context,
        event_type="sandbox_call_failed",
        actions=[actions[-1]],
        status="failed",
        metadata={"error_type": "RuntimeError"},
        path=journal,
    )
    tool_calls = [
        {
            "id": action["tool_call_id"],
            "function": {
                "name": action["action_name"],
                "arguments": action["params"],
            },
        }
        for action in actions
    ]

    trajectory = build_atif_trajectory(
        {
            "trajectory_capture_mode": capture_mode,
            "trajectory_fidelity": (
                "partial" if capture_mode == "live_context" else "complete"
            ),
            "trajectory": [
                {
                    "meta": {
                        "task_id": "task-journal",
                        "session_id": "session-journal",
                        "task_epoch": "epoch-journal",
                        "run_boundary_id": "run-journal",
                        "step": 1,
                    },
                    # Deliberately no state.input.action_result: this is the
                    # partial/deadline shape that previously lost every result.
                    "action": {"content": "", "tool_calls": tool_calls},
                }
            ],
        },
        prompt="Run the tools",
        agent_name="Aworld",
        agent_version="dev",
        run_outcome={
            "semantic_status": (
                "budget_exhausted" if capture_mode == "live_context" else "succeeded"
            )
        },
    )

    results = {
        item["source_call_id"]: item
        for item in trajectory["steps"][1]["observation"]["results"]
    }
    arguments = trajectory["steps"][1]["tool_calls"][0]["arguments"]
    assert arguments["api_key"] == "<REDACTED_SECRET>"
    assert "sk-argumentsecretvalue123" not in json.dumps(trajectory)
    assert set(results) == {action["tool_call_id"] for action in actions}
    assert results["call-inline"]["extra"]["status"] == "completed"
    assert "sk-supersecretvalue123" not in results["call-inline"]["content"]
    assert "<REDACTED_SECRET>" in results["call-inline"]["content"]
    assert len(results["call-inline"]["content"]) <= 12_000
    assert results["call-inline"]["extra"]["content"]["truncated"] is True
    assert results["call-offloaded"]["extra"]["output"]["kind"] == "offloaded"
    assert "artifact_ref" not in json.dumps(results["call-offloaded"]["extra"])
    assert results["call-cache"]["extra"]["cache_replay"] is True
    terminal = results["call-cache"]["extra"]["terminal_execution"]
    assert terminal["effective_language"] == "shell"
    assert terminal["effect"] == "read_only"
    assert terminal["executed"] is True
    assert terminal["timed_out"] is False
    assert terminal["exit_code"] == 0
    assert terminal["workspace_generation_delta"] == 0
    assert len(terminal["read_paths"]) == 32
    assert terminal["read_paths_omitted_count"] == 8
    assert "sk-pathsecretvalue123" not in terminal["write_paths"][0]
    assert results["call-intercepted"]["extra"]["status"] == "intercepted"
    assert "private policy details" not in json.dumps(results["call-intercepted"])
    assert results["call-failed"]["extra"]["status"] == "failed"
    assert results["call-failed"]["extra"]["error_code"] == "nonzero_exit"
    assert results["call-transport-failed"]["extra"]["status"] == "failed"
    assert results["call-transport-failed"]["extra"]["error_code"] == (
        "sandbox_call_failed"
    )
    assert results["call-transport-failed"]["extra"]["failure_type"] == (
        "RuntimeError"
    )
    evidence = trajectory["extra"]["aworld"]["tool_action_journal"]
    assert evidence["status"] == "available"
    assert evidence["recovered_result_count"] == 6


def test_atif_does_not_recover_foreign_tool_result_without_task_scope(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    journal = tmp_path / "tool-actions.journal.jsonl"
    monkeypatch.setenv("AWORLD_TOOL_ACTION_JOURNAL_PATH", str(journal))
    append_tool_action_event(
        context=SimpleNamespace(task_id="foreign-task"),
        event_type="tool_observation_recorded",
        actions=[{"tool_call_id": "reused-call"}],
        results=[
            {
                "tool_call_id": "reused-call",
                "success": True,
                "content": "foreign secret result",
            }
        ],
        status="completed",
        path=journal,
    )

    trajectory = build_atif_trajectory(
        {
            "trajectory": [
                {
                    "meta": {"step": 1},
                    "action": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "reused-call",
                                "function": {"name": "run_code", "arguments": {}},
                            }
                        ],
                    },
                }
            ]
        },
        prompt="Run",
        agent_name="Aworld",
        agent_version="dev",
    )

    assert "observation" not in trajectory["steps"][1]
    evidence = trajectory["extra"]["aworld"]["tool_action_journal"]
    assert evidence["status"] == "available"
    assert "foreign secret result" not in json.dumps(trajectory)


def test_atif_scopes_reused_call_id_to_session_and_task_epoch(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    journal = tmp_path / "tool-actions.journal.jsonl"
    monkeypatch.setenv("AWORLD_TOOL_ACTION_JOURNAL_PATH", str(journal))
    action = {"tool_call_id": "reused-call"}
    for session_id, task_epoch, content in (
        ("old-session", 1, "old result"),
        ("current-session", 2, "current result"),
    ):
        append_tool_action_event(
            context=SimpleNamespace(
                task_id="reused-task",
                session_id=session_id,
                task_epoch=task_epoch,
                trace_id="run-reused",
            ),
            event_type="tool_observation_recorded",
            actions=[action],
            results=[
                {
                    "tool_call_id": "reused-call",
                    "success": True,
                    "content": content,
                }
            ],
            status="completed",
            path=journal,
        )
    trajectory = build_atif_trajectory(
        {
            "trajectory": [
                {
                    "meta": {
                        "task_id": "reused-task",
                        "session_id": "current-session",
                        "task_epoch": 2,
                        "run_boundary_id": "run-reused",
                        "step": 1,
                    },
                    "action": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "reused-call",
                                "function": {"name": "run_code", "arguments": {}},
                            }
                        ],
                    },
                }
            ]
        },
        prompt="Run",
        agent_name="Aworld",
        agent_version="dev",
    )

    result = trajectory["steps"][1]["observation"]["results"][0]
    assert result["content"] == "current result"
    assert "old result" not in json.dumps(trajectory)

    ambiguous = build_atif_trajectory(
        {
            "trajectory": [
                {
                    "meta": {"task_id": "reused-task", "step": 1},
                    "action": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "reused-call",
                                "function": {"name": "run_code", "arguments": {}},
                            }
                        ],
                    },
                }
            ]
        },
        prompt="Run",
        agent_name="Aworld",
        agent_version="dev",
    )
    assert "observation" not in ambiguous["steps"][1]
    assert ambiguous["extra"]["aworld"]["tool_action_journal"][
        "ambiguous_call_id_count"
    ] == 1


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
    final_extra = trajectory["final_metrics"]["extra"]
    assert final_extra["llm_call_count"] == 3
    assert final_extra["tool_call_count"] == 1
    assert final_extra["action_count"] == 1
    assert final_extra["llm_diagnostics"]["call_count"] == 3
    assert final_extra["llm_diagnostics"]["usage"]["reported"] is False
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
