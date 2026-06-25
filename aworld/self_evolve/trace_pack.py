from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class TraceEvidenceStep:
    evidence_id: str
    source_index: int
    original_id: str | None
    state: Mapping[str, Any]
    action: Mapping[str, Any]
    reward: Mapping[str, Any]
    agent_id: str | None = None
    pre_agent: str | None = None
    tool_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class TracePack:
    pack_id: str
    source_kind: str
    task_id: str
    steps: tuple[TraceEvidenceStep, ...]
    omitted_step_count: int = 0
    compression_summary: str | None = None

    @property
    def final_action_excerpt(self) -> str | None:
        if not self.steps:
            return None
        content = self.steps[-1].action.get("content")
        return content if isinstance(content, str) else None


def build_trace_pack(
    trajectory: Iterable[Mapping[str, Any]],
    *,
    source_kind: str,
    task_id: str | None = None,
    max_steps: int = 8,
    max_text_chars: int = 2000,
) -> TracePack:
    items = list(trajectory)
    resolved_task_id = task_id or _task_id_from_items(items) or "unknown-task"
    selected_indexed, omitted_indexed = _select_boundary_items(items, max_steps=max_steps)

    steps = tuple(
        _evidence_step(
            item,
            source_index=source_index,
            task_id=resolved_task_id,
            max_text_chars=max_text_chars,
        )
        for source_index, item in selected_indexed
    )
    omitted_ids = [
        _evidence_id(item, task_id=resolved_task_id, fallback_index=source_index)
        for source_index, item in omitted_indexed
    ]
    compression_summary = None
    if omitted_ids:
        compression_summary = (
            f"omitted {len(omitted_ids)} middle step(s): " + ", ".join(omitted_ids)
        )

    return TracePack(
        pack_id=f"{source_kind}:{resolved_task_id}",
        source_kind=source_kind,
        task_id=resolved_task_id,
        steps=steps,
        omitted_step_count=len(omitted_ids),
        compression_summary=compression_summary,
    )


def trace_packs_from_trajectory_log(
    path: str | Path,
    *,
    max_steps: int = 8,
    max_text_chars: int = 2000,
) -> list[TracePack]:
    packs: list[TracePack] = []
    for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        record = _trajectory_log_record(raw_line)
        if record is None:
            continue
        trajectory = json.loads(record["trajectory"])
        packs.append(
            build_trace_pack(
                trajectory,
                source_kind="trajectory_log",
                task_id=record.get("task_id"),
                max_steps=max_steps,
                max_text_chars=max_text_chars,
            )
        )
    return packs


def _trajectory_log_record(raw_line: str) -> Mapping[str, Any] | None:
    clean = re.sub(r"\x1b\[[0-9;]*m", "", raw_line).strip()
    start = clean.find("{")
    if start < 0:
        return None
    try:
        record = ast.literal_eval(clean[start:])
    except (SyntaxError, ValueError):
        return None
    if not isinstance(record, Mapping):
        return None
    if "task_id" not in record or "trajectory" not in record:
        return None
    return record


def _select_boundary_items(
    items: list[Mapping[str, Any]],
    *,
    max_steps: int,
) -> tuple[list[tuple[int, Mapping[str, Any]]], list[tuple[int, Mapping[str, Any]]]]:
    if max_steps <= 0:
        raise ValueError("max_steps must be positive")
    indexed_items = list(enumerate(items))
    if len(items) <= max_steps:
        return indexed_items, []
    if max_steps == 1:
        return [indexed_items[-1]], indexed_items[:-1]

    head_count = max_steps // 2
    tail_count = max_steps - head_count
    selected = indexed_items[:head_count] + indexed_items[-tail_count:]
    omitted = indexed_items[head_count:-tail_count]
    return selected, omitted


def _evidence_step(
    item: Mapping[str, Any],
    *,
    source_index: int,
    task_id: str,
    max_text_chars: int,
) -> TraceEvidenceStep:
    meta = item.get("meta") if isinstance(item.get("meta"), Mapping) else {}
    state = _bounded_mapping(item.get("state"), max_text_chars=max_text_chars)
    action = _bounded_mapping(item.get("action"), max_text_chars=max_text_chars)
    reward = _bounded_mapping(item.get("reward"), max_text_chars=max_text_chars)
    return TraceEvidenceStep(
        evidence_id=_evidence_id(item, task_id=task_id, fallback_index=source_index),
        source_index=source_index,
        original_id=str(item.get("id")) if item.get("id") is not None else None,
        state=state,
        action=action,
        reward=reward,
        agent_id=_string_or_none(meta.get("agent_id")),
        pre_agent=_string_or_none(meta.get("pre_agent")),
        tool_names=_tool_names(action),
    )


def _evidence_id(item: Mapping[str, Any], *, task_id: str, fallback_index: int) -> str:
    if item.get("id") is not None:
        return f"{task_id}:{item['id']}"
    meta = item.get("meta") if isinstance(item.get("meta"), Mapping) else {}
    step = meta.get("step", fallback_index + 1)
    return f"{task_id}:step-{step}"


def _task_id_from_items(items: list[Mapping[str, Any]]) -> str | None:
    for item in items:
        meta = item.get("meta")
        if isinstance(meta, Mapping) and meta.get("task_id") is not None:
            return str(meta["task_id"])
    return None


def _bounded_mapping(value: Any, *, max_text_chars: int) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return {
            str(key): _bounded_value(item, max_text_chars=max_text_chars)
            for key, item in value.items()
        }
    return {}


def _bounded_value(value: Any, *, max_text_chars: int) -> Any:
    if isinstance(value, str):
        if len(value) <= max_text_chars:
            return value
        return value[: max(0, max_text_chars - 3)] + "..."
    if isinstance(value, Mapping):
        return {
            str(key): _bounded_value(item, max_text_chars=max_text_chars)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_bounded_value(item, max_text_chars=max_text_chars) for item in value]
    return value


def _tool_names(action: Mapping[str, Any]) -> tuple[str, ...]:
    names: list[str] = []
    tool_calls = action.get("tool_calls")
    if not isinstance(tool_calls, list):
        return ()
    for call in tool_calls:
        if not isinstance(call, Mapping):
            continue
        function = call.get("function")
        if isinstance(function, Mapping) and isinstance(function.get("name"), str):
            names.append(function["name"])
    return tuple(names)


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None
