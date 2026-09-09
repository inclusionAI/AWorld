import json

from aworld.core.common import ActionModel, ActionResult
from aworld.core.context.base import Context
from aworld.core.tool_action_journal import (
    append_tool_action_event,
    read_tool_action_journal,
    tool_action_batch_id,
)


def test_tool_action_journal_recovers_real_boundary_events(tmp_path):
    path = tmp_path / "tool-actions.journal.jsonl"
    context = Context(task_id="task-1")
    actions = [
        {
            "tool_call_id": "call-1",
            "tool_name": "terminal",
            "action_name": "run_code",
            "params": {"code": "echo ok"},
        }
    ]
    batch_id = tool_action_batch_id(actions)
    append_tool_action_event(
        context=context,
        event_type="sandbox_call_started",
        actions=actions,
        status="in_progress",
        batch_id=batch_id,
        path=path,
    )
    append_tool_action_event(
        context=context,
        event_type="tool_observation_recorded",
        actions=actions,
        results=[{"success": True, "content": "ok"}],
        status="success",
        batch_id=batch_id,
        metadata={"source": "model_visible_observation"},
        path=path,
    )

    recovery = read_tool_action_journal(path)

    assert recovery.available
    assert recovery.invalid_record_count == 0
    assert recovery.stream_count == 1
    assert [event["event_type"] for event in recovery.events] == [
        "sandbox_call_started",
        "tool_observation_recorded",
    ]
    assert {event["batch_id"] for event in recovery.events} == {batch_id}
    assert recovery.events[-1]["results"][0]["content"] == "ok"


def test_tool_action_journal_ignores_torn_final_record(tmp_path):
    path = tmp_path / "tool-actions.journal.jsonl"
    context = Context(task_id="task-1")
    append_tool_action_event(
        context=context,
        event_type="sandbox_call_started",
        actions=[{"tool_call_id": "call-1"}],
        status="in_progress",
        path=path,
    )
    with path.open("ab") as stream:
        stream.write(b'{"schema_version":"aworld.tool-action-journal.v1"')

    recovery = read_tool_action_journal(path)

    assert recovery.valid_record_count == 1
    assert recovery.invalid_record_count == 1
    assert recovery.trailing_partial_ignored is True


def test_tool_action_journal_rejects_checksum_tampering(tmp_path):
    path = tmp_path / "tool-actions.journal.jsonl"
    context = Context(task_id="task-1")
    append_tool_action_event(
        context=context,
        event_type="sandbox_call_started",
        actions=[{"tool_call_id": "call-1"}],
        status="in_progress",
        path=path,
    )
    record = json.loads(path.read_text())
    record["status"] = "success"
    path.write_text(json.dumps(record) + "\n")

    recovery = read_tool_action_journal(path)

    assert recovery.available is False
    assert recovery.invalid_record_count == 1


def test_tool_action_journal_preserves_structured_runtime_models(tmp_path):
    path = tmp_path / "tool-actions.journal.jsonl"
    context = Context(task_id="task-1")
    action = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        tool_call_id="call-1",
        params={"code": "opaque-reader"},
    )
    result = ActionResult(
        success=False,
        content="transaction rolled back",
        metadata={
            "context_management": {
                "rollback_performed": True,
                "rollback_reason": "unexpected_implicit_artifact_loss",
            }
        },
    )

    append_tool_action_event(
        context=context,
        event_type="sandbox_transaction_resolved",
        actions=[action],
        results=[result],
        status="rolled_back",
        path=path,
    )

    event = read_tool_action_journal(path).events[0]
    assert event["actions"][0]["action_name"] == "run_code"
    assert event["actions"][0]["params"] == {"code": "opaque-reader"}
    assert event["results"][0]["success"] is False
    assert (
        event["results"][0]["metadata"]["context_management"][
            "rollback_reason"
        ]
        == "unexpected_implicit_artifact_loss"
    )
