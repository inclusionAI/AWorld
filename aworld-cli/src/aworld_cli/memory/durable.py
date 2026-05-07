from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DURABLE_MEMORY_TYPES = ("user", "feedback", "workspace", "reference")
INSTRUCTION_MEMORY_TYPES = frozenset({"user", "feedback", "workspace"})
DEFAULT_DURABLE_MEMORY_TYPE = "workspace"


@dataclass(frozen=True)
class DurableMemoryRecord:
    memory_type: str
    content: str
    source: str
    recorded_at: str
    source_file: Path
    decision_id: str = ""
    source_ref: dict[str, str] | None = None


@dataclass(frozen=True)
class DurableMemoryWriteResult:
    record_path: Path
    memory_type: str
    record_created: bool


def normalize_durable_memory_type(memory_type: str | None) -> str:
    normalized = (memory_type or DEFAULT_DURABLE_MEMORY_TYPE).strip().lower()
    if normalized in DURABLE_MEMORY_TYPES:
        return normalized

    valid = ", ".join(DURABLE_MEMORY_TYPES)
    raise ValueError(f"Invalid durable memory type: {memory_type}. Valid types: {valid}")


def durable_memory_file(workspace_path: str | os.PathLike[str]) -> Path:
    workspace = Path(workspace_path).expanduser().resolve()
    return workspace / ".aworld" / "memory" / "durable.jsonl"


def promotion_reviews_file(workspace_path: str | os.PathLike[str]) -> Path:
    workspace = Path(workspace_path).expanduser().resolve()
    return workspace / ".aworld" / "memory" / "metrics" / "promotion_reviews.jsonl"


def read_durable_memory_records(
    workspace_path: str | os.PathLike[str],
    *,
    memory_type: str | None = None,
) -> tuple[DurableMemoryRecord, ...]:
    records = read_all_durable_memory_records(
        workspace_path,
        memory_type=memory_type,
    )
    inactive_governed_decision_ids = _inactive_governed_decision_ids(workspace_path)
    if not inactive_governed_decision_ids:
        return records

    active_records: list[DurableMemoryRecord] = []
    for record in records:
        if (
            record.source == "governed_auto_promotion"
            and record.decision_id
            and record.decision_id in inactive_governed_decision_ids
        ):
            continue
        active_records.append(record)
    return tuple(active_records)


def read_all_durable_memory_records(
    workspace_path: str | os.PathLike[str],
    *,
    memory_type: str | None = None,
) -> tuple[DurableMemoryRecord, ...]:
    target = durable_memory_file(workspace_path)
    if not target.exists():
        return ()

    normalized_type = normalize_durable_memory_type(memory_type) if memory_type else None
    records: list[DurableMemoryRecord] = []

    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ()

    for line in lines:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        content = payload.get("content")
        record_type = payload.get("memory_type")
        source = payload.get("source")
        recorded_at = payload.get("recorded_at")
        decision_id = payload.get("decision_id")
        source_ref = payload.get("source_ref")
        if not isinstance(content, str) or not content.strip():
            continue
        if not isinstance(record_type, str):
            continue
        try:
            record_type = normalize_durable_memory_type(record_type)
        except ValueError:
            continue
        if normalized_type and record_type != normalized_type:
            continue
        records.append(
            DurableMemoryRecord(
                memory_type=record_type,
                content=content.strip(),
                source=source if isinstance(source, str) and source.strip() else "unknown",
                recorded_at=recorded_at if isinstance(recorded_at, str) else "",
                source_file=target,
                decision_id=decision_id.strip() if isinstance(decision_id, str) else "",
                source_ref=_normalize_source_ref(source_ref),
            )
        )

    return tuple(records)


def append_durable_memory_record(
    workspace_path: str | os.PathLike[str],
    *,
    memory_type: str,
    text: str,
    source: str,
    decision_id: str | None = None,
    source_ref: dict[str, str] | None = None,
) -> DurableMemoryWriteResult:
    normalized_type = normalize_durable_memory_type(memory_type)
    normalized_text = (text or "").strip()
    if not normalized_text:
        raise ValueError("Durable memory content must not be empty")

    target = durable_memory_file(workspace_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    existing = read_durable_memory_records(workspace_path, memory_type=normalized_type)
    if any(record.content == normalized_text for record in existing):
        return DurableMemoryWriteResult(
            record_path=target,
            memory_type=normalized_type,
            record_created=False,
        )

    payload = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "memory_type": normalized_type,
        "content": normalized_text,
        "source": source,
    }
    if isinstance(decision_id, str) and decision_id.strip():
        payload["decision_id"] = decision_id.strip()
    normalized_source_ref = _normalize_source_ref(source_ref)
    if normalized_source_ref:
        payload["source_ref"] = normalized_source_ref
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False))
        handle.write("\n")

    return DurableMemoryWriteResult(
        record_path=target,
        memory_type=normalized_type,
        record_created=True,
    )


def _normalize_source_ref(source_ref: object) -> dict[str, str] | None:
    if not isinstance(source_ref, dict):
        return None
    normalized: dict[str, str] = {}
    for key, value in source_ref.items():
        if not isinstance(key, str):
            continue
        if value is None:
            continue
        normalized[key] = str(value)
    return normalized or None


def _inactive_governed_decision_ids(
    workspace_path: str | os.PathLike[str],
) -> set[str]:
    target = promotion_reviews_file(workspace_path)
    if not target.exists():
        return set()

    latest_review_actions: dict[str, str] = {}
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()

    for line in lines:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        decision_id = payload.get("decision_id")
        review_action = payload.get("review_action")
        if not isinstance(decision_id, str) or not decision_id.strip():
            continue
        if not isinstance(review_action, str) or not review_action.strip():
            continue
        latest_review_actions[decision_id.strip()] = review_action.strip().lower()

    return {
        decision_id
        for decision_id, review_action in latest_review_actions.items()
        if review_action == "reverted"
    }
