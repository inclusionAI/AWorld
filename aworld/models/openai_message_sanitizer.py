import json
from typing import Any

from aworld.memory.tool_call_compaction import normalize_tool_calls_for_replay


def _normalize_reasoning_details(value: Any) -> Any:
    if value is None:
        return None

    if isinstance(value, str):
        stripped = value.strip()
        if not stripped or stripped.lower() in {"none", "null"}:
            return None
        try:
            value = json.loads(stripped)
        except (TypeError, json.JSONDecodeError):
            return None

    if isinstance(value, dict):
        return [value]

    if isinstance(value, tuple):
        value = list(value)

    if not isinstance(value, list):
        return None

    normalized = [item for item in value if isinstance(item, dict)]
    return normalized or None


def _normalize_optional_extra_content(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped or stripped.lower() in {"none", "null"}:
            return None
    return value


def _normalize_content_part(item: Any) -> dict[str, str]:
    if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
        return item
    return {"type": "text", "text": str(item)}


def _normalize_assistant_content(value: Any, *, has_tool_calls: bool) -> Any:
    if not has_tool_calls:
        return value
    if value is None:
        return []
    if isinstance(value, list):
        return [_normalize_content_part(item) for item in value]
    return [_normalize_content_part(value)]


def _normalize_tool_content(value: Any) -> list[dict[str, str]]:
    if value is None:
        return [{"type": "text", "text": ""}]
    if isinstance(value, list):
        return [_normalize_content_part(item) for item in value]
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            decoded = value
        if isinstance(decoded, list):
            return [_normalize_content_part(item) for item in decoded] or [{"type": "text", "text": ""}]
        if isinstance(decoded, dict):
            return [_normalize_content_part(json.dumps(decoded, ensure_ascii=False))]
        return [_normalize_content_part(decoded)]
    if isinstance(value, dict):
        return [_normalize_content_part(json.dumps(value, ensure_ascii=False))]
    return [_normalize_content_part(value)]


def _normalize_tool_calls(value: Any) -> Any:
    if value is None:
        return None

    if isinstance(value, str):
        stripped = value.strip()
        if not stripped or stripped.lower() in {"none", "null"}:
            return None
        try:
            value = json.loads(stripped)
        except (TypeError, json.JSONDecodeError):
            return None

    if isinstance(value, tuple):
        value = list(value)

    if isinstance(value, dict):
        value = [value]

    if not isinstance(value, list):
        return None

    normalized_tool_calls = normalize_tool_calls_for_replay([tool_call for tool_call in value if isinstance(tool_call, dict)])
    if not normalized_tool_calls:
        return None

    for normalized_tool_call in normalized_tool_calls:
        if "extra_content" in normalized_tool_call:
            normalized_tool_call["extra_content"] = _normalize_optional_extra_content(
                normalized_tool_call.get("extra_content")
            )

    return normalized_tool_calls


def sanitize_openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sanitized_messages: list[dict[str, Any]] = []

    for message in messages:
        if not isinstance(message, dict):
            sanitized_messages.append(message)
            continue

        sanitized_message = dict(message)

        if sanitized_message.get("role") == "assistant":
            if "tool_calls" in sanitized_message:
                sanitized_message["tool_calls"] = _normalize_tool_calls(sanitized_message.get("tool_calls"))
            sanitized_message["content"] = _normalize_assistant_content(
                sanitized_message.get("content"),
                has_tool_calls=bool(sanitized_message.get("tool_calls")),
            )

            if "reasoning_details" in sanitized_message:
                sanitized_message["reasoning_details"] = _normalize_reasoning_details(
                    sanitized_message.get("reasoning_details")
                )
        elif sanitized_message.get("role") == "tool":
            sanitized_message["content"] = _normalize_tool_content(sanitized_message.get("content"))

        sanitized_messages.append(sanitized_message)

    return sanitized_messages
