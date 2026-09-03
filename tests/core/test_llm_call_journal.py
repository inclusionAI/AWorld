from __future__ import annotations

import hashlib
import json

import aworld.core.context.base as context_base
from aworld.core.context.base import Context
from aworld.core.llm_call_journal import (
    JOURNAL_PATH_ENV,
    append_llm_call_snapshot,
    read_llm_call_journal,
)


def test_context_mutations_append_checksum_valid_snapshots(tmp_path, monkeypatch):
    path = tmp_path / "llm_calls.journal.jsonl"
    monkeypatch.setenv(JOURNAL_PATH_ENV, str(path))
    context = Context(task_id="task-journal")

    context.append_llm_call(
        {"call_id": "call-1", "status": "in_progress"},
        event_type="model_request_started",
    )
    context.replace_llm_call(
        0,
        {
            "call_id": "call-1",
            "request_id": "request-1",
            "status": "in_progress",
            "provider_invoked": True,
            "provider_attempt_status": "attempted",
        },
        event_type="provider_request_attempted",
    )

    recovery = read_llm_call_journal(path)
    assert recovery.available is True
    assert recovery.valid_record_count == 2
    assert recovery.invalid_record_count == 0
    assert recovery.latest_event_type == "provider_request_attempted"
    assert recovery.latest_llm_calls == (
        {
            "call_id": "call-1",
            "request_id": "request-1",
            "status": "in_progress",
            "provider_invoked": True,
            "provider_attempt_status": "attempted",
        },
    )
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert all(record["record_checksum"].startswith("sha256:") for record in records)
    assert all(record["context"] == {"task_id": "task-journal"} for record in records)
    assert [record["stream_sequence"] for record in records] == [0, 1]
    assert records[0]["previous_record_checksum"] is None
    assert records[1]["previous_record_checksum"] == records[0]["record_checksum"]


def test_reader_recovers_last_valid_snapshot_after_torn_final_write(tmp_path):
    path = tmp_path / "llm_calls.journal.jsonl"
    append_llm_call_snapshot(
        context=Context(task_id="task-torn"),
        event_type="model_request_started",
        llm_calls=[{"call_id": "call-1", "status": "in_progress"}],
        path=path,
    )
    with path.open("ab") as stream:
        stream.write(b'{"schema_version":"aworld.llm-call-journal.v1"')

    recovery = read_llm_call_journal(path)

    assert recovery.valid_record_count == 1
    assert recovery.invalid_record_count == 1
    assert recovery.trailing_partial_ignored is True
    assert recovery.latest_llm_calls[0]["call_id"] == "call-1"


def test_corrupt_checksum_is_not_used_as_recovery_truth(tmp_path):
    path = tmp_path / "llm_calls.journal.jsonl"
    append_llm_call_snapshot(
        context=Context(task_id="task-corrupt"),
        event_type="provider_request_attempted",
        llm_calls=[{"request_id": "request-1", "provider_invoked": True}],
        path=path,
    )
    record = json.loads(path.read_text())
    record["llm_calls"][0]["provider_invoked"] = False
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    recovery = read_llm_call_journal(path)

    assert recovery.available is False
    assert recovery.valid_record_count == 0
    assert recovery.invalid_record_count == 1
    assert recovery.latest_llm_calls == ()


def test_journal_is_disabled_without_explicit_path(tmp_path, monkeypatch):
    monkeypatch.delenv(JOURNAL_PATH_ENV, raising=False)
    context = Context(task_id="task-disabled")

    context.append_llm_call({"call_id": "call-1"})

    assert list(tmp_path.iterdir()) == []


def test_replace_mutation_is_compact_and_replays_nested_changes(tmp_path, monkeypatch):
    path = tmp_path / "llm_calls.journal.jsonl"
    monkeypatch.setenv(JOURNAL_PATH_ENV, str(path))
    context = Context(task_id="task-compact")
    original = {
        "request_id": "request-1",
        "status": "in_progress",
        "request": {"messages": [{"role": "user", "content": "x" * 10_000}]},
        "context_rollout": {"candidate_status": "provider_prepared"},
        "error": {"code": "temporary"},
    }
    context.append_llm_call(original, event_type="provider_request_prepared")
    updated = {
        **original,
        "provider_invoked": True,
        "provider_attempt_status": "attempted",
        "context_rollout": {"candidate_status": "provider_attempted"},
    }
    updated.pop("error")
    context.replace_llm_call(0, updated, event_type="provider_request_attempted")

    recovery = read_llm_call_journal(path)

    assert recovery.latest_llm_calls == (updated,)
    assert path.stat().st_size < 15_000
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert records[0]["operation"] == "snapshot"
    assert records[1]["operation"] == "replace"
    assert "llm_call" not in records[1]
    assert "messages" not in json.dumps(records[1]["patch"])


def test_large_records_are_losslessly_compressed_within_record_limit(tmp_path):
    path = tmp_path / "llm_calls.journal.jsonl"
    call = {
        "request_id": "large-request",
        "status": "in_progress",
        "request": {"messages": [{"role": "user", "content": "x" * 2_000_000}]},
    }

    append_llm_call_snapshot(
        context=Context(task_id="task-compressed"),
        event_type="model_request_started",
        llm_calls=[call],
        path=path,
        max_record_bytes=20_000,
    )

    stored = json.loads(path.read_text())
    assert stored["encoding"] == "zlib+base64"
    assert path.stat().st_size < 20_000
    recovery = read_llm_call_journal(path)
    assert recovery.invalid_record_count == 0
    assert recovery.latest_llm_calls == (call,)


def test_reader_streams_journal_without_reading_entire_file(tmp_path, monkeypatch):
    path = tmp_path / "llm_calls.journal.jsonl"
    context = Context(task_id="task-streaming-reader")
    append_llm_call_snapshot(
        context=context,
        event_type="model_request_started",
        llm_calls=[{"request_id": "request-1", "status": "in_progress"}],
        path=path,
    )

    def reject_read_bytes(self):
        raise AssertionError("journal recovery must not load the whole file")

    monkeypatch.setattr(type(path), "read_bytes", reject_read_bytes)

    recovery = read_llm_call_journal(path)

    assert recovery.valid_record_count == 1
    assert recovery.latest_llm_calls[0]["request_id"] == "request-1"


def test_forked_context_streams_replay_independently_and_merge_by_identity(
    tmp_path, monkeypatch
):
    path = tmp_path / "llm_calls.journal.jsonl"
    monkeypatch.setenv(JOURNAL_PATH_ENV, str(path))
    parent = Context(task_id="task-streams")
    parent.append_llm_call({"request_id": "parent-request", "status": "in_progress"})
    child = parent.deep_copy()
    child.append_llm_call({"request_id": "child-request", "status": "in_progress"})
    parent.replace_llm_call(0, {"request_id": "parent-request", "status": "success"})
    child.replace_llm_call(1, {"request_id": "child-request", "status": "failed"})

    recovery = read_llm_call_journal(path)

    assert recovery.available is True
    assert len(recovery.streams) == 2
    assert {stream.stream_id for stream in recovery.streams} == {
        parent._llm_call_journal_stream_id,
        child._llm_call_journal_stream_id,
    }
    merged = {call["request_id"]: call for call in recovery.merged_llm_calls}
    assert merged == {
        "parent-request": {"request_id": "parent-request", "status": "success"},
        "child-request": {"request_id": "child-request", "status": "failed"},
    }


def test_late_child_snapshot_does_not_overwrite_newer_parent_call(
    tmp_path, monkeypatch
):
    path = tmp_path / "llm_calls.journal.jsonl"
    monkeypatch.setenv(JOURNAL_PATH_ENV, str(path))
    parent = Context(task_id="task-stale-child")
    parent.append_llm_call({"call_id": "call-1", "status": "in_progress"})
    child = parent.deep_copy()

    parent.replace_llm_call(
        0,
        {
            "call_id": "call-1",
            "request_id": "request-1",
            "status": "success",
        },
    )
    # The child's first journal write snapshots its inherited stale call after
    # the parent success. Per-call logical timestamps must preserve the newer
    # parent mutation rather than treating the whole child snapshot as newer.
    child.append_llm_call({"request_id": "child-request", "status": "in_progress"})

    recovery = read_llm_call_journal(path)

    merged = {
        call.get("request_id") or call.get("call_id"): call
        for call in recovery.merged_llm_calls
    }
    assert merged == {
        "request-1": {
            "call_id": "call-1",
            "request_id": "request-1",
            "status": "success",
        },
        "child-request": {
            "request_id": "child-request",
            "status": "in_progress",
        },
    }


def test_provider_retries_with_distinct_request_ids_are_not_collapsed(
    tmp_path, monkeypatch
):
    path = tmp_path / "llm_calls.journal.jsonl"
    monkeypatch.setenv(JOURNAL_PATH_ENV, str(path))
    context = Context(task_id="task-provider-retries")
    context.append_llm_call(
        {"call_id": "logical-call", "request_id": "request-1", "status": "failed"}
    )
    retry_context = context.deep_copy()
    retry_context.append_llm_call(
        {
            "call_id": "logical-call",
            "request_id": "request-2",
            "status": "success",
        }
    )

    recovery = read_llm_call_journal(path)

    assert [call["request_id"] for call in recovery.merged_llm_calls] == [
        "request-1",
        "request-2",
    ]


def test_corruption_in_one_context_stream_does_not_poison_sibling_stream(
    tmp_path, monkeypatch
):
    path = tmp_path / "llm_calls.journal.jsonl"
    monkeypatch.setenv(JOURNAL_PATH_ENV, str(path))
    parent = Context(task_id="task-stream-corruption")
    parent.append_llm_call({"request_id": "parent", "status": "in_progress"})
    child = parent.deep_copy()
    child.append_llm_call({"request_id": "child", "status": "in_progress"})
    parent.replace_llm_call(0, {"request_id": "parent", "status": "success"})
    child.replace_llm_call(1, {"request_id": "child", "status": "failed"})
    records = [json.loads(line) for line in path.read_text().splitlines()]
    parent_update = next(
        record
        for record in records
        if record["stream_id"] == parent._llm_call_journal_stream_id
        and record["operation"] == "replace"
    )
    parent_update["record_checksum"] = "sha256:" + "0" * 64
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )

    recovery = read_llm_call_journal(path)

    by_stream = {stream.stream_id: stream for stream in recovery.streams}
    assert by_stream[parent._llm_call_journal_stream_id].invalid_record_count == 1
    assert by_stream[child._llm_call_journal_stream_id].invalid_record_count == 0
    child_calls = by_stream[child._llm_call_journal_stream_id].llm_calls
    assert child_calls[-1] == {"request_id": "child", "status": "failed"}


def test_broken_stream_sequence_isolated_from_interleaved_sibling(
    tmp_path, monkeypatch
):
    path = tmp_path / "llm_calls.journal.jsonl"
    monkeypatch.setenv(JOURNAL_PATH_ENV, str(path))
    parent = Context(task_id="task-stream-order")
    child = parent.deep_copy()
    parent.append_llm_call({"request_id": "parent", "status": "in_progress"})
    child.append_llm_call({"request_id": "child", "status": "in_progress"})
    parent.replace_llm_call(0, {"request_id": "parent", "status": "success"})
    child.replace_llm_call(0, {"request_id": "child", "status": "success"})

    records = [json.loads(line) for line in path.read_text().splitlines()]
    parent_update = next(
        record
        for record in records
        if record["stream_id"] == parent._llm_call_journal_stream_id
        and record["stream_sequence"] == 1
    )
    parent_update["stream_sequence"] = 3
    checksum_payload = {
        key: value for key, value in parent_update.items() if key != "record_checksum"
    }
    encoded = json.dumps(
        checksum_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    parent_update["record_checksum"] = "sha256:" + hashlib.sha256(encoded).hexdigest()
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )

    recovery = read_llm_call_journal(path)

    by_stream = {stream.stream_id: stream for stream in recovery.streams}
    assert by_stream[parent._llm_call_journal_stream_id].invalid_record_count == 1
    assert by_stream[parent._llm_call_journal_stream_id].llm_calls == (
        {"request_id": "parent", "status": "in_progress"},
    )
    assert by_stream[child._llm_call_journal_stream_id].invalid_record_count == 0
    assert by_stream[child._llm_call_journal_stream_id].llm_calls == (
        {"request_id": "child", "status": "success"},
    )


def test_failed_delta_rotates_stream_and_next_snapshot_recovers_state(
    tmp_path, monkeypatch
):
    path = tmp_path / "llm_calls.journal.jsonl"
    monkeypatch.setenv(JOURNAL_PATH_ENV, str(path))
    context = Context(task_id="task-stream-rotation")
    context.append_llm_call({"request_id": "request-1", "status": "in_progress"})
    original_stream_id = context._llm_call_journal_stream_id
    original_append = context_base.append_llm_call_mutation
    attempts = 0

    def fail_once(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("simulated journal write failure")
        return original_append(**kwargs)

    monkeypatch.setattr(context_base, "append_llm_call_mutation", fail_once)
    context.replace_llm_call(
        0, {"request_id": "request-1", "status": "provider_attempted"}
    )
    rotated_stream_id = context._llm_call_journal_stream_id
    context.replace_llm_call(0, {"request_id": "request-1", "status": "success"})

    recovery = read_llm_call_journal(path)

    assert original_stream_id != rotated_stream_id
    assert {stream.stream_id for stream in recovery.streams} == {
        original_stream_id,
        rotated_stream_id,
    }
    assert recovery.invalid_record_count == 0
    assert recovery.merged_llm_calls == (
        {"request_id": "request-1", "status": "success"},
    )
