from __future__ import annotations

import json
import multiprocessing
import os
import stat
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from aworld.core.trajectory import (
    TrajectoryBuildResult,
    TrajectoryBuildStatus,
    TrajectoryFidelity,
    TrajectoryReasonCode,
    TrajectorySourceKind,
    compute_trajectory_checksum,
)
from aworld.dataset.trajectory_io import (
    SCHEMA_VERSION,
    TrajectoryChecksumMismatchError,
    TrajectoryEnvelope,
    TrajectoryFormat,
    TrajectoryIOError,
    TrajectoryJsonlSink,
    TrajectoryRevisionConflictError,
    TrajectorySinkConfig,
    compute_record_checksum,
    looks_like_trajectory_log,
    read_trajectory_records,
)
from aworld.evaluations.sources import (
    extract_aworld_trajectory_record,
    iter_aworld_trajectory_records,
)


CREATED_AT = datetime(2026, 8, 31, 3, 0, tzinfo=timezone.utc)


def _step(answer: str = "answer") -> dict:
    return {
        "id": f"step-{answer}",
        "meta": {"step": 1, "agent_id": "agent"},
        "state": {"input": {"content": "question"}, "messages": []},
        "action": {"content": answer, "tool_calls": [], "is_agent_finished": True},
        "reward": {"tool_outputs": [], "status": None, "score": None},
    }


def _build_result(
    trajectory: list[dict] | None = None,
    *,
    task_id: str = "task-1",
    status: TrajectoryBuildStatus = TrajectoryBuildStatus.COMPLETE,
    fidelity: TrajectoryFidelity = TrajectoryFidelity.COMPLETE,
    reason_code: TrajectoryReasonCode | None = None,
    trajectory_ref: str | None = None,
) -> TrajectoryBuildResult:
    trajectory = [_step()] if trajectory is None and status is TrajectoryBuildStatus.COMPLETE else trajectory
    count = len(trajectory or [])
    return TrajectoryBuildResult(
        task_id=task_id,
        session_id="session-1",
        trace_id="trace-1",
        task_epoch=0,
        status=status,
        fidelity=fidelity,
        reason_code=reason_code,
        source_kind=TrajectorySourceKind.EVENT_STATE,
        source_high_watermark=count or None,
        scheduled_updates=count,
        completed_updates=count,
        failed_updates=0,
        pending_updates=0,
        source_agent_messages=count,
        llm_call_count=count,
        tool_call_count=0,
        persisted_items=count,
        trajectory_ref=trajectory_ref,
        source_checksum=None,
        trajectory_checksum=compute_trajectory_checksum(trajectory) if trajectory else None,
        builder_version="sar-v1",
        created_at=CREATED_AT,
    )


def _envelope(
    trajectory: list[dict] | None = None,
    *,
    task_id: str = "task-1",
    revision: int = 1,
) -> TrajectoryEnvelope:
    trajectory = trajectory or [_step()]
    return TrajectoryEnvelope(
        build_result=_build_result(trajectory, task_id=task_id),
        revision=revision,
        trajectory=trajectory,
        llm_calls=[{"request_id": "request-1", "request": {"messages": []}}],
    )


def _append_revision(args: tuple[str, int]) -> None:
    path, revision = args
    TrajectoryJsonlSink(
        TrajectorySinkConfig(format=TrajectoryFormat.JSONL_V2, path=path)
    ).append(_envelope(revision=revision))


def test_v2_round_trip_uses_one_json_object_with_direct_structures_and_safe_sink(tmp_path: Path) -> None:
    path = tmp_path / "trajectory.jsonl"
    sink = TrajectoryJsonlSink(
        TrajectorySinkConfig(format=TrajectoryFormat.DUAL, path=path)
    )

    assert sink.config.writes_legacy is True
    assert sink.append(_envelope()) == path

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["schema_version"] == SCHEMA_VERSION
    assert isinstance(payload["trajectory"], list)
    assert isinstance(payload["llm_calls"], list)
    assert "answer" not in payload
    checksum_payload = dict(payload)
    checksum_payload["integrity"] = dict(payload["integrity"])
    checksum_payload["integrity"].pop("record_checksum")
    assert payload["integrity"]["record_checksum"] == compute_record_checksum(checksum_payload)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    record = read_trajectory_records(path).records[0]
    assert record.task_id == "task-1"
    assert record.trajectory == [_step()]
    assert record.build_result["status"] == "complete"


def test_v2_checksum_mutation_is_a_hard_failure(tmp_path: Path) -> None:
    path = tmp_path / "trajectory.jsonl"
    payload = _envelope().to_dict()
    payload["trajectory"][0]["action"]["content"] = "tampered"
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(TrajectoryChecksumMismatchError, match="record checksum mismatch"):
        read_trajectory_records(path)


def test_v2_rejects_control_plane_and_inline_data_contradictions() -> None:
    step = _step()
    empty = _build_result(
        [],
        status=TrajectoryBuildStatus.EMPTY,
        fidelity=TrajectoryFidelity.UNAVAILABLE,
        reason_code=TrajectoryReasonCode.EXECUTION_NOT_STARTED,
    )
    with pytest.raises(TrajectoryIOError, match="empty trajectory cannot contain"):
        TrajectoryEnvelope(build_result=empty, revision=1, trajectory=[step])

    failed_with_ref = _build_result(
        [step],
        status=TrajectoryBuildStatus.FAILED,
        fidelity=TrajectoryFidelity.BUILD_FAILED,
        reason_code=TrajectoryReasonCode.TRAJECTORY_BUILD_FAILED,
        trajectory_ref="artifact://trajectory/failed",
    )
    with pytest.raises(TrajectoryIOError, match="failed trajectory cannot contain"):
        TrajectoryEnvelope(build_result=failed_with_ref, revision=1, trajectory=None)

    complete_with_ref = _build_result(
        [step], trajectory_ref="artifact://trajectory/task-1"
    )
    with pytest.raises(TrajectoryIOError, match="mutually exclusive"):
        TrajectoryEnvelope(build_result=complete_with_ref, revision=1, trajectory=[step])

    mismatched_count = replace(_build_result([step]), persisted_items=2)
    with pytest.raises(TrajectoryIOError, match="persisted_items"):
        TrajectoryEnvelope(build_result=mismatched_count, revision=1, trajectory=[step])

    partial_without_checksum = replace(
        _build_result(
            [step],
            status=TrajectoryBuildStatus.PARTIAL,
            fidelity=TrajectoryFidelity.PARTIAL,
            reason_code=TrajectoryReasonCode.SOURCE_NOT_FINALIZED,
        ),
        trajectory_checksum=None,
    )
    with pytest.raises(TrajectoryIOError, match="requires trajectory_checksum"):
        TrajectoryEnvelope(
            build_result=partial_without_checksum,
            revision=1,
            trajectory=[step],
        )


def test_v2_deserialization_reapplies_envelope_semantic_validation() -> None:
    result = _build_result(
        [],
        status=TrajectoryBuildStatus.EMPTY,
        fidelity=TrajectoryFidelity.UNAVAILABLE,
        reason_code=TrajectoryReasonCode.EXECUTION_NOT_STARTED,
    )
    payload = TrajectoryEnvelope(
        build_result=result, revision=1, trajectory=[]
    ).to_dict()
    payload["trajectory"] = [_step("contradiction")]
    integrity = dict(payload["integrity"])
    integrity.pop("record_checksum")
    payload["integrity"]["record_checksum"] = compute_record_checksum(
        {**payload, "integrity": integrity}
    )

    with pytest.raises(TrajectoryIOError, match="empty trajectory cannot contain"):
        TrajectoryEnvelope.from_dict(payload)


def test_v2_complete_record_can_reference_a_checksummed_artifact_without_inline_steps(
    tmp_path: Path,
) -> None:
    path = tmp_path / "trajectory.jsonl"
    result = _build_result([_step()], trajectory_ref="artifact://trajectory/task-1")
    envelope = TrajectoryEnvelope(build_result=result, revision=1, trajectory=None)
    TrajectoryJsonlSink(
        TrajectorySinkConfig(format=TrajectoryFormat.JSONL_V2, path=path)
    ).append(envelope)

    record = read_trajectory_records(path).records[0]
    assert record.trajectory is None
    assert record.trajectory_ref == "artifact://trajectory/task-1"
    assert record.trajectory_checksum == result.trajectory_checksum


@pytest.mark.parametrize(
    ("status", "fidelity", "reason"),
    [
        (
            TrajectoryBuildStatus.EMPTY,
            TrajectoryFidelity.UNAVAILABLE,
            TrajectoryReasonCode.EXECUTION_NOT_STARTED,
        ),
        (
            TrajectoryBuildStatus.PARTIAL,
            TrajectoryFidelity.PARTIAL,
            TrajectoryReasonCode.TRAJECTORY_UPDATE_TIMEOUT,
        ),
        (
            TrajectoryBuildStatus.FAILED,
            TrajectoryFidelity.BUILD_FAILED,
            TrajectoryReasonCode.TRAJECTORY_BUILD_FAILED,
        ),
    ],
)
def test_v2_represents_non_complete_builds_without_synthetic_steps(
    tmp_path: Path,
    status: TrajectoryBuildStatus,
    fidelity: TrajectoryFidelity,
    reason: TrajectoryReasonCode,
) -> None:
    path = tmp_path / f"{status.value}.jsonl"
    result = _build_result(
        [], status=status, fidelity=fidelity, reason_code=reason
    )
    envelope = TrajectoryEnvelope(build_result=result, revision=1, trajectory=[])
    TrajectoryJsonlSink(
        TrajectorySinkConfig(format=TrajectoryFormat.JSONL_V2, path=path)
    ).append(envelope)

    record = read_trajectory_records(path).records[0]
    assert record.trajectory == []
    assert record.build_result["status"] == status.value
    assert record.build_result["reason_code"] == reason.value


def test_reader_handles_real_loguru_header_python_repr_and_nested_json(tmp_path: Path) -> None:
    path = tmp_path / "trajectory.log"
    legacy = {
        "task_id": "legacy-task",
        "is_sub_task": False,
        "trajectory": json.dumps([_step("legacy")]),
        "token_id_trajectory": json.dumps({"agent": [1, 2]}),
        "llm_calls": json.dumps([{"request_id": "legacy-request"}]),
    }
    path.write_text(
        " | 2026-08-31 10:00:00.000 | INFO | trajectory PID: 1 | module:1 -\n"
        + repr(legacy)
        + " \x1b[0m\n",
        encoding="utf-8",
    )

    result = read_trajectory_records(path)
    assert len(result.records) == 1
    assert result.records[0].schema_version == "legacy"
    assert result.records[0].fidelity == "legacy"
    assert result.records[0].revision == 0
    assert result.records[0].llm_calls == [{"request_id": "legacy-request"}]
    assert result.records[0].token_id_trajectory == {"agent": [1, 2]}
    assert result.records[0].build_result["status"] == "complete"
    assert any(item.code == "ignored_header" for item in result.diagnostics)


def test_legacy_reader_preserves_embedded_partial_build_metadata(tmp_path: Path) -> None:
    path = tmp_path / "trajectory.log"
    trajectory = [_step("partial")]
    result = _build_result(
        trajectory,
        task_id="legacy-partial",
        status=TrajectoryBuildStatus.PARTIAL,
        fidelity=TrajectoryFidelity.PARTIAL,
        reason_code=TrajectoryReasonCode.SOURCE_NOT_FINALIZED,
    )
    path.write_text(
        repr(
            {
                "task_id": "legacy-partial",
                "is_sub_task": True,
                "trajectory": json.dumps(trajectory),
                "llm_calls": json.dumps([]),
                "trajectory_build_result": result.to_dict(),
            }
        )
        + "\n",
        encoding="utf-8",
    )

    record = read_trajectory_records(path).records[0]
    assert record.schema_version == "legacy"
    assert record.fidelity == TrajectoryFidelity.LEGACY.value
    assert record.build_result["status"] == TrajectoryBuildStatus.PARTIAL.value
    assert record.build_result["fidelity"] == TrajectoryFidelity.LEGACY.value
    assert record.build_result["source_kind"] == "legacy_log"
    assert record.build_result["source_build_fidelity"] == TrajectoryFidelity.PARTIAL.value
    assert record.build_result["source_build_kind"] == TrajectorySourceKind.EVENT_STATE.value
    assert record.build_result["reason_code"] == TrajectoryReasonCode.SOURCE_NOT_FINALIZED.value
    assert record.build_result["persisted_items"] == 1
    assert record.trajectory_checksum == result.trajectory_checksum
    assert record.is_sub_task is True


@pytest.mark.parametrize(
    ("status", "fidelity", "reason"),
    [
        (
            TrajectoryBuildStatus.EMPTY,
            TrajectoryFidelity.UNAVAILABLE,
            TrajectoryReasonCode.EXECUTION_NOT_STARTED,
        ),
        (
            TrajectoryBuildStatus.FAILED,
            TrajectoryFidelity.BUILD_FAILED,
            TrajectoryReasonCode.TRAJECTORY_BUILD_FAILED,
        ),
    ],
)
def test_legacy_reader_normalizes_embedded_empty_and_failed_without_synthetic_steps(
    tmp_path: Path,
    status: TrajectoryBuildStatus,
    fidelity: TrajectoryFidelity,
    reason: TrajectoryReasonCode,
) -> None:
    path = tmp_path / f"legacy-{status.value}.log"
    result = _build_result(
        [],
        task_id=f"legacy-{status.value}",
        status=status,
        fidelity=fidelity,
        reason_code=reason,
    )
    path.write_text(
        repr(
            {
                "task_id": result.task_id,
                "trajectory": json.dumps([]),
                "llm_calls": json.dumps([]),
                "trajectory_build_result": result.to_dict(),
            }
        )
        + "\n",
        encoding="utf-8",
    )

    record = read_trajectory_records(path).records[0]
    assert record.fidelity == TrajectoryFidelity.LEGACY.value
    assert record.trajectory == []
    assert record.build_result["status"] == status.value
    assert record.build_result["reason_code"] == reason.value
    assert record.build_result["fidelity"] == TrajectoryFidelity.LEGACY.value
    assert record.build_result["source_kind"] == "legacy_log"
    assert record.build_result["source_build_fidelity"] == fidelity.value
    assert record.build_result["source_build_kind"] == TrajectorySourceKind.EVENT_STATE.value
    normalized = list(iter_aworld_trajectory_records(path))[0][1]
    assert normalized["steps"] == []
    assert normalized["final_answer"] is None


def test_legacy_embedded_checksum_mutation_is_a_hard_failure(tmp_path: Path) -> None:
    path = tmp_path / "trajectory.log"
    result = _build_result([_step("original")], task_id="legacy-checksum")
    path.write_text(
        repr(
            {
                "task_id": result.task_id,
                "trajectory": json.dumps([_step("mutated")]),
                "llm_calls": json.dumps([]),
                "trajectory_build_result": result.to_dict(),
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(TrajectoryChecksumMismatchError, match="legacy inline trajectory"):
        read_trajectory_records(path)


def test_old_legacy_empty_record_remains_compatible(tmp_path: Path) -> None:
    path = tmp_path / "trajectory.log"
    path.write_text(
        repr({"task_id": "old-empty", "trajectory": json.dumps([])}) + "\n",
        encoding="utf-8",
    )

    record = read_trajectory_records(path).records[0]
    assert record.trajectory == []
    assert record.fidelity == TrajectoryFidelity.LEGACY.value
    assert record.build_result == {
        "status": "empty",
        "fidelity": "legacy",
        "source_kind": "legacy_log",
        "persisted_items": 0,
        "trajectory_checksum": None,
    }


def test_mixed_reader_prefers_highest_v2_revision_and_latest_legacy(tmp_path: Path) -> None:
    legacy_path = tmp_path / "trajectory.log"
    legacy_path.write_text(
        "\n".join(
            [
                repr({"task_id": "legacy-only", "trajectory": json.dumps([_step("old")])}),
                repr({"task_id": "mixed", "trajectory": json.dumps([_step("legacy")])}),
                repr({"task_id": "legacy-only", "trajectory": json.dumps([_step("new")])}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    v2_path = tmp_path / "trajectory.jsonl"
    v2_path.write_bytes(
        _envelope([_step("v1")], task_id="mixed", revision=1).to_json_line()
        + _envelope([_step("v2")], task_id="mixed", revision=2).to_json_line()
        + _envelope([_step("v2")], task_id="mixed", revision=2).to_json_line()
    )

    records = {record.task_id: record for record in read_trajectory_records(legacy_path).records}
    assert records["legacy-only"].trajectory == [_step("new")]
    assert records["mixed"].schema_version == SCHEMA_VERSION
    assert records["mixed"].revision == 2
    assert records["mixed"].trajectory == [_step("v2")]
    assert records["mixed"].source == str(v2_path)


def test_same_v2_revision_with_different_valid_records_is_a_conflict(tmp_path: Path) -> None:
    path = tmp_path / "trajectory.jsonl"
    path.write_bytes(
        _envelope([_step("first")], revision=1).to_json_line()
        + _envelope([_step("second")], revision=1).to_json_line()
    )

    with pytest.raises(TrajectoryRevisionConflictError, match="conflicting"):
        read_trajectory_records(path)


def test_reader_discovers_zipped_loguru_rotation(tmp_path: Path) -> None:
    rotated = tmp_path / "trajectory.2026-08-30.log.zip"
    legacy = repr(
        {"task_id": "zip-task", "trajectory": json.dumps([_step("from zip")])}
    )
    with zipfile.ZipFile(rotated, "w") as archive:
        archive.writestr(
            "trajectory.2026-08-30.log",
            " | formatter header -\n" + legacy + "\n",
        )

    records = read_trajectory_records(tmp_path / "trajectory.log").records
    assert len(records) == 1
    assert records[0].task_id == "zip-task"
    assert "!trajectory.2026-08-30.log" in records[0].source


def test_sink_is_thread_safe_and_every_physical_line_is_complete_json(tmp_path: Path) -> None:
    path = tmp_path / "trajectory.jsonl"

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda revision: _append_revision((str(path), revision)), range(1, 33)))

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 32
    assert {json.loads(line)["revision"] for line in lines} == set(range(1, 33))


@pytest.mark.skipif(os.name != "posix", reason="cross-process flock is POSIX-specific")
def test_sink_uses_cross_process_append_lock(tmp_path: Path) -> None:
    path = tmp_path / "trajectory.jsonl"
    process_context = multiprocessing.get_context("fork")
    with process_context.Pool(processes=4) as pool:
        pool.map(_append_revision, [(str(path), revision) for revision in range(1, 25)])

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 24
    assert {json.loads(line)["revision"] for line in lines} == set(range(1, 25))


def test_evaluation_source_rejects_file_without_any_valid_record(tmp_path: Path) -> None:
    path = tmp_path / "trajectory.log"
    path.write_text(
        " | formatter header -\n{'task_id': 'broken', 'trajectory': not-json}\n",
        encoding="utf-8",
    )

    result = read_trajectory_records(path)
    assert not result.records
    assert {item.code for item in result.diagnostics} == {"ignored_header", "malformed_record"}
    with pytest.raises(ValueError, match="no valid AWorld trajectory records"):
        list(iter_aworld_trajectory_records(path))


def test_reader_bounds_oversized_physical_records_before_parsing(tmp_path: Path) -> None:
    path = tmp_path / "trajectory.log"
    path.write_text(
        repr({"task_id": "large", "trajectory": "x" * 1000}) + "\n",
        encoding="utf-8",
    )

    result = read_trajectory_records(path, max_record_bytes=128)
    assert not result.records
    assert [item.code for item in result.diagnostics] == ["record_too_large"]


def test_evaluation_reader_and_cli_detection_keep_legacy_and_v2_compatible(tmp_path: Path) -> None:
    from aworld_cli.evaluator_runtime import _looks_like_aworld_trajectory_log

    path = tmp_path / "trajectory.log"
    path.write_text(
        " | real formatter header -\n"
        + repr({"task_id": "task-1", "trajectory": json.dumps([_step("legacy")])})
        + "\n",
        encoding="utf-8",
    )
    assert looks_like_trajectory_log(path) is True
    assert _looks_like_aworld_trajectory_log(path) is True
    assert list(iter_aworld_trajectory_records(path))[0][0] == "task-1"
    assert extract_aworld_trajectory_record(path, "task-1")["final_answer"] == "legacy"

    v2_path = tmp_path / "v2.jsonl"
    v2_path.write_bytes(_envelope().to_json_line())
    assert looks_like_trajectory_log(v2_path) is True
    assert _looks_like_aworld_trajectory_log(v2_path) is True
    assert extract_aworld_trajectory_record(v2_path, "task-1")["trajectory_record"][
        "schema_version"
    ] == SCHEMA_VERSION


def test_dataset_package_keeps_established_lazy_public_api() -> None:
    import aworld.dataset as dataset

    assert dataset.TrajectoryDataset.__name__ == "TrajectoryDataset"
    assert dataset.DefaultTrajectoryStrategy.__name__ == "DefaultTrajectoryStrategy"
