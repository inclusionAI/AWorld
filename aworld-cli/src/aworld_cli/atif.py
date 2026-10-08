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
_MAX_ATIF_MAPPING_KEY_CHARS = 256
_MAX_ATIF_ARGUMENT_SERIALIZED_CHARS = 16_384
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
            raw_key_text = str(raw_key)
            key = _bounded_redacted_text(
                raw_key_text,
                max_chars=_MAX_ATIF_MAPPING_KEY_CHARS,
            )[0]
            if key in projected:
                key = (
                    key[: max(0, _MAX_ATIF_MAPPING_KEY_CHARS - 17)]
                    + "#"
                    + hashlib.sha256(raw_key_text.encode("utf-8")).hexdigest()[:16]
                )
            if any(
                token in raw_key_text.casefold()
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


def _bounded_arguments(value: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(encoded) <= _MAX_ATIF_ARGUMENT_SERIALIZED_CHARS:
        return value
    preview, projection = _bounded_redacted_text(
        encoded,
        max_chars=_MAX_ATIF_TEXT_CHARS,
    )
    return {
        "__aworld_bounded_arguments__": {
            "schema_version": "aworld.atif.bounded-arguments.v1",
            "truncated": True,
            "serialized_chars": len(encoded),
            "content_hash": projection["content_hash"],
            "preview": preview,
        }
    }


def _parse_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return _bounded_arguments(_safe_projection(value))
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"raw": _safe_projection(value)}
        projected = (
            _safe_projection(parsed)
            if isinstance(parsed, dict)
            else {"value": _safe_projection(parsed)}
        )
        return _bounded_arguments(projected)
    return (
        _bounded_arguments({"value": _safe_projection(value)})
        if value is not None
        else {}
    )


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


def _native_scope_token(meta: dict[str, Any]) -> str:
    from aworld_cli.durable_scope import normalize_scope

    return json.dumps(
        normalize_scope(meta),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _native_tool_result_series(
    native_items: list[dict[str, Any]],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    series: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in native_items:
        scope_token = _native_scope_token(_as_dict(item.get("meta")))
        state_input = _as_dict(_as_dict(item.get("state")).get("input"))
        for result in state_input.get("action_result") or []:
            if not isinstance(result, dict):
                continue
            call_id = result.get("tool_call_id")
            if not isinstance(call_id, str) or not call_id:
                continue
            raw_content = result.get("content")
            content, _ = _bounded_redacted_text(
                raw_content
                if isinstance(raw_content, str)
                else _safe_projection(raw_content)
            )
            series.setdefault((scope_token, call_id), []).append({"content": content})
    return series


def _tool_call_counts(
    native_items: list[dict[str, Any]],
) -> tuple[dict[tuple[str, str], int], dict[str, int]]:
    scoped: dict[tuple[str, str], int] = {}
    global_counts: dict[str, int] = {}
    for item in native_items:
        scope_token = _native_scope_token(_as_dict(item.get("meta")))
        for call in _as_dict(item.get("action")).get("tool_calls") or []:
            call_id = call.get("id") if isinstance(call, dict) else None
            if not isinstance(call_id, str) or not call_id:
                continue
            key = (scope_token, call_id)
            scoped[key] = scoped.get(key, 0) + 1
            global_counts[call_id] = global_counts.get(call_id, 0) + 1
    return scoped, global_counts


@dataclass
class _ToolResultLedger:
    native: dict[tuple[str, str], list[dict[str, Any]]]
    native_by_call_id: dict[str, list[dict[str, Any]]]
    durable: dict[tuple[tuple[Any, ...], str], list[dict[str, Any]]]
    scoped_call_counts: dict[tuple[str, str], int]
    global_call_counts: dict[str, int]

    def lookup(
        self,
        *,
        meta: dict[str, Any],
        call_id: str,
        native_occurrence: int,
        durable_occurrence: int,
    ) -> dict[str, Any] | None:
        from aworld_cli.durable_scope import scope_key

        native_key = (_native_scope_token(meta), call_id)
        native_results = self.native.get(native_key, [])
        if (
            len(native_results) == self.scoped_call_counts.get(native_key, 0)
            and native_occurrence < len(native_results)
        ):
            return native_results[native_occurrence]
        native_legacy = self.native_by_call_id.get(call_id, [])
        if self.global_call_counts.get(call_id) == 1 and len(native_legacy) == 1:
            return native_legacy[0]
        exact_scope = scope_key(meta)
        if exact_scope is not None:
            durable_results = self.durable.get((exact_scope, call_id), [])
            if durable_occurrence < len(durable_results):
                return durable_results[durable_occurrence]
        return None


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


def _durable_tool_result_series(
    native_items: list[dict[str, Any]],
) -> tuple[
    dict[tuple[tuple[Any, ...], str], list[dict[str, Any]]],
    dict[str, Any] | None,
]:
    """Recover journal results by full scope, batch occurrence and action index."""

    from aworld_cli.durable_scope import normalize_scope, scope_key

    known_call_ids = {
        call_id
        for item in native_items
        for call in _as_dict(item.get("action")).get("tool_calls") or []
        if isinstance(call, dict)
        and isinstance((call_id := call.get("id")), str)
        and call_id
    }
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
    if not known_call_ids:
        return {}, {
            "schema_version": "aworld.tool-action-journal.v1",
            "status": "unavailable",
            "reason_code": "tool_call_scope_unavailable",
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
        tuple[tuple[Any, ...], str, int, str],
        tuple[int, int, dict[str, Any]],
    ] = {}
    selected_event_count = 0
    rejected_incomplete_scope_count = 0
    for event in recovery.events:
        actions = event.get("actions")
        if not isinstance(actions, list):
            continue
        event_type = str(event.get("event_type") or "")
        metadata = _as_dict(event.get("metadata"))
        context_management = _as_dict(metadata.get("context_management"))
        rolled_back = (
            event_type == "sandbox_transaction_resolved"
            and (
                event.get("status") == "rolled_back"
                or context_management.get("rollback_performed") is True
            )
        )
        priority = (
            40
            if rolled_back
            else {
                "tool_observation_recorded": 30,
                "sandbox_call_completed": 20,
                "sandbox_call_failed": 20,
                "sandbox_transaction_resolved": 10,
            }.get(event_type, 0)
        )
        results = event.get("results")
        if priority == 0 or (
            not rolled_back
            and event_type not in {"sandbox_call_failed"}
            and not isinstance(results, list)
        ):
            continue
        results = results if isinstance(results, list) else []
        event_scope = normalize_scope(event.get("context"))
        event_scope_key = scope_key(event_scope)
        if event_scope_key is None:
            rejected_incomplete_scope_count += 1
            continue
        batch_id = str(event.get("batch_id") or "")
        if not batch_id:
            continue
        selected_event_count += 1
        for index, raw_action in enumerate(actions):
            action = _as_dict(raw_action)
            result = _as_dict(results[index] if index < len(results) else None)
            call_id = result.get("tool_call_id") or action.get("tool_call_id")
            if not isinstance(call_id, str) or call_id not in known_call_ids:
                continue
            if rolled_back:
                result = {
                    "tool_call_id": call_id,
                    "success": False,
                    "error": "sandbox_transaction_rolled_back",
                    "content": "Tool result was rolled back and is not committed.",
                }
            elif event_type == "sandbox_call_failed" and not result:
                result = {
                    "tool_call_id": call_id,
                    "success": False,
                    "error": "sandbox_call_failed",
                    "content": "Tool execution failed before returning an observation.",
                }
            elif not result:
                continue
            projected = _project_tool_observation_result(
                result,
                call_id=call_id,
                source=(
                    "tool_action_journal:model_visible_observation"
                    if event_type == "tool_observation_recorded"
                    else f"tool_action_journal:{event_type}"
                ),
            )
            if event_type == "sandbox_call_failed":
                failure_type = _safe_error_code(metadata.get("error_type"))
                if failure_type is not None:
                    projected["extra"]["failure_type"] = failure_type
            if rolled_back:
                projected["extra"].update(
                    {
                        "status": "rolled_back",
                        "success": False,
                        "error_code": "sandbox_transaction_rolled_back",
                        "rollback": {
                            "performed": True,
                            "reason_code": _safe_error_code(
                                context_management.get("rollback_reason")
                            ),
                        },
                    }
                )
                projected["extra"]["rollback"] = {
                    key: value
                    for key, value in projected["extra"]["rollback"].items()
                    if value is not None
                }
            occurrence_key = (event_scope_key, batch_id, index, call_id)
            candidate = (
                priority,
                int(event.get("recorded_at_epoch_ns") or 0),
                projected,
            )
            previous = selected.get(occurrence_key)
            if previous is None or candidate[:2] > previous[:2]:
                selected[occurrence_key] = candidate

    grouped: dict[
        tuple[tuple[Any, ...], str],
        list[tuple[int, str, int, dict[str, Any]]],
    ] = {}
    for (event_scope_key, batch_id, index, call_id), (
        _,
        recorded_at,
        result,
    ) in selected.items():
        entry = (recorded_at, batch_id, index, result)
        grouped.setdefault((event_scope_key, call_id), []).append(entry)
    durable = {
        key: [entry[3] for entry in sorted(entries, key=lambda item: item[:3])]
        for key, entries in grouped.items()
    }
    durable_by_call_id: dict[str, list[dict[str, Any]]] = {}
    for (_, call_id), results_for_scope in durable.items():
        durable_by_call_id.setdefault(call_id, []).extend(results_for_scope)
    ambiguous_result_count = sum(
        max(0, len(results) - 1) for results in durable_by_call_id.values()
    )
    evidence = recovery.to_evidence()
    evidence.update(
        {
            "selected_event_count": selected_event_count,
            "recovered_result_count": len(selected),
            "ambiguous_result_count": ambiguous_result_count,
            "ambiguous_call_id_count": ambiguous_result_count,
            "rejected_incomplete_scope_count": rejected_incomplete_scope_count,
        }
    )
    return durable, evidence


def _native_agent_step(
    item: dict[str, Any],
    *,
    step_id: int,
    model_name: str | None,
    tool_results: _ToolResultLedger,
    native_occurrences: dict[tuple[str, str], int],
    durable_occurrences: dict[tuple[tuple[Any, ...] | None, str], int],
) -> dict[str, Any]:
    from aworld_cli.durable_scope import scope_key

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
    missing_tool_results: list[dict[str, str]] = []
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
        native_key = (_native_scope_token(meta), call_id)
        durable_key = (scope_key(meta), call_id)
        native_occurrence = native_occurrences.get(native_key, 0)
        durable_occurrence = durable_occurrences.get(durable_key, 0)
        native_occurrences[native_key] = native_occurrence + 1
        durable_occurrences[durable_key] = durable_occurrence + 1
        result = tool_results.lookup(
            meta=meta,
            call_id=call_id,
            native_occurrence=native_occurrence,
            durable_occurrence=durable_occurrence,
        )
        if result is not None:
            observation = {
                "source_call_id": call_id,
                "content": result.get("content", ""),
            }
            if isinstance(result.get("extra"), dict):
                observation["extra"] = result["extra"]
            observation_results.append(observation)
        else:
            # This is a trajectory diagnostic, not a synthetic Tool result.
            # Keep the causal gap explicit without fabricating content,
            # success, a receipt, or an ``observation.results`` entry.
            missing_tool_results.append(
                {
                    "source_call_id": call_id[:256],
                    "kind": "tool_result_missing",
                }
            )

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
    if missing_tool_results:
        step["extra"]["missing_tool_results"] = missing_tool_results[:32]
    if isinstance(meta.get("llm_request_id"), str) and meta["llm_request_id"]:
        step["extra"]["aworld_llm_request_id"] = meta["llm_request_id"][:256]
    from aworld_cli.durable_scope import task_epoch as normalize_task_epoch

    normalized_epoch = normalize_task_epoch(meta.get("task_epoch"))
    if normalized_epoch is not None:
        step["extra"]["aworld_task_epoch"] = normalized_epoch
    run_boundary_id = meta.get("run_boundary_id")
    if isinstance(run_boundary_id, str) and run_boundary_id:
        step["extra"]["aworld_run_boundary_id"] = run_boundary_id[:256]
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
    native_tool_results = _native_tool_result_series(native_items)
    native_by_call_id: dict[str, list[dict[str, Any]]] = {}
    for (_, call_id), results_for_scope in native_tool_results.items():
        native_by_call_id.setdefault(call_id, []).extend(results_for_scope)
    durable_tool_results, tool_journal_evidence = _durable_tool_result_series(
        native_items
    )
    scoped_call_counts, global_call_counts = _tool_call_counts(native_items)
    tool_results = _ToolResultLedger(
        native=native_tool_results,
        native_by_call_id=native_by_call_id,
        durable=durable_tool_results,
        scoped_call_counts=scoped_call_counts,
        global_call_counts=global_call_counts,
    )
    # Native results remain authoritative when a complete checkpoint exists;
    # the durable post-boundary journal fills only missing occurrences.
    native_occurrences: dict[tuple[str, str], int] = {}
    durable_occurrences: dict[tuple[tuple[Any, ...] | None, str], int] = {}
    for item in native_items:
        steps.append(
            _native_agent_step(
                item,
                step_id=len(steps) + 1,
                model_name=model_name,
                tool_results=tool_results,
                native_occurrences=native_occurrences,
                durable_occurrences=durable_occurrences,
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
