"""Export AWorld direct-run summaries as ATIF trajectories."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from itertools import islice
from pathlib import Path
from typing import Any


_THINK_BLOCK_RE = re.compile(r"<think>\s*(.*?)\s*</think>", re.DOTALL)
_SAFE_ERROR_CODE_RE = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_MAX_ATIF_TEXT_CHARS = 12_000
_MAX_ATIF_COLLECTION_ITEMS = 128
_MAX_DURABLE_TOOL_JOURNAL_BYTES = 256 * 1024 * 1024
_TOOL_OBSERVATION_SCHEMA_VERSION = "aworld.atif.tool-observation.v1"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _execution_protocol_telemetry(value: Any) -> dict[str, Any] | None:
    """Use the core allowlist before telemetry enters any export surface."""
    from aworld.runners.execution_protocol import (
        project_execution_protocol_telemetry,
    )

    return project_execution_protocol_telemetry(value)


def _bounded_redacted_text(
    value: Any,
    *,
    max_chars: int = _MAX_ATIF_TEXT_CHARS,
) -> tuple[str, dict[str, Any]]:
    from aworld.secret_detection import redact_sensitive_literals

    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        except (TypeError, ValueError):
            text = str(value or "")
    redacted = redact_sensitive_literals(text)
    original_chars = len(redacted)
    content_hash = "sha256:" + hashlib.sha256(redacted.encode("utf-8")).hexdigest()
    if original_chars <= max_chars:
        return redacted, {
            "content_chars": original_chars,
            "content_hash": content_hash,
            "truncated": False,
        }
    marker = "\n…<bounded ATIF tool observation>…\n"
    available = max(0, max_chars - len(marker))
    head = available * 3 // 4
    tail = available - head
    bounded = redacted[:head] + marker + (redacted[-tail:] if tail else "")
    return bounded, {
        "content_chars": original_chars,
        "content_hash": content_hash,
        "truncated": True,
        "omitted_chars": max(0, original_chars - head - tail),
    }


def _safe_projection(value: Any, *, depth: int = 0) -> Any:
    """Return a bounded JSON value with concrete credential literals removed."""

    if depth >= 5:
        if isinstance(value, Mapping):
            return {"bounded_nested_mapping": True}
        if isinstance(value, (list, tuple)):
            return ["<bounded nested collection>"]
        text, _ = _bounded_redacted_text(value, max_chars=512)
        return text
    if isinstance(value, Mapping):
        projected: dict[str, Any] = {}
        for raw_key, item in islice(value.items(), _MAX_ATIF_COLLECTION_ITEMS):
            key = str(raw_key)
            if any(
                token in key.casefold()
                for token in ("secret", "token", "password", "api_key", "apikey")
            ):
                projected[key] = "<REDACTED_SECRET>"
            else:
                projected[key] = _safe_projection(item, depth=depth + 1)
        return projected
    if isinstance(value, (list, tuple)):
        return [
            _safe_projection(item, depth=depth + 1)
            for item in value[:_MAX_ATIF_COLLECTION_ITEMS]
        ]
    if isinstance(value, str):
        return _bounded_redacted_text(value)[0]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _bounded_redacted_text(value, max_chars=512)[0]


def _parse_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return _safe_projection(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"raw": _safe_projection(value)}
        return (
            _safe_projection(parsed)
            if isinstance(parsed, dict)
            else {"value": _safe_projection(parsed)}
        )
    return {"value": _safe_projection(value)} if value is not None else {}


def _iso_timestamp(value: Any) -> str | None:
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _split_message_and_reasoning(
    content: Any,
    *,
    response_kind: str | None = None,
    has_tool_calls: bool = False,
) -> tuple[str, str | None]:
    text = content if isinstance(content, str) else str(content or "")
    reasoning_parts = _THINK_BLOCK_RE.findall(text)
    message = _THINK_BLOCK_RE.sub("", text).strip()
    reasoning = "\n\n".join(part.strip() for part in reasoning_parts if part.strip())
    if not message:
        if response_kind == "reasoning_only_retry":
            message = "(reasoning-only response; framework retry followed)"
        elif response_kind == "empty_response_retry":
            message = "(empty model response; framework retry followed)"
        elif has_tool_calls:
            message = "(tool call only)"
        elif reasoning_parts:
            message = "(reasoning-only response)"
        else:
            message = "(empty model response)"
    return message, reasoning or None


def _tool_result_index(
    native_items: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for item in native_items:
        state_input = _as_dict(_as_dict(item.get("state")).get("input"))
        for result in state_input.get("action_result") or []:
            if not isinstance(result, dict):
                continue
            call_id = result.get("tool_call_id")
            if call_id:
                content, _ = _bounded_redacted_text(
                    result.get("content")
                    if isinstance(result.get("content"), str)
                    else _safe_projection(result.get("content"))
                )
                results[str(call_id)] = {"content": content}
    return results


def _known_trajectory_scope(
    trajectory_payload: dict[str, Any], native_items: list[dict[str, Any]]
) -> tuple[set[str], set[str], set[int], set[str]]:
    task_ids = {
        str(task_id)
        for item in native_items
        if (task_id := _as_dict(item.get("meta")).get("task_id")) is not None
    }
    task_ids.update(
        str(task_id)
        for call in trajectory_payload.get("llm_calls") or []
        if isinstance(call, dict) and (task_id := call.get("task_id")) is not None
    )
    session_ids = {
        str(session_id)
        for item in native_items
        if (session_id := _as_dict(item.get("meta")).get("session_id")) is not None
    }
    task_epochs = {
        task_epoch
        for item in native_items
        if isinstance(
            (task_epoch := _as_dict(item.get("meta")).get("task_epoch")), int
        )
        and not isinstance(task_epoch, bool)
        and task_epoch >= 0
    }
    task_epochs.update(
        task_epoch
        for call in trajectory_payload.get("llm_calls") or []
        if isinstance(call, dict)
        and isinstance(
            (task_epoch := _as_dict(call.get("turn_economics")).get("task_epoch")),
            int,
        )
        and not isinstance(task_epoch, bool)
        and task_epoch >= 0
    )
    call_ids = {
        str(call_id)
        for item in native_items
        for raw_call in _as_dict(item.get("action")).get("tool_calls") or []
        if isinstance(raw_call, dict)
        and (call_id := raw_call.get("id")) is not None
    }
    return task_ids, session_ids, task_epochs, call_ids


def _safe_int(value: Any) -> int | None:
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else None
    )


def _safe_error_code(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if _SAFE_ERROR_CODE_RE.fullmatch(text) else None


def _project_terminal_execution_receipt(value: Any) -> dict[str, Any] | None:
    receipt = _as_dict(value)
    if receipt.get("schema_version") not in {
        "aworld.terminal-execution-receipt/v1",
        "aworld.terminal-execution-receipt/v2",
    }:
        return None
    projected: dict[str, Any] = {
        "schema_version": receipt["schema_version"],
    }
    for key in ("effective_language", "effect"):
        candidate = _safe_error_code(receipt.get(key))
        if candidate is not None:
            projected[key] = candidate
    for key in ("executed", "timed_out"):
        candidate = receipt.get(key)
        if isinstance(candidate, bool):
            projected[key] = candidate
    exit_code = receipt.get("exit_code")
    if (
        isinstance(exit_code, int)
        and not isinstance(exit_code, bool)
        and -1_000_000 <= exit_code <= 1_000_000
    ):
        projected["exit_code"] = exit_code
    generation_delta = _safe_int(receipt.get("workspace_generation_delta"))
    if generation_delta is not None:
        projected["workspace_generation_delta"] = generation_delta
    for key in ("read_paths", "write_paths"):
        paths = receipt.get(key)
        if not isinstance(paths, (list, tuple)):
            continue
        projected[key] = [
            _bounded_redacted_text(path, max_chars=512)[0]
            for path in paths[:32]
            if isinstance(path, str)
        ]
        if len(paths) > 32:
            projected[f"{key}_omitted_count"] = len(paths) - 32
    return projected


def _project_tool_observation_result(
    result: dict[str, Any],
    *,
    call_id: str,
    source: str,
) -> dict[str, Any]:
    raw_content = result.get("content")
    content, content_projection = _bounded_redacted_text(
        raw_content if isinstance(raw_content, str) else _safe_projection(raw_content)
    )
    metadata = _as_dict(result.get("metadata"))
    sandbox = _as_dict(metadata.get("sandbox_observation"))
    output_policy = _as_dict(metadata.get("tool_output_policy"))
    interception = _as_dict(sandbox.get("hook_interception"))
    terminal_execution = _project_terminal_execution_receipt(
        sandbox.get("terminal_execution_receipt")
        or metadata.get("terminal_execution_receipt")
    )
    explicit_success = result.get("success")
    error_code = _safe_error_code(result.get("error"))
    if interception:
        status = "intercepted"
    elif explicit_success is False or error_code is not None:
        status = "failed"
    else:
        status = "completed"
    output_kind = (
        "offloaded"
        if output_policy.get("artifact_ref")
        or (_safe_int(output_policy.get("offloaded_tokens")) or 0) > 0
        else "inline"
    )
    output: dict[str, Any] = {"kind": output_kind}
    for source_key, target_key in (
        ("reason_code", "reason_code"),
        ("raw_byte_count", "raw_byte_count"),
        ("raw_checksum", "content_hash"),
        ("inline_tokens", "inline_tokens"),
        ("offloaded_tokens", "offloaded_tokens"),
    ):
        value = output_policy.get(source_key)
        if source_key.endswith("count") or source_key.endswith("tokens"):
            value = _safe_int(value)
        elif source_key == "reason_code":
            value = _safe_error_code(value)
        elif source_key == "raw_checksum":
            value = value if isinstance(value, str) and len(value) <= 128 else None
        if value is not None:
            output[target_key] = value
    extra: dict[str, Any] = {
        "schema_version": _TOOL_OBSERVATION_SCHEMA_VERSION,
        "capture_source": source,
        "status": status,
        "content": content_projection,
        "output": output,
        "cache_replay": sandbox.get("cache_hit") is True,
    }
    if isinstance(explicit_success, bool):
        extra["success"] = explicit_success
    if error_code is not None:
        extra["error_code"] = error_code
    sandbox_projection = {
        key: value
        for key, value in {
            "effect": (
                str(sandbox.get("effect"))[:64]
                if sandbox.get("effect") is not None
                else None
            ),
            "changed": (
                sandbox.get("changed")
                if isinstance(sandbox.get("changed"), bool)
                else None
            ),
            "workspace_mutated": (
                sandbox.get("workspace_mutated")
                if isinstance(sandbox.get("workspace_mutated"), bool)
                else None
            ),
            "workspace_generation": _safe_int(sandbox.get("workspace_generation")),
            "observation_id": (
                sandbox.get("observation_id")
                if isinstance(sandbox.get("observation_id"), str)
                and len(sandbox["observation_id"]) <= 128
                else None
            ),
        }.items()
        if value is not None
    }
    if sandbox_projection:
        extra["sandbox"] = sandbox_projection
    if terminal_execution is not None:
        extra["terminal_execution"] = terminal_execution
    if interception:
        extra["interception"] = {
            key: value
            for key, value in {
                "kind": _safe_error_code(interception.get("kind")),
                "error_code": _safe_error_code(interception.get("error_code")),
                "content_type": _safe_error_code(interception.get("content_type")),
            }.items()
            if value is not None
        }
    return {
        "source_call_id": call_id,
        "content": content,
        "extra": extra,
    }


def _durable_tool_result_index(
    trajectory_payload: dict[str, Any], native_items: list[dict[str, Any]]
) -> tuple[dict[str, dict[str, Any]], dict[str, Any] | None]:
    """Recover completed Tool observations from the crash-tolerant journal."""

    try:
        from aworld.core.tool_action_journal import (
            configured_journal_path,
            read_tool_action_journal,
        )

        path = configured_journal_path()
    except Exception:
        path = None
    if path is None:
        return {}, None
    task_ids, session_ids, task_epochs, known_call_ids = _known_trajectory_scope(
        trajectory_payload,
        native_items,
    )
    if not task_ids or not known_call_ids:
        return {}, {
            "schema_version": "aworld.tool-action-journal.v1",
            "status": "unavailable",
            "reason_code": (
                "task_scope_unavailable" if not task_ids else "tool_call_scope_unavailable"
            ),
            "recovered_result_count": 0,
        }

    try:
        if path.stat().st_size > _MAX_DURABLE_TOOL_JOURNAL_BYTES:
            return {}, {
                "schema_version": "aworld.tool-action-journal.v1",
                "status": "unavailable",
                "reason_code": "journal_size_limit_exceeded",
                "recovered_result_count": 0,
            }
        recovery = read_tool_action_journal(path)
    except FileNotFoundError:
        recovery = read_tool_action_journal(path)
    except Exception:
        return {}, {
            "schema_version": "aworld.tool-action-journal.v1",
            "status": "unavailable",
            "reason_code": "journal_recovery_failed",
            "recovered_result_count": 0,
        }
    selected: dict[
        str,
        tuple[int, tuple[str | None, int | None], dict[str, Any]],
    ] = {}
    ambiguous_call_ids: set[str] = set()
    selected_event_count = 0
    for event in recovery.events:
        context = _as_dict(event.get("context"))
        event_task_id = context.get("task_id")
        if event_task_id is None or str(event_task_id) not in task_ids:
            continue
        event_session_id = context.get("session_id")
        if session_ids and (
            event_session_id is None or str(event_session_id) not in session_ids
        ):
            continue
        event_task_epoch = context.get("task_epoch")
        if task_epochs and (
            not isinstance(event_task_epoch, int)
            or isinstance(event_task_epoch, bool)
            or event_task_epoch not in task_epochs
        ):
            continue
        event_type = str(event.get("event_type") or "")
        results = event.get("results")
        actions = event.get("actions")
        if not isinstance(actions, list):
            continue
        priority = {
            "tool_observation_recorded": 3,
            "sandbox_call_completed": 2,
            "sandbox_call_failed": 2,
            "sandbox_transaction_resolved": 1,
        }.get(event_type, 0)
        if priority == 0 or (
            event_type != "sandbox_call_failed" and not isinstance(results, list)
        ):
            continue
        if not isinstance(results, list):
            results = []
        selected_event_count += 1
        for index, action in enumerate(actions):
            action = _as_dict(action)
            result = _as_dict(results[index] if index < len(results) else None)
            if event_type == "sandbox_call_failed" and not result:
                result = {
                    "tool_call_id": action.get("tool_call_id"),
                    "success": False,
                    "error": "sandbox_call_failed",
                    "content": "Tool execution failed before returning an observation.",
                }
            call_id = result.get("tool_call_id") or action.get("tool_call_id")
            if (
                not isinstance(call_id, str)
                or not call_id
                or call_id not in known_call_ids
                or call_id in ambiguous_call_ids
            ):
                continue
            event_scope = (
                str(event_session_id) if event_session_id is not None else None,
                (
                    event_task_epoch
                    if isinstance(event_task_epoch, int)
                    and not isinstance(event_task_epoch, bool)
                    else None
                ),
            )
            previous = selected.get(call_id)
            if previous is not None:
                scopes_conflict = previous[1] != event_scope
                if scopes_conflict:
                    selected.pop(call_id, None)
                    ambiguous_call_ids.add(call_id)
                    continue
                if previous[0] > priority:
                    continue
            projected_result = _project_tool_observation_result(
                result,
                call_id=call_id,
                source=(
                    "tool_action_journal:model_visible_observation"
                    if event_type == "tool_observation_recorded"
                    else f"tool_action_journal:{event_type}"
                ),
            )
            if event_type == "sandbox_call_failed":
                failure_type = _safe_error_code(
                    _as_dict(event.get("metadata")).get("error_type")
                )
                if failure_type is not None:
                    projected_result["extra"]["failure_type"] = failure_type
            selected[call_id] = (
                priority,
                event_scope,
                projected_result,
            )
    evidence = recovery.to_evidence()
    evidence.update(
        {
            "selected_event_count": selected_event_count,
            "recovered_result_count": len(selected),
            "ambiguous_call_id_count": len(ambiguous_call_ids),
        }
    )
    return {
        call_id: value for call_id, (_, _, value) in selected.items()
    }, evidence


def _native_agent_step(
    item: dict[str, Any],
    *,
    step_id: int,
    model_name: str | None,
    tool_results: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    meta = _as_dict(item.get("meta"))
    action = _as_dict(item.get("action"))
    raw_calls = action.get("tool_calls") or []
    response_kind = meta.get("assistant_response_kind")
    raw_content = action.get("content")
    visible_content_empty = not _THINK_BLOCK_RE.sub(
        "", raw_content if isinstance(raw_content, str) else str(raw_content or "")
    ).strip()
    message, reasoning = _split_message_and_reasoning(
        raw_content,
        response_kind=(str(response_kind) if response_kind else None),
        has_tool_calls=any(isinstance(call, dict) for call in raw_calls),
    )

    tool_calls: list[dict[str, Any]] = []
    observation_results: list[dict[str, Any]] = []
    for index, raw_call in enumerate(raw_calls, start=1):
        if not isinstance(raw_call, dict):
            continue
        function = _as_dict(raw_call.get("function"))
        call_id = str(raw_call.get("id") or f"aworld-call-{step_id}-{index}")
        tool_calls.append(
            {
                "tool_call_id": call_id,
                "function_name": str(function.get("name") or "unknown"),
                "arguments": _parse_arguments(function.get("arguments")),
            }
        )
        if call_id in tool_results:
            result = tool_results[call_id]
            observation = {
                "source_call_id": call_id,
                "content": result.get("content", ""),
            }
            if isinstance(result.get("extra"), dict):
                observation["extra"] = result["extra"]
            observation_results.append(observation)

    if response_kind is None:
        if tool_calls and visible_content_empty:
            response_kind = "tool_call_only"
        elif reasoning and visible_content_empty:
            response_kind = "reasoning_only"
        elif visible_content_empty:
            response_kind = "empty_response"

    step: dict[str, Any] = {
        "step_id": step_id,
        "source": "agent",
        "message": message,
        "llm_call_count": 1,
        "extra": {
            "aworld_step": meta.get("step"),
            "aworld_task_id": meta.get("task_id"),
            "aworld_agent_id": meta.get("agent_id"),
        },
    }
    if response_kind:
        step["extra"]["assistant_response_kind"] = str(response_kind)
    if isinstance(meta.get("llm_request_id"), str) and meta["llm_request_id"]:
        step["extra"]["aworld_llm_request_id"] = meta["llm_request_id"][:256]
    task_epoch = _safe_int(meta.get("task_epoch"))
    if task_epoch is not None:
        step["extra"]["aworld_task_epoch"] = task_epoch
    timestamp = _iso_timestamp(meta.get("execute_time"))
    if timestamp:
        step["timestamp"] = timestamp
    if model_name:
        step["model_name"] = model_name
    if reasoning:
        step["reasoning_content"] = reasoning
    if tool_calls:
        step["tool_calls"] = tool_calls
    if observation_results:
        step["observation"] = {"results": observation_results}
    return step


def _as_nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _count_tool_calls(native_items: list[dict[str, Any]]) -> int:
    count = 0
    for item in native_items:
        calls = _as_dict(item.get("action")).get("tool_calls")
        if isinstance(calls, list):
            count += sum(1 for call in calls if isinstance(call, dict))
    return count


def _run_metric(
    run_outcome: dict[str, Any],
    trajectory_payload: dict[str, Any],
    name: str,
    fallback: int,
) -> int:
    for source in (run_outcome, trajectory_payload):
        value = _as_nonnegative_int(source.get(name))
        if value is not None:
            return value
    return fallback


def _complete_usage_totals(
    trajectory_payload: dict[str, Any], llm_call_count: int
) -> dict[str, int]:
    """Export totals only when every distinct call has actual provider usage.

    Missing terminal usage is unknown, including interrupted streams. Older
    records filled missing usage with zeros, so accept legacy records only
    when their raw usage contains nonzero evidence.
    """
    calls: dict[str, dict[str, Any]] = {}
    for call in trajectory_payload.get("llm_calls") or []:
        if not isinstance(call, dict) or not call.get("request_id"):
            return {}
        request_id = str(call["request_id"])
        if request_id in calls and calls[request_id] != call:
            return {}
        calls[request_id] = call
    if not calls or len(calls) != llm_call_count:
        return {}
    from aworld.models.usage import (
        CacheUsageFidelity,
        reconcile_cache_usage_receipt,
    )

    prompt = completion = cached = 0
    cache_read_complete = True
    for call in calls.values():
        if call.get("status") not in (None, "success"):
            return {}
        raw = _as_dict(call.get("usage_raw"))
        available = call.get("usage_available")
        if available is False or (
            available is not True
            and not any(
                (_as_nonnegative_int(raw.get(key)) or 0) > 0
                for key in (
                    "prompt_tokens",
                    "input_tokens",
                    "completion_tokens",
                    "output_tokens",
                )
            )
        ):
            return {}
        input_tokens = _as_nonnegative_int(
            raw.get("prompt_tokens", raw.get("input_tokens"))
        )
        output_tokens = _as_nonnegative_int(
            raw.get("completion_tokens", raw.get("output_tokens"))
        )
        if input_tokens is None or output_tokens is None:
            return {}
        normalized = _as_dict(call.get("usage_normalized"))
        if normalized:
            input_tokens = _as_nonnegative_int(normalized.get("prompt_tokens"))
            output_tokens = _as_nonnegative_int(normalized.get("completion_tokens"))
            if input_tokens is None or output_tokens is None:
                return {}
        prompt += input_tokens
        completion += output_tokens
        cache_receipt = reconcile_cache_usage_receipt(
            captured_receipt=call.get("cache_usage_receipt"),
            raw_usage=raw,
            normalized_usage=normalized,
        )
        if cache_receipt.fidelity is CacheUsageFidelity.EXACT:
            cached += cache_receipt.cache_read_tokens or 0
        else:
            cache_read_complete = False
    totals = {
        "total_prompt_tokens": prompt,
        "total_completion_tokens": completion,
    }
    if cache_read_complete:
        totals["total_cached_tokens"] = cached
    return totals


class AtifExportStatus(str, Enum):
    PERSISTED = "persisted"
    FAILED = "failed"
    NOT_REQUESTED = "not_requested"


@dataclass(frozen=True)
class AtifExportReceipt:
    """Sanitized control-plane result for one ATIF output sink."""

    status: AtifExportStatus
    trajectory_fidelity: str
    error_code: str | None = None
    error_type: str | None = None

    SCHEMA_VERSION = "aworld.atif.export.v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", AtifExportStatus(self.status))
        if self.status is AtifExportStatus.FAILED and not self.error_code:
            raise ValueError("failed ATIF export requires error_code")
        if self.status is not AtifExportStatus.FAILED and (
            self.error_code is not None or self.error_type is not None
        ):
            raise ValueError("ATIF export errors are only valid for failed receipts")

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.SCHEMA_VERSION,
            "status": self.status.value,
            "trajectory_fidelity": self.trajectory_fidelity,
        }
        if self.error_code is not None:
            payload["error_code"] = self.error_code
        if self.error_type is not None:
            payload["error_type"] = self.error_type
        return payload


def build_atif_trajectory(
    trajectory_payload: dict[str, Any],
    *,
    prompt: str,
    agent_name: str,
    agent_version: str,
    model_name: str | None = None,
    run_outcome: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Convert AWorld's direct-run trajectory payload to ATIF v1.7."""
    normalized_outcome = _as_dict(run_outcome)
    native_items = [
        item
        for item in trajectory_payload.get("trajectory") or []
        if isinstance(item, dict)
    ]
    session_id = next(
        (
            str(_as_dict(item.get("meta")).get("session_id"))
            for item in native_items
            if _as_dict(item.get("meta")).get("session_id")
        ),
        f"aworld-{uuid.uuid4()}",
    )
    steps: list[dict[str, Any]] = [
        {
            "step_id": 1,
            "source": "user",
            "message": prompt,
        }
    ]
    tool_results = _tool_result_index(native_items)
    durable_tool_results, tool_journal_evidence = _durable_tool_result_index(
        trajectory_payload,
        native_items,
    )
    # The post-boundary journal is the authoritative durable copy of the
    # bounded result that entered model history.  It is strictly richer than a
    # legacy ``state.input.action_result`` projection and remains available
    # when a deadline interrupts the next trajectory checkpoint.
    tool_results.update(durable_tool_results)
    for item in native_items:
        steps.append(
            _native_agent_step(
                item,
                step_id=len(steps) + 1,
                model_name=model_name,
                tool_results=tool_results,
            )
        )

    captured_agent_steps = [step for step in steps if step.get("source") == "agent"]
    semantic_status = str(normalized_outcome.get("semantic_status") or "succeeded")
    completed = semantic_status == "succeeded"
    if len(steps) == 1 and completed:
        steps.append(
            {
                "step_id": 2,
                "source": "agent",
                "message": "(AWorld completed without a captured response)",
                "llm_call_count": 0,
            }
        )

    inferred_llm_calls = len(trajectory_payload.get("llm_calls") or [])
    if not inferred_llm_calls:
        inferred_llm_calls = len(captured_agent_steps)
    llm_call_count = _run_metric(
        normalized_outcome,
        trajectory_payload,
        "llm_call_count",
        inferred_llm_calls,
    )
    tool_call_count = _run_metric(
        normalized_outcome,
        trajectory_payload,
        "tool_call_count",
        _count_tool_calls(native_items),
    )
    action_count = _run_metric(
        normalized_outcome,
        trajectory_payload,
        "action_count",
        len(captured_agent_steps),
    )

    # Native captured steps each represent one provider action.  Reconcile the
    # per-step ATIF counters to the authoritative control-plane total without
    # fabricating extra assistant messages on partial failures.
    remaining_llm_calls = llm_call_count
    for step in captured_agent_steps:
        step["llm_call_count"] = 1 if remaining_llm_calls > 0 else 0
        remaining_llm_calls = max(0, remaining_llm_calls - 1)
    if captured_agent_steps and remaining_llm_calls:
        captured_agent_steps[-1]["llm_call_count"] += remaining_llm_calls

    agent: dict[str, Any] = {
        "name": agent_name,
        "version": agent_version,
    }
    if model_name:
        agent["model_name"] = model_name

    trajectory_fidelity = str(
        normalized_outcome.get("trajectory_fidelity")
        or trajectory_payload.get("trajectory_fidelity")
        or ("complete" if completed else "partial")
    )
    final_metrics: dict[str, Any] = {
        "total_steps": len(steps),
        **_complete_usage_totals(trajectory_payload, llm_call_count),
        "extra": {
            "llm_call_count": llm_call_count,
            "tool_call_count": tool_call_count,
            "action_count": action_count,
        },
    }
    # Keep the ATIF shape stable without manufacturing cache misses. An exact
    # provider receipt produces an integer (including a genuine zero); missing
    # or partial provider cache usage remains JSON null.
    final_metrics.setdefault("total_cached_tokens", None)
    from aworld_cli.executors.stats import build_llm_diagnostics_summary

    llm_diagnostics = build_llm_diagnostics_summary(
        [
            call
            for call in trajectory_payload.get("llm_calls") or []
            if isinstance(call, dict)
        ]
    )
    if llm_diagnostics.get("call_count"):
        final_metrics["extra"]["llm_diagnostics"] = llm_diagnostics
    execution_protocol = _execution_protocol_telemetry(
        trajectory_payload.get("execution_protocol")
    )
    if execution_protocol is not None:
        final_metrics["extra"]["execution_protocol"] = execution_protocol
    aworld_projection: dict[str, Any] = {
        "completion_state": "complete" if completed else "incomplete",
        "trajectory_fidelity": trajectory_fidelity,
        "llm_call_count": llm_call_count,
        "tool_call_count": tool_call_count,
        "action_count": action_count,
        "last_successful_checkpoint": normalized_outcome.get(
            "last_successful_checkpoint"
        ),
    }
    if tool_journal_evidence is not None:
        aworld_projection["tool_action_journal"] = tool_journal_evidence
    if normalized_outcome:
        aworld_projection["run_outcome"] = normalized_outcome

    return {
        "schema_version": "ATIF-v1.7",
        "session_id": session_id,
        "agent": agent,
        "steps": steps,
        "final_metrics": final_metrics,
        "extra": {
            "producer": "aworld-cli",
            "trajectory_capture_mode": trajectory_payload.get(
                "trajectory_capture_mode",
                "unknown",
            ),
            "aworld": aworld_projection,
        },
    }


def write_atif_trajectory(
    path: str | os.PathLike[str], trajectory: dict[str, Any]
) -> None:
    """Write an ATIF trajectory atomically."""
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(
        f".{output_path.name}.{uuid.uuid4().hex}.tmp"
    )
    try:
        with temporary_path.open("w", encoding="utf-8") as stream:
            stream.write(json.dumps(trajectory, ensure_ascii=False, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary_path.replace(output_path)
    finally:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass


def try_write_atif_trajectory(
    path: str | os.PathLike[str],
    trajectory: dict[str, Any],
    *,
    trajectory_fidelity: str,
) -> AtifExportReceipt:
    """Persist ATIF and return a sanitized receipt instead of raising.

    The CLI boundary decides whether a requested sink failure changes the
    effective process outcome; this low-level helper only reports persistence.
    """

    try:
        write_atif_trajectory(path, trajectory)
    except Exception as exc:
        return AtifExportReceipt(
            status=AtifExportStatus.FAILED,
            trajectory_fidelity=trajectory_fidelity,
            error_code="atif_write_failed",
            error_type=type(exc).__name__,
        )
    return AtifExportReceipt(
        status=AtifExportStatus.PERSISTED,
        trajectory_fidelity=trajectory_fidelity,
    )


__all__ = [
    "AtifExportReceipt",
    "AtifExportStatus",
    "build_atif_trajectory",
    "try_write_atif_trajectory",
    "write_atif_trajectory",
]
