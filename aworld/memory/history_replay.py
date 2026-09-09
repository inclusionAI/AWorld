"""Causal ordering for event-driven short-term Memory replay."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from aworld.memory.models import MemoryAIMessage, MemoryMessage, MemoryToolMessage


def _tool_result_id(item: Any) -> str | None:
    if isinstance(item, MemoryToolMessage):
        return item.tool_call_id or None
    metadata = getattr(item, "metadata", None)
    if isinstance(metadata, dict) and metadata.get("role") == "tool":
        value = metadata.get("tool_call_id")
        return str(value) if value else None
    return None


def _assistant_tool_call_ids(item: Any) -> tuple[str, ...]:
    tool_calls = None
    if isinstance(item, MemoryAIMessage):
        tool_calls = item.tool_calls
    elif isinstance(item, MemoryMessage):
        return ()
    else:
        metadata = getattr(item, "metadata", None)
        if isinstance(metadata, dict):
            tool_calls = metadata.get("tool_calls")
    if not isinstance(tool_calls, list):
        return ()
    call_ids: list[str] = []
    for call in tool_calls:
        value = getattr(call, "id", None)
        if value is None and isinstance(call, dict):
            value = call.get("id")
        if value:
            call_ids.append(str(value))
    return tuple(call_ids)


def causalize_memory_history(histories: Sequence[Any]) -> list[Any]:
    """Return assistant/Tool groups in causal rather than append order.

    Event-driven stores can commit the matching Tool result after a subsequent
    record.  Provider protocols still require each result immediately after its
    assistant call.  First-result-wins also preserves the existing duplicate
    suppression contract.
    """
    first_tool_by_id: dict[str, Any] = {}
    for history in histories:
        tool_call_id = _tool_result_id(history)
        if tool_call_id and tool_call_id not in first_tool_by_id:
            first_tool_by_id[tool_call_id] = history

    ordered: list[Any] = []
    consumed_tool_ids: set[str] = set()
    for history in histories:
        if _tool_result_id(history):
            continue
        ordered.append(history)
        for tool_call_id in _assistant_tool_call_ids(history):
            result = first_tool_by_id.get(tool_call_id)
            if result is None or tool_call_id in consumed_tool_ids:
                continue
            ordered.append(result)
            consumed_tool_ids.add(tool_call_id)
    return ordered


__all__ = ["causalize_memory_history"]
