"""Typed, content-free control-plane records for direct CLI runs.

The direct-run summary is the trajectory data plane and may contain prompts,
model output, and tool results.  The records in this module intentionally keep
only stable status, counters, and checkpoint identifiers so callers can make
process/trajectory decisions without parsing user-visible text.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any


class DirectRunStatus(str, Enum):
    """Semantic result of one direct CLI invocation."""

    SUCCEEDED = "succeeded"
    TASK_FAILED = "task_failed"
    INFRASTRUCTURE_FAILED = "infrastructure_failed"
    CANCELLED = "cancelled"


class DirectRunStage(str, Enum):
    """Stable stage taxonomy for direct-run failures."""

    AGENT_LOAD = "agent_load"
    EXECUTOR_CREATE = "executor_create"
    PROVIDER_START = "provider_start"
    AGENT_EXECUTION = "agent_execution"
    ORCHESTRATION = "orchestration"


class DirectRunErrorCode(str, Enum):
    """Framework-owned error codes emitted by the direct CLI."""

    AGENT_LOAD_FAILED = "agent_load_failed"
    AGENT_NOT_FOUND = "agent_not_found"
    EXECUTOR_CREATION_FAILED = "executor_creation_failed"
    PROVIDER_CALL_NOT_CAPTURED = "provider_call_not_captured"
    AGENT_TASK_FAILED = "agent_task_failed"
    AGENT_EXECUTION_INFRASTRUCTURE_FAILED = "agent_execution_infrastructure_failed"
    AGENT_EXECUTION_UNTYPED_FAILURE = "agent_execution_untyped_failure"
    DIRECT_RUN_EXCEPTION = "direct_run_exception"
    DIRECT_RUN_CANCELLED = "direct_run_cancelled"
    DIRECT_RUN_INTERRUPTED = "direct_run_interrupted"
    ATIF_EXPORT_FAILED = "atif_export_failed"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _serialize_control_record(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            payload = to_dict()
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}
    return {}


def _native_trajectory(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    trajectory = result.get("trajectory")
    if not isinstance(trajectory, list):
        return []
    return [item for item in trajectory if isinstance(item, dict)]


def _agent_action_count(trajectory: list[dict[str, Any]]) -> int:
    count = 0
    for item in trajectory:
        action = item.get("action")
        if not isinstance(action, dict):
            continue
        if action.get("content") is not None or action.get("tool_calls") is not None:
            count += 1
    return count


def _tool_call_count(trajectory: list[dict[str, Any]]) -> int:
    count = 0
    for item in trajectory:
        action = _as_dict(item.get("action"))
        calls = action.get("tool_calls")
        if isinstance(calls, list):
            count += sum(1 for call in calls if isinstance(call, dict))
    return count


def _result_counts(result: Mapping[str, Any]) -> tuple[int, int, int]:
    trajectory = _native_trajectory(result)
    build = _serialize_control_record(result.get("trajectory_build_result"))

    llm_count = _as_nonnegative_int(build.get("llm_call_count"))
    if llm_count is None:
        llm_calls = result.get("llm_calls")
        if isinstance(llm_calls, list):
            llm_count = len(llm_calls)
        elif str(result.get("trajectory_capture_mode") or "") == "task_response":
            llm_count = _agent_action_count(trajectory)
        else:
            llm_count = 0

    tool_count = _as_nonnegative_int(build.get("tool_call_count"))
    if tool_count is None:
        tool_count = _tool_call_count(trajectory)

    action_count = _as_nonnegative_int(build.get("source_agent_messages"))
    if action_count is None:
        action_count = _agent_action_count(trajectory)

    return llm_count, tool_count, action_count


def _checkpoint_for_result(
    result: Mapping[str, Any],
    *,
    result_index: int,
) -> dict[str, Any] | None:
    build = _serialize_control_record(result.get("trajectory_build_result"))
    completed_updates = _as_nonnegative_int(build.get("completed_updates")) or 0
    persisted_items = _as_nonnegative_int(build.get("persisted_items")) or 0
    if build and (completed_updates > 0 or persisted_items > 0):
        checkpoint = {
            "kind": "trajectory_build",
            "result_iteration": _as_nonnegative_int(result.get("iteration"))
            or result_index,
            "task_id": build.get("task_id"),
            "session_id": build.get("session_id"),
            "source_high_watermark": build.get("source_high_watermark"),
            "completed_updates": completed_updates,
            "persisted_items": persisted_items,
            "trajectory_checksum": build.get("trajectory_checksum"),
        }
        return {key: value for key, value in checkpoint.items() if value is not None}

    # Without a builder receipt, only a successful result proves that its last
    # captured step is a successful checkpoint.  A failed result's last step is
    # evidence, but must not be mislabeled as successful progress.
    if not bool(result.get("success")):
        return None
    trajectory = _native_trajectory(result)
    for item in reversed(trajectory):
        action = item.get("action")
        if not isinstance(action, dict):
            continue
        meta = _as_dict(item.get("meta"))
        checkpoint = {
            "kind": "captured_trajectory_step",
            "result_iteration": _as_nonnegative_int(result.get("iteration"))
            or result_index,
            "trajectory_step": _as_nonnegative_int(meta.get("step")),
            "task_id": meta.get("task_id"),
            "session_id": meta.get("session_id"),
        }
        return {key: value for key, value in checkpoint.items() if value is not None}
    return None


def _summary_metrics(summary: dict[str, Any] | None) -> dict[str, Any]:
    llm_call_count = 0
    tool_call_count = 0
    action_count = 0
    trajectory_item_count = 0
    last_successful_checkpoint = None
    fidelities: list[str] = []

    if isinstance(summary, dict):
        for index, result in enumerate(summary.get("results") or [], start=1):
            if not isinstance(result, dict):
                continue
            llm_count, tool_count, result_action_count = _result_counts(result)
            llm_call_count += llm_count
            tool_call_count += tool_count
            action_count += result_action_count
            trajectory_item_count += len(_native_trajectory(result))
            checkpoint = _checkpoint_for_result(result, result_index=index)
            if checkpoint is not None:
                last_successful_checkpoint = checkpoint
            build = _serialize_control_record(result.get("trajectory_build_result"))
            fidelity = build.get("fidelity") or result.get("trajectory_fidelity")
            if fidelity:
                fidelities.append(str(fidelity))

    return {
        "llm_call_count": llm_call_count,
        "tool_call_count": tool_call_count,
        "action_count": action_count,
        "trajectory_item_count": trajectory_item_count,
        "last_successful_checkpoint": last_successful_checkpoint,
        "fidelities": fidelities,
    }


def _derive_fidelity(
    summary: dict[str, Any] | None,
    *,
    status: DirectRunStatus,
    metrics: Mapping[str, Any],
) -> str:
    has_evidence = bool(
        metrics.get("llm_call_count")
        or metrics.get("tool_call_count")
        or metrics.get("action_count")
        or metrics.get("trajectory_item_count")
        or metrics.get("last_successful_checkpoint")
    )
    fidelities = set(metrics.get("fidelities") or [])
    if status is not DirectRunStatus.SUCCEEDED:
        return "partial" if has_evidence else "unavailable"
    if "partial" in fidelities or "placeholder" in fidelities:
        return "partial"
    if "build_failed" in fidelities or "unavailable" in fidelities:
        return "partial" if has_evidence else "unavailable"
    if "complete" in fidelities or has_evidence:
        return "complete"
    # A successful legacy executor may return a text-only summary.  It is a
    # valid run outcome, but its trajectory projection remains unavailable.
    return "unavailable"


@dataclass(frozen=True)
class DirectRunOutcome(Mapping[str, Any]):
    """Typed outcome plus the original summary for compatibility consumers."""

    status: DirectRunStatus
    summary: dict[str, Any] | None
    process_exit_code: int
    trajectory_fidelity: str
    llm_call_count: int
    tool_call_count: int
    action_count: int
    last_successful_checkpoint: dict[str, Any] | None = None
    failure_record: dict[str, Any] | None = None

    SCHEMA_VERSION = "aworld.run.outcome.v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", DirectRunStatus(self.status))
        if self.process_exit_code < 0:
            raise ValueError("process_exit_code must be non-negative")
        for field_name in ("llm_call_count", "tool_call_count", "action_count"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")

    @property
    def succeeded(self) -> bool:
        return self.status is DirectRunStatus.SUCCEEDED

    @classmethod
    def from_summary(
        cls,
        summary: dict[str, Any] | None,
        *,
        status: DirectRunStatus | str,
        failure_record: Mapping[str, Any] | None = None,
        process_exit_code: int | None = None,
    ) -> "DirectRunOutcome":
        normalized_status = DirectRunStatus(status)
        metrics = _summary_metrics(summary)
        return cls(
            status=normalized_status,
            summary=summary,
            process_exit_code=(
                process_exit_code
                if process_exit_code is not None
                else (0 if normalized_status is DirectRunStatus.SUCCEEDED else 1)
            ),
            trajectory_fidelity=_derive_fidelity(
                summary,
                status=normalized_status,
                metrics=metrics,
            ),
            llm_call_count=metrics["llm_call_count"],
            tool_call_count=metrics["tool_call_count"],
            action_count=metrics["action_count"],
            last_successful_checkpoint=metrics["last_successful_checkpoint"],
            failure_record=dict(failure_record) if failure_record is not None else None,
        )

    def to_dict(self, *, atif_export: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.SCHEMA_VERSION,
            "semantic_status": self.status.value,
            "process_exit_code": self.process_exit_code,
            "trajectory_fidelity": self.trajectory_fidelity,
            "llm_call_count": self.llm_call_count,
            "tool_call_count": self.tool_call_count,
            "action_count": self.action_count,
            "last_successful_checkpoint": self.last_successful_checkpoint,
        }
        if self.failure_record:
            payload["failure"] = {
                key: self.failure_record[key]
                for key in ("stage", "error_code")
                if self.failure_record.get(key) is not None
            }
        if atif_export is not None:
            payload["atif_export"] = dict(atif_export)
        return payload

    # Preserve the private helper's historical dict-like summary behavior for
    # resume integrations and tests while making status handling explicit.
    def __getitem__(self, key: str) -> Any:
        if self.summary is None:
            raise KeyError(key)
        return self.summary[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.summary or {})

    def __len__(self) -> int:
        return len(self.summary or {})

    def __bool__(self) -> bool:
        """Preserve the legacy helper's success/failure truth-value contract."""

        return self.succeeded


def coerce_direct_run_outcome(value: Any) -> DirectRunOutcome:
    """Normalize legacy private-helper return values at the CLI boundary."""

    if isinstance(value, DirectRunOutcome):
        return value
    if isinstance(value, dict):
        # Before the typed contract, any summary dict meant the command reached
        # its normal return path; retain that behavior for plugin monkeypatches.
        return DirectRunOutcome.from_summary(value, status=DirectRunStatus.SUCCEEDED)
    return DirectRunOutcome.from_summary(
        None,
        status=DirectRunStatus.INFRASTRUCTURE_FAILED,
        failure_record={
            "stage": DirectRunStage.ORCHESTRATION.value,
            "error_code": DirectRunErrorCode.DIRECT_RUN_EXCEPTION.value,
        },
    )


__all__ = [
    "DirectRunErrorCode",
    "DirectRunOutcome",
    "DirectRunStage",
    "DirectRunStatus",
    "coerce_direct_run_outcome",
]
