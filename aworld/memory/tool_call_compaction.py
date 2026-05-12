import hashlib
import json
from typing import Any

from aworld.models.utils import num_tokens_from_string

REPLAY_TOOL_CALL_ARGUMENT_TOKEN_THRESHOLD = 1024
_MAX_SCHEMA_KEYS = 6
_MAX_SCHEMA_DEPTH = 2
_MAX_SUMMARY_KEYS = 8


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _shape_name(value: Any, *, depth: int = 0) -> str:
    if depth >= _MAX_SCHEMA_DEPTH:
        if isinstance(value, dict):
            return "object"
        if isinstance(value, list):
            return "list"
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "bool"
        if isinstance(value, (int, float)):
            return "number"
        if isinstance(value, str):
            return "string"
        return type(value).__name__

    if isinstance(value, dict):
        items = []
        sorted_items = sorted(((str(key), inner_value) for key, inner_value in value.items()), key=lambda item: item[0])
        for key, inner_value in sorted_items[:_MAX_SCHEMA_KEYS]:
            items.append(f"{key}:{_shape_name(inner_value, depth=depth + 1)}")
        if len(value) > _MAX_SCHEMA_KEYS:
            items.append(f"+{len(value) - _MAX_SCHEMA_KEYS}")
        return "object{" + ",".join(items) + "}"

    if isinstance(value, list):
        if not value:
            return "list[0]"
        first_shape = _shape_name(value[0], depth=depth + 1)
        return f"list[{len(value)}]<{first_shape}>"

    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return type(value).__name__


def _semantic_summary(value: Any) -> str:
    if isinstance(value, dict):
        keys = sorted(str(k) for k in value.keys())
        preview = ",".join(keys[:_MAX_SUMMARY_KEYS])
        if len(keys) > _MAX_SUMMARY_KEYS:
            preview = f"{preview},+{len(keys) - _MAX_SUMMARY_KEYS}"
        return f"keys={preview}" if preview else "keys="
    if isinstance(value, list):
        return f"items={len(value)}"
    if isinstance(value, str):
        return f"string(len={len(value)})"
    return _shape_name(value)


def _content_hash(serialized: str) -> str:
    return "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def _placeholder_arguments(
    *,
    tool_name: str | None,
    serialized: str,
    parsed_value: Any,
    sanitized_reason: str,
) -> str:
    token_count = num_tokens_from_string(serialized) if serialized else 0
    return _canonical_json(
        {
            "_aworld_replay": "compacted_tool_call_arguments",
            "argument_schema": _shape_name(parsed_value),
            "content_hash": _content_hash(serialized),
            "original_chars": len(serialized),
            "original_tokens": token_count,
            "sanitized_reason": sanitized_reason,
            "semantic_summary": _semantic_summary(parsed_value),
            "tool_name": tool_name or "unknown",
        }
    )


def normalize_tool_call_arguments_for_replay(
    arguments: Any,
    *,
    tool_name: str | None = None,
    token_threshold: int = REPLAY_TOOL_CALL_ARGUMENT_TOKEN_THRESHOLD,
) -> str | None:
    if arguments is None:
        return None

    parsed_value = arguments
    serialized = None

    if isinstance(arguments, str):
        stripped = arguments.strip()
        if not stripped or stripped.lower() in {"none", "null"}:
            return None
        try:
            parsed_value = json.loads(stripped)
        except (TypeError, json.JSONDecodeError):
            return _placeholder_arguments(
                tool_name=tool_name,
                serialized=stripped,
                parsed_value=stripped,
                sanitized_reason="invalid_json_arguments",
            )
    elif isinstance(arguments, tuple):
        parsed_value = list(arguments)

    try:
        serialized = _canonical_json(parsed_value)
    except TypeError:
        serialized = str(parsed_value)
        return _placeholder_arguments(
            tool_name=tool_name,
            serialized=serialized,
            parsed_value=serialized,
            sanitized_reason="non_json_arguments",
        )

    token_count = num_tokens_from_string(serialized) if serialized else 0
    if token_count > max(token_threshold, 0):
        return _placeholder_arguments(
            tool_name=tool_name,
            serialized=serialized,
            parsed_value=parsed_value,
            sanitized_reason="oversized_replay_compaction",
        )

    return serialized


def normalize_tool_call_for_replay(
    tool_call: dict[str, Any],
    *,
    token_threshold: int = REPLAY_TOOL_CALL_ARGUMENT_TOKEN_THRESHOLD,
) -> dict[str, Any]:
    normalized_tool_call = dict(tool_call)

    if "function" not in normalized_tool_call and "name" in normalized_tool_call and "arguments" in normalized_tool_call:
        normalized_tool_call["function"] = {
            "name": normalized_tool_call["name"],
            "arguments": normalized_tool_call["arguments"],
        }

    function_payload = normalized_tool_call.get("function")
    if not isinstance(function_payload, dict):
        function_payload = {}

    function_payload = dict(function_payload)
    function_payload["name"] = function_payload.get("name") or normalized_tool_call.get("name") or "unknown"
    function_payload["arguments"] = normalize_tool_call_arguments_for_replay(
        function_payload.get("arguments"),
        tool_name=function_payload.get("name"),
        token_threshold=token_threshold,
    )
    normalized_tool_call["function"] = function_payload
    return normalized_tool_call


def normalize_tool_calls_for_replay(
    tool_calls: list[dict[str, Any]] | None,
    *,
    token_threshold: int = REPLAY_TOOL_CALL_ARGUMENT_TOKEN_THRESHOLD,
) -> list[dict[str, Any]] | None:
    if not tool_calls:
        return None
    return [
        normalize_tool_call_for_replay(tool_call, token_threshold=token_threshold)
        for tool_call in tool_calls
        if isinstance(tool_call, dict)
    ] or None


def _content_text_length(content: Any) -> int:
    if content is None:
        return 0
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        total = 0
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                total += len(item["text"])
            else:
                total += len(str(item))
        return total
    return len(str(content))


def _is_compacted_replay_arguments(arguments: Any) -> bool:
    if not isinstance(arguments, str):
        return False
    try:
        payload = json.loads(arguments)
    except (TypeError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and payload.get("_aworld_replay") == "compacted_tool_call_arguments"


def parse_compacted_replay_arguments(arguments: Any) -> dict[str, Any] | None:
    payload = arguments
    if isinstance(arguments, str):
        try:
            payload = json.loads(arguments)
        except (TypeError, json.JSONDecodeError):
            return None
    if isinstance(payload, dict) and payload.get("_aworld_replay") == "compacted_tool_call_arguments":
        return payload
    return None


def compacted_replay_execution_error(
    arguments: Any,
    *,
    tool_name: str | None = None,
) -> str | None:
    payload = parse_compacted_replay_arguments(arguments)
    if not payload:
        return None

    target_tool = tool_name or payload.get("tool_name") or "unknown"
    schema = payload.get("argument_schema") or "unknown"
    reason = payload.get("sanitized_reason") or "unknown"
    return (
        f"Tool call arguments for {target_tool} were compacted for replay and cannot be executed directly "
        f"(reason={reason}, schema={schema}). Please regenerate the full tool call arguments."
    )


def collect_replay_message_metrics(messages: list[dict[str, Any]]) -> dict[str, int]:
    metrics = {
        "assistant_tool_call_argument_bytes": 0,
        "assistant_tool_call_compacted_count": 0,
        "assistant_tool_call_offloaded_count": 0,
        "tool_result_bytes": 0,
    }

    for message in messages:
        if not isinstance(message, dict):
            continue

        if message.get("role") == "assistant":
            for tool_call in message.get("tool_calls") or []:
                if not isinstance(tool_call, dict):
                    continue
                function_payload = tool_call.get("function") or {}
                arguments = function_payload.get("arguments")
                if isinstance(arguments, str):
                    metrics["assistant_tool_call_argument_bytes"] += len(arguments.encode("utf-8"))
                    if _is_compacted_replay_arguments(arguments):
                        metrics["assistant_tool_call_compacted_count"] += 1
                        if '"sanitized_reason":"oversized_replay_compaction"' in arguments:
                            metrics["assistant_tool_call_offloaded_count"] += 1

        if message.get("role") == "tool":
            metrics["tool_result_bytes"] += _content_text_length(message.get("content"))

    return metrics
