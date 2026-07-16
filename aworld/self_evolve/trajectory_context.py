from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from aworld.self_evolve.sanitization import sanitize_text
from aworld.self_evolve.trace_pack import TrajectoryLogRecord


TRAJECTORY_CONTEXT_SCHEMA_VERSION = "aworld.self_evolve.trajectory_context.v1"
_CONTINUATION_MARKERS = (
    "continue the current task",
    "additional operator steering",
    "interrupt requested by operator",
)
_PARENT_KEYS = ("parent_task_id", "previous_task_id", "parent_id")


@dataclass(frozen=True)
class TrajectoryContextTurn:
    role: str
    content: str
    source_task_id: str
    evidence_ref: str


@dataclass(frozen=True)
class TrajectoryContextSnapshot:
    schema_version: str
    case_id: str
    source_kind: str
    source_record_index: int
    source_fingerprint: str
    session_id: str | None
    task_input: Any
    steps: tuple[Mapping[str, Any], ...]
    step_count: int
    omitted_step_count: int
    prior_turns: tuple[TrajectoryContextTurn, ...]
    link_strategy: str | None
    fingerprint: str


def build_trajectory_context_snapshots(
    records: Sequence[TrajectoryLogRecord],
    *,
    source_kind: str = "trajectory_log",
    max_steps: int = 128,
    max_text_chars: int = 8_192,
) -> tuple[TrajectoryContextSnapshot, ...]:
    if max_steps <= 0:
        raise ValueError("max_steps must be positive")
    records_by_task = {record.task_id: record for record in records}
    latest_by_session: dict[str, TrajectoryLogRecord] = {}
    snapshots: list[TrajectoryContextSnapshot] = []
    for position, record in enumerate(records):
        task_input = _record_task_input(record)
        session_id = _record_session_id(record)
        prior_turns = _recorded_prior_turns(
            record,
            task_input=task_input,
            max_text_chars=max_text_chars,
        )
        link_strategy: str | None = (
            "recorded_message_history" if prior_turns else None
        )
        predecessor: TrajectoryLogRecord | None = None
        parent_id = _explicit_parent_id(record)
        if parent_id is not None and parent_id in records_by_task:
            predecessor = records_by_task[parent_id]
            link_strategy = "explicit_parent"
        elif not prior_turns and _is_continuation(task_input):
            if session_id and session_id in latest_by_session:
                predecessor = latest_by_session[session_id]
                link_strategy = "same_session_predecessor"
            elif position > 0:
                predecessor = records[position - 1]
                link_strategy = "adjacent_record_fallback"
        if predecessor is not None:
            prior_turns = _predecessor_turns(
                predecessor,
                max_text_chars=max_text_chars,
            )

        selected_steps = record.trajectory[:max_steps]
        steps = tuple(
            _bounded_value(step, max_text_chars=max_text_chars)
            for step in selected_steps
        )
        payload = {
            "schema_version": TRAJECTORY_CONTEXT_SCHEMA_VERSION,
            "case_id": record.task_id,
            "source_kind": source_kind,
            "source_record_index": record.record_index,
            "source_fingerprint": record.source_fingerprint,
            "session_id": session_id,
            "task_input": _bounded_value(
                task_input,
                max_text_chars=max_text_chars,
            ),
            "steps": list(steps),
            "step_count": len(record.trajectory),
            "omitted_step_count": max(0, len(record.trajectory) - len(steps)),
            "prior_turns": [asdict(turn) for turn in prior_turns],
            "link_strategy": link_strategy,
        }
        fingerprint = _fingerprint(payload)
        snapshots.append(
            TrajectoryContextSnapshot(
                schema_version=TRAJECTORY_CONTEXT_SCHEMA_VERSION,
                case_id=record.task_id,
                source_kind=source_kind,
                source_record_index=record.record_index,
                source_fingerprint=record.source_fingerprint,
                session_id=session_id,
                task_input=payload["task_input"],
                prior_turns=prior_turns,
                steps=steps,
                step_count=len(record.trajectory),
                omitted_step_count=max(0, len(record.trajectory) - len(steps)),
                link_strategy=link_strategy,
                fingerprint=fingerprint,
            )
        )
        if session_id:
            latest_by_session[session_id] = record
    return tuple(snapshots)


def context_snapshot_for_current_trajectory(
    trajectory: Sequence[Mapping[str, Any]],
    *,
    task_id: str,
) -> TrajectoryContextSnapshot:
    record = TrajectoryLogRecord(
        record_index=0,
        task_id=task_id,
        record_metadata={"task_id": task_id},
        trajectory=tuple(trajectory),
    )
    return build_trajectory_context_snapshots(
        (record,),
        source_kind="current_trajectory",
    )[0]


def input_with_reconstructed_context(
    task_input: Any,
    snapshot: TrajectoryContextSnapshot,
) -> Any:
    if not snapshot.prior_turns:
        return task_input
    transcript = "\n".join(
        f"{turn.role.title()}: {turn.content}"
        for turn in snapshot.prior_turns
    )
    prefix = (
        "Recorded prior task context "
        f"[{snapshot.link_strategy or 'recorded'}]:\n{transcript}\n\n"
        "Current task:\n"
    )
    if isinstance(task_input, str):
        return prefix + task_input
    if isinstance(task_input, Mapping):
        content = task_input.get("content")
        if isinstance(content, str):
            return {**dict(task_input), "content": prefix + content}
    return task_input


def _record_task_input(record: TrajectoryLogRecord) -> Any:
    if not record.trajectory:
        return {}
    state = record.trajectory[0].get("state")
    return state.get("input") if isinstance(state, Mapping) else {}


def _record_session_id(record: TrajectoryLogRecord) -> str | None:
    recorded = record.record_metadata.get("session_id")
    if isinstance(recorded, str) and recorded.strip():
        return recorded.strip()
    for step in record.trajectory:
        meta = step.get("meta")
        if isinstance(meta, Mapping) and meta.get("session_id") is not None:
            value = str(meta["session_id"]).strip()
            return value or None
    return None


def _explicit_parent_id(record: TrajectoryLogRecord) -> str | None:
    sources: list[Mapping[str, Any]] = [record.record_metadata]
    if record.trajectory:
        meta = record.trajectory[0].get("meta")
        if isinstance(meta, Mapping):
            sources.append(meta)
    for source in sources:
        for key in _PARENT_KEYS:
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _recorded_prior_turns(
    record: TrajectoryLogRecord,
    *,
    task_input: Any,
    max_text_chars: int,
) -> tuple[TrajectoryContextTurn, ...]:
    if not record.trajectory:
        return ()
    state = record.trajectory[0].get("state")
    messages = state.get("messages") if isinstance(state, Mapping) else None
    if not isinstance(messages, list):
        return ()
    current_text = _text_content(task_input)
    current_index: int | None = None
    for index, message in enumerate(messages):
        if not isinstance(message, Mapping) or message.get("role") != "user":
            continue
        if _text_content(message.get("content")) == current_text:
            current_index = index
    if current_index is None or current_index == 0:
        return ()
    turns: list[TrajectoryContextTurn] = []
    remaining_chars = max_text_chars
    for index in range(current_index - 1, -1, -1):
        message = messages[index]
        if not isinstance(message, Mapping):
            continue
        role = str(message.get("role") or "unknown").strip().lower()
        # A recorded system/tool prompt belongs to the source environment. The
        # current runner supplies its own system and tool context; replaying the
        # old one as user text duplicates tens of thousands of tokens and can
        # change the task's authority boundary. Conversational user/assistant
        # turns remain useful portable context.
        if role not in {"user", "assistant"}:
            continue
        content = sanitize_text(
            _text_content(message.get("content")),
            max_chars=remaining_chars,
        )
        if not content:
            continue
        turns.append(
            TrajectoryContextTurn(
                role=role,
                content=content,
                source_task_id=record.task_id,
                evidence_ref=f"{record.task_id}:message-{index}",
            )
        )
        remaining_chars -= len(content)
        if remaining_chars <= 0:
            break
    turns.reverse()
    return tuple(turns)


def _predecessor_turns(
    record: TrajectoryLogRecord,
    *,
    max_text_chars: int,
) -> tuple[TrajectoryContextTurn, ...]:
    task = sanitize_text(
        _text_content(_record_task_input(record)),
        max_chars=max_text_chars,
    )
    answer = ""
    answer_index = 0
    for index, step in enumerate(record.trajectory):
        action = step.get("action")
        if isinstance(action, Mapping) and _text_content(action.get("content")):
            answer = sanitize_text(
                _text_content(action.get("content")),
                max_chars=max_text_chars,
            )
            answer_index = index
    turns: list[TrajectoryContextTurn] = []
    if task:
        turns.append(
            TrajectoryContextTurn(
                role="user",
                content=task,
                source_task_id=record.task_id,
                evidence_ref=f"{record.task_id}:input",
            )
        )
    if answer:
        turns.append(
            TrajectoryContextTurn(
                role="assistant",
                content=answer,
                source_task_id=record.task_id,
                evidence_ref=f"{record.task_id}:step-{answer_index + 1}",
            )
        )
    return tuple(turns)


def _is_continuation(value: Any) -> bool:
    text = _text_content(value).lower()
    return any(marker in text for marker in _CONTINUATION_MARKERS)


def _text_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        content = value.get("content")
        if isinstance(content, str):
            return content
        return "\n".join(_text_content(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return "\n".join(_text_content(item) for item in value)
    return ""


def _bounded_value(value: Any, *, max_text_chars: int) -> Any:
    if isinstance(value, str):
        return sanitize_text(value, max_chars=max_text_chars)
    if isinstance(value, bool) or isinstance(value, (int, float)) or value is None:
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _bounded_value(item, max_text_chars=max_text_chars)
            for key, item in list(value.items())[:64]
        }
    if isinstance(value, (list, tuple)):
        return [
            _bounded_value(item, max_text_chars=max_text_chars)
            for item in list(value)[:128]
        ]
    return sanitize_text(value, max_chars=max_text_chars)


def _fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
