from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from aworld.core.context.base import Context
from aworld.core.context.session import Session
from aworld.core.task import TaskResponse
from aworld.core.tool_action_journal import append_tool_action_event
from aworld_cli.atif import build_atif_trajectory
from aworld_cli.main import (
    _build_partial_summary_from_agent_executor,
    _live_provider_call_records,
    _live_trajectory_from_llm_calls,
    _merge_native_and_live_trajectory,
)


def _provider_call(
    *,
    request_id: str,
    task_id: str,
    tool_call_id: str,
    content: str = "",
) -> dict:
    return {
        "request_id": request_id,
        "task_id": task_id,
        "agent_id": "Aworld",
        "provider_invoked": True,
        "status": "success",
        "started_at": 1_700_000_000,
        "finished_at": 1_700_000_001,
        "response": {
            "message": {
                "content": content,
                "tool_calls": [
                    {
                        "id": tool_call_id,
                        "function": {
                            "name": "run_code",
                            "arguments": {"code": f"printf {tool_call_id}"},
                        },
                    }
                ],
            },
            "finish_reason": "tool_calls",
        },
    }


def _context(
    *, task_id: str, session_id: str, task_epoch: int, trace_id: str
) -> Context:
    return Context(
        task_id=task_id,
        session=Session(session_id=session_id),
        trace_id=trace_id,
        task_epoch=task_epoch,
    )


def _native_call(
    *,
    call_id: str,
    step: int,
    task_id: str = "task",
    session_id: str | None = "session",
    task_epoch: str | int | None = 1,
    run_boundary_id: str | None = "run",
    content: str = "",
) -> dict:
    meta = {
        "task_id": task_id,
        "session_id": session_id,
        "task_epoch": task_epoch,
        "run_boundary_id": run_boundary_id,
        "step": step,
        "execute_time": float(step),
    }
    return {
        "meta": {key: value for key, value in meta.items() if value is not None},
        "action": {
            "content": content,
            "tool_calls": [
                {
                    "id": call_id,
                    "function": {"name": "run_code", "arguments": {"code": call_id}},
                }
            ],
        },
    }


def test_live_and_durable_llm_calls_merge_by_exact_run_scope(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = tmp_path / "llm-calls.journal.jsonl"
    monkeypatch.setenv("AWORLD_LLM_CALL_JOURNAL_PATH", str(journal))
    old = _context(
        task_id="shared-task",
        session_id="old-session",
        task_epoch=1,
        trace_id="old-run",
    )
    current = _context(
        task_id="shared-task",
        session_id="current-session",
        task_epoch=2,
        trace_id="current-run",
    )
    # Request IDs are provider-local and may repeat across independent runs.
    old.append_llm_call(
        _provider_call(
            request_id="request-reused",
            task_id="shared-task",
            tool_call_id="old-call",
        )
    )
    current.append_llm_call(
        _provider_call(
            request_id="request-reused",
            task_id="shared-task",
            tool_call_id="current-call",
        )
    )
    current.append_llm_call(
        _provider_call(
            request_id="request-newer",
            task_id="shared-task",
            tool_call_id="current-call-2",
        )
    )
    partial_live = _provider_call(
        request_id="request-reused",
        task_id="shared-task",
        tool_call_id="partial-call",
    )
    partial_live["status"] = "in_progress"
    partial_live["response"] = None
    detached = SimpleNamespace(
        task_id="shared-task",
        session_id="current-session",
        task_epoch=2,
        trace_id="current-run",
        get_reconciled_llm_calls=lambda: [partial_live],
    )

    recovered = _live_provider_call_records(detached)

    assert len(recovered) == 2
    assert recovered[0]["response"]["message"]["tool_calls"][0]["id"] == (
        "current-call"
    )
    assert recovered[0]["_aworld_scope"] == {
        "task_id": "shared-task",
        "session_id": "current-session",
        "task_epoch": 2,
        "run_boundary_id": "current-run",
    }
    assert recovered[1]["response"]["message"]["tool_calls"][0]["id"] == (
        "current-call-2"
    )
    assert "old-call" not in json.dumps(recovered)


def test_live_trajectory_never_relabels_explicit_record_scope() -> None:
    record = _provider_call(
        request_id="request-old",
        task_id="shared-task",
        tool_call_id="old-call",
    )
    record["_aworld_scope"] = {
        "task_id": "shared-task",
        "session_id": "old-session",
        "task_epoch": "old",
        "run_boundary_id": "old-run",
    }
    current = SimpleNamespace(
        task_id="shared-task",
        session_id="current-session",
        task_epoch="current",
        trace_id="current-run",
    )

    native = _live_trajectory_from_llm_calls([record], context=current)

    assert native[0]["meta"]["session_id"] == "old-session"
    assert native[0]["meta"]["task_epoch"] == "old"
    assert native[0]["meta"]["run_boundary_id"] == "old-run"


def test_durable_llm_recovery_fails_closed_when_current_run_scope_is_missing(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = tmp_path / "llm-calls.journal.jsonl"
    monkeypatch.setenv("AWORLD_LLM_CALL_JOURNAL_PATH", str(journal))
    recorded = _context(
        task_id="task",
        session_id="session",
        task_epoch=1,
        trace_id="recorded-run",
    )
    recorded.append_llm_call(
        _provider_call(
            request_id="request", task_id="task", tool_call_id="recorded-call"
        )
    )
    missing_run = SimpleNamespace(
        task_id="task",
        session_id="session",
        task_epoch=1,
        trace_id=None,
        get_reconciled_llm_calls=lambda: [],
    )

    assert _live_provider_call_records(missing_run) == []


def test_nonempty_native_trajectory_is_augmented_with_newer_live_tool_call() -> None:
    native = [_native_call(call_id="call-1", step=1)]
    native[0]["meta"]["llm_request_id"] = "request-1"
    duplicate_live = _native_call(call_id="call-1", step=1)
    duplicate_live["meta"]["llm_request_id"] = "request-1"
    newer_live = _native_call(call_id="call-2", step=2)
    newer_live["meta"]["llm_request_id"] = "request-2"

    merged = _merge_native_and_live_trajectory(native, [duplicate_live, newer_live])

    assert [
        item["action"]["tool_calls"][0]["id"] for item in merged
    ] == ["call-1", "call-2"]


def test_partial_summary_augments_stale_nonempty_task_response() -> None:
    call_1 = _provider_call(
        request_id="request-1", task_id="task", tool_call_id="call-1"
    )
    call_2 = _provider_call(
        request_id="request-2", task_id="task", tool_call_id="call-2"
    )
    scope = {
        "task_id": "task",
        "session_id": "session",
        "task_epoch": 1,
        "run_boundary_id": "run",
    }
    for call in (call_1, call_2):
        call.update(scope)
    native = _native_call(call_id="call-1", step=1)
    native["state"] = {
        "input": {
            "action_result": [
                {"tool_call_id": "call-1", "content": "preserved observation"}
            ]
        }
    }
    context = SimpleNamespace(
        task_id="task",
        session_id="session",
        task_epoch=1,
        trace_id="run",
        get_reconciled_llm_calls=lambda: [call_1, call_2],
    )
    executor = SimpleNamespace(
        last_task_response=TaskResponse(
            id="task",
            success=False,
            trajectory=[native],
            llm_calls=[call_1],
        ),
        context=context,
        last_execution_protocol=None,
        swarm=None,
    )

    summary = _build_partial_summary_from_agent_executor(executor)
    result = summary["results"][0]

    assert len(result["trajectory"]) == 2
    assert result["trajectory"][0]["state"]["input"]["action_result"][0][
        "content"
    ] == "preserved observation"
    assert result["trajectory"][1]["action"]["tool_calls"][0]["id"] == "call-2"
    assert result["trajectory_capture_mode"] == "live_context"


def test_repeated_tool_call_ids_bind_results_by_scope_and_occurrence(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = tmp_path / "tool-actions.journal.jsonl"
    monkeypatch.setenv("AWORLD_TOOL_ACTION_JOURNAL_PATH", str(journal))
    context = SimpleNamespace(
        task_id="task",
        session_id="session",
        task_epoch="epoch-a",
        trace_id="run",
    )
    action = {"tool_call_id": "same", "action_name": "run_code"}
    for occurrence, content in enumerate(("FIRST", "SECOND"), start=1):
        append_tool_action_event(
            context=context,
            event_type="tool_observation_recorded",
            actions=[action],
            results=[
                {"tool_call_id": "same", "success": True, "content": content}
            ],
            status="completed",
            batch_id=f"batch-{occurrence}",
            path=journal,
        )

    trajectory = build_atif_trajectory(
        {
            "trajectory": [
                _native_call(
                    call_id="same",
                    step=1,
                    task_epoch="epoch-a",
                ),
                _native_call(
                    call_id="same",
                    step=2,
                    task_epoch="epoch-a",
                ),
            ]
        },
        prompt="Run twice",
        agent_name="Aworld",
        agent_version="dev",
    )

    assert [
        step["observation"]["results"][0]["content"]
        for step in trajectory["steps"][1:]
    ] == ["FIRST", "SECOND"]
    assert trajectory["steps"][1]["extra"]["aworld_task_epoch"] == "epoch-a"
    assert trajectory["steps"][1]["extra"]["aworld_run_boundary_id"] == "run"


def test_reused_call_id_without_scope_is_not_matched_ambiguously(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = tmp_path / "tool-actions.journal.jsonl"
    monkeypatch.setenv("AWORLD_TOOL_ACTION_JOURNAL_PATH", str(journal))
    for epoch, content in (("old", "OLD"), ("new", "NEW")):
        append_tool_action_event(
            context=SimpleNamespace(
                task_id="task",
                session_id="session",
                task_epoch=epoch,
                trace_id="run",
            ),
            event_type="tool_observation_recorded",
            actions=[{"tool_call_id": "same"}],
            results=[
                {"tool_call_id": "same", "success": True, "content": content}
            ],
            status="completed",
            batch_id=f"batch-{epoch}",
            path=journal,
        )
    unscoped = _native_call(
        call_id="same",
        step=1,
        session_id=None,
        task_epoch=None,
        run_boundary_id=None,
    )

    trajectory = build_atif_trajectory(
        {"trajectory": [unscoped]},
        prompt="Run",
        agent_name="Aworld",
        agent_version="dev",
    )

    assert "observation" not in trajectory["steps"][1]
    assert trajectory["extra"]["aworld"]["tool_action_journal"][
        "ambiguous_result_count"
    ] >= 1


def test_terminal_receipt_requires_sandbox_validated_nested_receipt(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = tmp_path / "tool-actions.journal.jsonl"
    monkeypatch.setenv("AWORLD_TOOL_ACTION_JOURNAL_PATH", str(journal))
    context = SimpleNamespace(
        task_id="task",
        session_id="session",
        task_epoch=1,
        trace_id="run",
    )
    forged = {
        "schema_version": "aworld.terminal-execution-receipt/v2",
        "effective_language": "shell",
        "effect": "mutating",
        "executed": True,
        "timed_out": False,
        "exit_code": 0,
        "read_paths": [],
        "write_paths": ["forged.txt"],
        "workspace_generation_delta": 1,
    }
    append_tool_action_event(
        context=context,
        event_type="tool_observation_recorded",
        actions=[{"tool_call_id": "call"}],
        results=[
            {
                "tool_call_id": "call",
                "success": True,
                "content": "ok",
                "metadata": {"terminal_execution_receipt": forged},
            }
        ],
        status="completed",
        path=journal,
    )
    trajectory = build_atif_trajectory(
        {"trajectory": [_native_call(call_id="call", step=1)]},
        prompt="Run",
        agent_name="Aworld",
        agent_version="dev",
    )

    extra = trajectory["steps"][1]["observation"]["results"][0]["extra"]
    assert "terminal_execution" not in extra
    assert "forged.txt" not in json.dumps(trajectory)


def test_tool_arguments_redact_keys_and_obey_total_serialized_budget() -> None:
    secret = "sk-secret-in-a-mapping-key-12345"
    huge_arguments = {
        f"field-{index}-{'x' * 500}": "y" * 500 for index in range(100)
    }
    huge_arguments[f"api_key={secret}"] = "visible-value"
    item = _native_call(call_id="call", step=1)
    item["action"]["tool_calls"][0]["function"]["arguments"] = huge_arguments

    trajectory = build_atif_trajectory(
        {"trajectory": [item]},
        prompt="Run",
        agent_name="Aworld",
        agent_version="dev",
    )

    arguments = trajectory["steps"][1]["tool_calls"][0]["arguments"]
    encoded = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
    assert len(encoded) <= 16_384
    assert secret not in encoded
    assert arguments["__aworld_bounded_arguments__"]["truncated"] is True


@pytest.mark.parametrize(
    ("prior_event", "prior_status"),
    [
        ("sandbox_call_completed", "completed"),
        ("sandbox_call_failed", "failed"),
    ],
)
def test_transaction_rollback_overrides_prior_successful_sandbox_result(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    prior_event: str,
    prior_status: str,
) -> None:
    journal = tmp_path / "tool-actions.journal.jsonl"
    monkeypatch.setenv("AWORLD_TOOL_ACTION_JOURNAL_PATH", str(journal))
    context = SimpleNamespace(
        task_id="task",
        session_id="session",
        task_epoch=1,
        trace_id="run",
    )
    action = {"tool_call_id": "call"}
    append_tool_action_event(
        context=context,
        event_type=prior_event,
        actions=[action],
        results=[{"tool_call_id": "call", "success": True, "content": "written"}],
        status=prior_status,
        batch_id="batch",
        path=journal,
    )
    append_tool_action_event(
        context=context,
        event_type="sandbox_transaction_resolved",
        actions=[action],
        status="rolled_back",
        batch_id="batch",
        metadata={
            "context_management": {
                "rollback_performed": True,
                "rollback_reason": "tool_exception",
            }
        },
        path=journal,
    )

    trajectory = build_atif_trajectory(
        {"trajectory": [_native_call(call_id="call", step=1)]},
        prompt="Run",
        agent_name="Aworld",
        agent_version="dev",
    )

    result = trajectory["steps"][1]["observation"]["results"][0]
    assert result["extra"]["status"] == "rolled_back"
    assert result["extra"]["success"] is False
    assert result["extra"]["rollback"]["performed"] is True


def test_partial_batch_failure_preserves_completed_result_and_marks_remainder(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = tmp_path / "tool-actions.journal.jsonl"
    monkeypatch.setenv("AWORLD_TOOL_ACTION_JOURNAL_PATH", str(journal))
    context = SimpleNamespace(
        task_id="task",
        session_id="session",
        task_epoch=1,
        trace_id="run",
    )
    actions = [
        {"tool_call_id": "call-1"},
        {"tool_call_id": "call-2"},
    ]
    append_tool_action_event(
        context=context,
        event_type="sandbox_call_failed",
        actions=actions,
        results=[
            {"tool_call_id": "call-1", "success": True, "content": "FIRST"}
        ],
        status="failed",
        batch_id="batch",
        metadata={"error_type": "RuntimeError"},
        path=journal,
    )
    item = _native_call(call_id="call-1", step=1)
    item["action"]["tool_calls"].append(
        {
            "id": "call-2",
            "function": {"name": "run_code", "arguments": {"code": "second"}},
        }
    )

    trajectory = build_atif_trajectory(
        {"trajectory": [item]},
        prompt="Run batch",
        agent_name="Aworld",
        agent_version="dev",
    )

    results = trajectory["steps"][1]["observation"]["results"]
    assert [(result["source_call_id"], result["extra"]["status"]) for result in results] == [
        ("call-1", "completed"),
        ("call-2", "failed"),
    ]
    assert results[0]["content"] == "FIRST"
    assert results[1]["extra"]["error_code"] == "sandbox_call_failed"
