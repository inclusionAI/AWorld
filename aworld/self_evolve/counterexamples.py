# coding: utf-8
# Copyright (c) 2025 inclusionAI.

"""Payload-free counterexample envelopes for candidate repair feedback."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from aworld.self_evolve.sanitization import sanitize_text


REPLAY_COUNTEREXAMPLE_SCHEMA_VERSION = "aworld.replay.counterexample.v1"
REPLAY_COUNTEREXAMPLE_FIELDS = (
    "schema_version",
    "sequence",
    "failure_code",
    "owner",
    "stage",
    "scope",
    "category",
    "state_before",
    "trigger",
    "tool_name",
    "action_name",
    "action_fingerprint",
    "manifest_entry_count",
    "artifact_file_count",
    "artifact_file_limit",
    "artifact_bytes",
    "artifact_byte_limit",
    "tool_call_attempt_count",
    "consecutive_failure_count",
    "observed_endpoint_count",
    "undeclared_endpoint_count",
    "control_action_count",
    "occurrence_count",
    "semantic_key",
    "constraint_ids",
    "required_transition",
)
_STAGE_TRANSITIONS = {
    "candidate_generation": "repair_candidate_package",
    "capability_compile": "repair_replay_capability_contract",
    "capability_preflight": "satisfy_candidate_capability_preflight",
    "adaptation": "repair_replay_adaptation_contract",
    "task_rollout": "repair_candidate_task_behavior",
    "evaluation": "satisfy_failed_evaluation_gate",
    "result_normalization": "repair_candidate_result_contract",
    "legacy_import": "migrate_candidate_failure_contract",
    "apply": "satisfy_apply_contract",
}
_INTEGER_FIELDS = frozenset(
    {
        "sequence",
        "manifest_entry_count",
        "artifact_file_count",
        "artifact_file_limit",
        "artifact_bytes",
        "artifact_byte_limit",
        "tool_call_attempt_count",
        "consecutive_failure_count",
        "observed_endpoint_count",
        "undeclared_endpoint_count",
        "control_action_count",
        "occurrence_count",
    }
)


@dataclass(frozen=True)
class ReplayCounterexampleEnvelope:
    failure_code: str
    stage: str
    state_before: str
    trigger: str
    required_transition: str
    sequence: int = 1
    owner: str = "candidate"
    scope: str | None = None
    category: str | None = None
    tool_name: str | None = None
    action_name: str | None = None
    action_fingerprint: str | None = None
    manifest_entry_count: int | None = None
    artifact_file_count: int | None = None
    artifact_file_limit: int | None = None
    artifact_bytes: int | None = None
    artifact_byte_limit: int | None = None
    tool_call_attempt_count: int | None = None
    consecutive_failure_count: int | None = None
    observed_endpoint_count: int | None = None
    undeclared_endpoint_count: int | None = None
    control_action_count: int | None = None
    occurrence_count: int = 1
    semantic_key: str | None = None
    constraint_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.failure_code.strip():
            raise ValueError("counterexample failure_code is required")
        if not self.stage.strip():
            raise ValueError("counterexample stage is required")
        if not self.required_transition.strip():
            raise ValueError("counterexample required_transition is required")
        if self.owner != "candidate":
            raise ValueError("repair counterexample owner must be candidate")
        for field_name in _INTEGER_FIELDS:
            value = getattr(self, field_name, None)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                raise ValueError(f"counterexample {field_name} is invalid")

    def to_dict(self) -> dict[str, object]:
        raw: dict[str, object] = {
            "schema_version": REPLAY_COUNTEREXAMPLE_SCHEMA_VERSION,
            "sequence": self.sequence,
            "failure_code": self.failure_code,
            "owner": self.owner,
            "stage": self.stage,
            "scope": self.scope,
            "category": self.category,
            "state_before": self.state_before,
            "trigger": self.trigger,
            "tool_name": self.tool_name,
            "action_name": self.action_name,
            "action_fingerprint": self.action_fingerprint,
            "manifest_entry_count": self.manifest_entry_count,
            "artifact_file_count": self.artifact_file_count,
            "artifact_file_limit": self.artifact_file_limit,
            "artifact_bytes": self.artifact_bytes,
            "artifact_byte_limit": self.artifact_byte_limit,
            "tool_call_attempt_count": self.tool_call_attempt_count,
            "consecutive_failure_count": self.consecutive_failure_count,
            "observed_endpoint_count": self.observed_endpoint_count,
            "undeclared_endpoint_count": self.undeclared_endpoint_count,
            "control_action_count": self.control_action_count,
            "occurrence_count": self.occurrence_count,
            "semantic_key": self.semantic_key,
            "constraint_ids": list(self.constraint_ids),
            "required_transition": self.required_transition,
        }
        return {key: value for key, value in raw.items() if value not in (None, (), [])}

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, object],
    ) -> "ReplayCounterexampleEnvelope":
        if value.get("schema_version") != REPLAY_COUNTEREXAMPLE_SCHEMA_VERSION:
            raise ValueError("counterexample schema version is unsupported")
        kwargs: dict[str, Any] = {}
        for field_name in _INTEGER_FIELDS:
            raw = value.get(field_name)
            if raw is None:
                continue
            if isinstance(raw, bool):
                raise ValueError(f"counterexample {field_name} is invalid")
            if isinstance(raw, int):
                parsed = raw
            elif isinstance(raw, float) and raw.is_integer():
                parsed = int(raw)
            elif isinstance(raw, str) and re.fullmatch(r"0|[1-9][0-9]*", raw):
                # Compatibility for reports written before typed
                # counterexamples were exempted from the diagnostic depth
                # stringification boundary.  Only canonical non-negative
                # decimal integers are migrated.
                parsed = int(raw)
            else:
                raise ValueError(f"counterexample {field_name} is invalid")
            if parsed < 0:
                raise ValueError(f"counterexample {field_name} is invalid")
            kwargs[field_name] = parsed
        constraints = value.get("constraint_ids")
        return cls(
            failure_code=sanitize_text(
                value.get("failure_code") or "", max_chars=96
            ),
            stage=sanitize_text(value.get("stage") or "", max_chars=64),
            state_before=sanitize_text(
                value.get("state_before") or "", max_chars=64
            ),
            trigger=sanitize_text(value.get("trigger") or "", max_chars=96),
            required_transition=sanitize_text(
                value.get("required_transition") or "",
                max_chars=160,
            ),
            owner=sanitize_text(value.get("owner") or "candidate", max_chars=32),
            scope=_optional_text(value.get("scope"), 64),
            category=_optional_text(value.get("category"), 96),
            tool_name=_optional_text(value.get("tool_name"), 128),
            action_name=_optional_text(value.get("action_name"), 128),
            action_fingerprint=_optional_text(
                value.get("action_fingerprint"), 80
            ),
            semantic_key=_optional_text(value.get("semantic_key"), 160),
            constraint_ids=_string_tuple(constraints, max_items=16),
            **kwargs,
        )


def candidate_failure_counterexample(
    value: Mapping[str, object],
    *,
    sequence: int = 1,
) -> ReplayCounterexampleEnvelope | None:
    """Create a generic envelope from one repairable candidate failure event."""

    owner = str(value.get("owner") or value.get("failure_owner") or "")
    repairable = value.get("repairable")
    if owner != "candidate" or repairable is False:
        return None
    failure_code = str(value.get("code") or value.get("failure_code") or "")
    stage = str(value.get("stage") or value.get("failure_stage") or "")
    if not failure_code or not stage:
        return None
    diagnostics = value.get("diagnostics")
    constraint_ids = _constraint_ids(
        diagnostics if isinstance(diagnostics, Mapping) else value
    )
    return ReplayCounterexampleEnvelope(
        sequence=max(1, sequence),
        failure_code=sanitize_text(failure_code, max_chars=96),
        owner="candidate",
        stage=sanitize_text(stage, max_chars=64),
        scope=_optional_text(value.get("scope") or value.get("failure_scope"), 64),
        category=_optional_text(value.get("category"), 96),
        state_before=sanitize_text(
            value.get("category") or stage,
            max_chars=64,
        ),
        trigger="typed_candidate_failure",
        occurrence_count=_positive_occurrence(value.get("occurrence_count")),
        semantic_key=_optional_text(value.get("semantic_key"), 160),
        constraint_ids=constraint_ids,
        required_transition=_STAGE_TRANSITIONS.get(
            stage,
            "satisfy_candidate_failure_contract",
        ),
    )


def normalize_counterexample(
    value: object,
) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    try:
        return ReplayCounterexampleEnvelope.from_dict(value).to_dict()
    except (TypeError, ValueError):
        return None


def _constraint_ids(value: Mapping[str, object]) -> tuple[str, ...]:
    result: list[str] = []
    pending: list[tuple[object, int]] = [(value, 0)]
    visited = 0
    while pending and visited < 128 and len(result) < 16:
        current, depth = pending.pop()
        visited += 1
        if depth > 4:
            continue
        if isinstance(current, Mapping):
            for key, nested in current.items():
                if key in {
                    "constraint_id",
                    "policy_id",
                    "requirement_identity_digest",
                    "contract_identity_digest",
                } and isinstance(nested, str) and nested.strip():
                    bounded = sanitize_text(nested, max_chars=160)
                    if bounded not in result:
                        result.append(bounded)
                elif key == "constraint_ids" and isinstance(nested, Sequence):
                    for item in nested[:16]:
                        if isinstance(item, str) and item.strip():
                            bounded = sanitize_text(item, max_chars=160)
                            if bounded not in result:
                                result.append(bounded)
                elif isinstance(nested, (Mapping, list, tuple)):
                    pending.append((nested, depth + 1))
        elif isinstance(current, (list, tuple)):
            pending.extend((nested, depth + 1) for nested in current[:32])
    return tuple(result)


def _optional_text(value: object, max_chars: int) -> str | None:
    if value is None:
        return None
    text = sanitize_text(value, max_chars=max_chars)
    return text if text else None


def _string_tuple(value: object, *, max_items: int) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(
        sanitize_text(item, max_chars=160)
        for item in value[:max_items]
        if isinstance(item, str) and item.strip()
    )


def _positive_occurrence(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 1
    return max(1, int(value))
