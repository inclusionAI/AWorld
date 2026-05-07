from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from aworld_cli.memory.durable import INSTRUCTION_MEMORY_TYPES, read_durable_memory_records
from aworld_cli.memory.promotion import TEMPORARY_HINTS

GOVERNANCE_POLICY_VERSION = "2026-05-07"
VALID_GOVERNANCE_MODES = frozenset({"off", "shadow", "governed"})
REQUIRED_SOURCE_REF_KEYS = ("session_id", "task_id", "candidate_id")
REQUIRED_DECISION_FIELDS = (
    "decision_id",
    "policy_mode",
    "policy_version",
    "decision",
    "reason",
    "confidence",
    "source_ref",
    "blockers",
)


@dataclass(frozen=True)
class GovernedDecision:
    decision_id: str
    candidate_id: str
    decision: str
    policy_mode: str
    policy_version: str
    reason: str
    blockers: tuple[str, ...] = ()
    confidence: str = ""
    memory_type: str = "workspace"
    content: str = ""
    source_ref: dict[str, str] = field(default_factory=dict)
    evaluated_at: str = ""

    def to_payload(self) -> dict:
        return asdict(self)


def governance_mode() -> str:
    raw = os.getenv("AWORLD_CLI_PROMOTION_MODE", "shadow").strip().lower()
    if raw in VALID_GOVERNANCE_MODES:
        return raw
    return "shadow"


def decisions_file(workspace_path: str | os.PathLike[str]) -> Path:
    workspace = Path(workspace_path).expanduser().resolve()
    return workspace / ".aworld" / "memory" / "metrics" / "promotion_decisions.jsonl"


def reviews_file(workspace_path: str | os.PathLike[str]) -> Path:
    workspace = Path(workspace_path).expanduser().resolve()
    return workspace / ".aworld" / "memory" / "metrics" / "promotion_reviews.jsonl"


def evaluate_governed_candidate(
    workspace_path: str | os.PathLike[str],
    candidate: dict,
    mode: str | None = None,
) -> GovernedDecision:
    resolved_mode = _normalize_governance_mode(mode)
    content = str(candidate.get("content") or "").strip()
    memory_type = str(candidate.get("memory_type") or "workspace").strip().lower()
    confidence = str(candidate.get("confidence") or "").strip()
    source_ref = _normalize_source_ref(candidate.get("source_ref"))
    blockers: list[str] = []

    if not content:
        blockers.append("missing_content")
    if _looks_temporary(content):
        blockers.append("temporary_candidate")
    if memory_type not in INSTRUCTION_MEMORY_TYPES:
        blockers.append("ineligible_memory_type")
    if not confidence:
        blockers.append("missing_confidence")
    if not _has_stable_source_ref(source_ref):
        blockers.append("missing_source_ref")
    if (
        content
        and memory_type in INSTRUCTION_MEMORY_TYPES
        and any(
            record.content == content
            for record in read_durable_memory_records(
                workspace_path,
                memory_type=memory_type,
            )
        )
    ):
        blockers.append("duplicate_active_durable_memory")

    if blockers:
        decision = "rejected"
        reason = blockers[0]
    elif resolved_mode == "governed":
        decision = "durable_memory"
        reason = "governed_policy_pass"
    elif resolved_mode == "off":
        decision = "session_log_only"
        reason = "governance_mode_off"
    else:
        decision = "session_log_only"
        reason = "shadow_mode_no_auto_promotion"

    return GovernedDecision(
        decision_id=str(candidate.get("decision_id") or _generated_id("gdec")),
        candidate_id=str(candidate.get("candidate_id") or _generated_id("cand")),
        decision=decision,
        policy_mode=resolved_mode,
        policy_version=GOVERNANCE_POLICY_VERSION,
        reason=reason,
        blockers=tuple(blockers),
        confidence=confidence,
        memory_type=memory_type,
        content=content,
        source_ref=source_ref,
        evaluated_at=datetime.now(timezone.utc).isoformat(),
    )


def append_governed_decision(
    workspace_path: str | os.PathLike[str],
    payload: dict,
) -> Path:
    return _append_jsonl(
        decisions_file(workspace_path),
        _normalize_decision_payload(payload),
    )


def append_governed_review(
    workspace_path: str | os.PathLike[str],
    payload: dict,
) -> Path:
    return _append_jsonl(reviews_file(workspace_path), payload)


def list_governed_decisions(workspace_path: str | os.PathLike[str]) -> list[dict]:
    decisions = [
        _normalize_listed_decision_payload(payload)
        for payload in _read_jsonl(decisions_file(workspace_path))
        if _is_listable_decision_payload(payload)
    ]
    reviews_by_decision: dict[str, list[dict]] = {}
    for review in _read_jsonl(reviews_file(workspace_path)):
        decision_id = review.get("decision_id")
        if not isinstance(decision_id, str) or not decision_id:
            continue
        reviews_by_decision.setdefault(decision_id, []).append(review)

    merged: list[dict] = []
    for payload in decisions:
        decision_id = payload.get("decision_id")
        reviews = reviews_by_decision.get(decision_id, [])
        merged.append({**payload, "reviews": reviews})
    return merged


def _append_jsonl(target: Path, payload: dict) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False))
        handle.write("\n")
    return target


def _read_jsonl(target: Path) -> list[dict]:
    if not target.exists():
        return []

    records: list[dict] = []
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    for line in lines:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def _generated_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def _normalize_governance_mode(mode: str | None) -> str:
    if mode is None:
        return governance_mode()
    raw = mode.strip().lower()
    if raw in VALID_GOVERNANCE_MODES:
        return raw
    return "shadow"


def _looks_temporary(content: str) -> bool:
    lowered = content.lower()
    return any(hint in lowered for hint in TEMPORARY_HINTS)


def _normalize_source_ref(source_ref: object) -> dict[str, str]:
    if not isinstance(source_ref, dict):
        return {}
    normalized: dict[str, str] = {}
    for key, value in source_ref.items():
        if not isinstance(key, str):
            continue
        if value is None:
            continue
        normalized[key] = str(value)
    return normalized


def _has_stable_source_ref(source_ref: dict[str, str]) -> bool:
    return all(source_ref.get(key, "").strip() for key in REQUIRED_SOURCE_REF_KEYS)


def _normalize_decision_payload(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Governed decision payload must be a dictionary")

    normalized = dict(payload)
    normalized["decision_id"] = str(normalized.get("decision_id") or "").strip()
    policy_mode = normalized.get("policy_mode")
    normalized["policy_mode"] = str(policy_mode or "").strip().lower()
    normalized["policy_version"] = str(normalized.get("policy_version") or "").strip()
    normalized["decision"] = str(normalized.get("decision") or "").strip()
    normalized["reason"] = str(normalized.get("reason") or "").strip()
    normalized["confidence"] = str(normalized.get("confidence") or "").strip()
    normalized["source_ref"] = _normalize_source_ref(normalized.get("source_ref"))
    blockers = normalized.get("blockers")
    if blockers is None:
        normalized["blockers"] = []
    elif isinstance(blockers, (list, tuple)):
        normalized["blockers"] = [str(blocker) for blocker in blockers]
    else:
        normalized["blockers"] = [str(blockers)]

    missing_fields = [
        field_name
        for field_name in REQUIRED_DECISION_FIELDS
        if not _has_required_decision_field(field_name, normalized.get(field_name))
    ]
    if missing_fields:
        raise ValueError(
            "Missing required decision fields: " + ", ".join(missing_fields)
        )
    if normalized["policy_mode"] not in VALID_GOVERNANCE_MODES:
        raise ValueError(f"Invalid policy_mode: {normalized['policy_mode']}")

    return normalized


def _normalize_listed_decision_payload(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Governed decision payload must be a dictionary")

    normalized = dict(payload)
    normalized["decision_id"] = str(normalized.get("decision_id") or "").strip()
    normalized["decision"] = str(normalized.get("decision") or "").strip()
    normalized["reason"] = str(normalized.get("reason") or "").strip()
    policy_mode = str(normalized.get("policy_mode") or "").strip().lower()
    normalized["policy_mode"] = (
        policy_mode if policy_mode in VALID_GOVERNANCE_MODES else ""
    )
    normalized["policy_version"] = str(normalized.get("policy_version") or "").strip()
    normalized["confidence"] = str(normalized.get("confidence") or "").strip()
    normalized["source_ref"] = _normalize_source_ref(normalized.get("source_ref"))
    blockers = normalized.get("blockers")
    if isinstance(blockers, (list, tuple)):
        normalized["blockers"] = [str(blocker) for blocker in blockers]
    elif blockers is None:
        normalized["blockers"] = []
    else:
        normalized["blockers"] = [str(blockers)]
    if not _has_complete_decision_contract(normalized):
        normalized["legacy_incomplete"] = True
    return normalized


def _is_listable_decision_payload(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    decision_id = str(payload.get("decision_id") or "").strip()
    decision = str(payload.get("decision") or "").strip()
    return bool(decision_id and decision)


def _has_required_decision_field(field_name: str, value: object) -> bool:
    if field_name == "source_ref":
        return isinstance(value, dict) and _has_stable_source_ref(value)
    if field_name == "blockers":
        return isinstance(value, list)
    return isinstance(value, str) and bool(value.strip())


def _has_complete_decision_contract(payload: dict) -> bool:
    return all(
        _has_required_decision_field(field_name, payload.get(field_name))
        for field_name in REQUIRED_DECISION_FIELDS
    )
