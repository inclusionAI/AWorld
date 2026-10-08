"""Framework-owned completion state, independent of an agent's final prose.

Control records contain no model/tool text. WorkingState is only a recovery
surface: restored records are scoped to the task and never prove completion.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import inspect
import json
import re
import uuid
from typing import Any

EXECUTION_STATE_KEY = "agent_execution_state"
EXECUTION_STATE_SCHEMA = "aworld.agent.execution-state/v2"
_LEGACY_EXECUTION_STATE_SCHEMA = "aworld.agent.execution-state/v1"
_STATUSES = {"running", "succeeded", "incomplete", "budget_exhausted"}
_BLOCKING_STATUSES = {"incomplete", "budget_exhausted"}
_STATUS_SEVERITY = {
    "succeeded": 0,
    "running": 1,
    "incomplete": 2,
    "budget_exhausted": 3,
}
_REASON_CODE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_MAX_BLOCKERS = 8
_MAX_RESOLUTION_IDS = 8
_MAX_RESOLUTION_EVENTS = 16
_MAX_RESOLUTION_SOURCES = 64
_BLOCKER_CATEGORIES = {
    "model_response",
    "independent_acceptance_review",
    "model_owned_review",
    "completion_contract",
    "validation",
    "candidate",
    "work",
    "budget",
}

_MODEL_RESPONSE_REASONS = {
    "model_output_truncated",
    "model_stream_ended_without_finish_reason",
    "model_output_interrupted",
    "malformed_tool_call_batch",
    "incomplete_tool_arguments",
    "invalid_tool_arguments",
    "reasoning_only_response",
    "empty_model_response",
    "context_window_exceeded",
    "transient_model_recovery_deadline_exhausted",
}
_RESOLUTION_CATEGORIES = {
    "complete_provider_tool_action": frozenset({"model_response"}),
    "complete_provider_final_action": frozenset({"model_response"}),
    "accepted_critic": frozenset({"independent_acceptance_review"}),
    "accepted_review": frozenset({"model_owned_review"}),
    "completion_contract_satisfied": frozenset(
        {"completion_contract", "validation", "candidate"}
    ),
    "candidate_advanced": frozenset({"candidate"}),
    "validation_passed": frozenset({"validation"}),
}


def state_context(context):
    manager = getattr(context, "event_manager", None)
    return getattr(manager, "context", None) or context


def _bounded_code(value: Any, fallback: str) -> tuple[str, str | None]:
    value = str(value or "").strip()
    if _REASON_CODE.fullmatch(value):
        return value, None
    digest = hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]
    return fallback, digest


def _scope_for(context, agent_id: str | None = None) -> dict[str, Any]:
    owner = state_context(context) if context is not None else None
    task_id = getattr(context, "task_id", None)
    task_epoch = getattr(context, "task_epoch", None)
    if task_id is None and owner is not None:
        task_id = getattr(owner, "task_id", None)
        task_epoch = getattr(owner, "task_epoch", None)
    return {
        "task_id": task_id,
        "task_epoch": task_epoch,
        "agent_id": agent_id,
    }


def _blocker_category(status: str, reason: str) -> str:
    if status == "budget_exhausted":
        return "budget"
    if reason in _MODEL_RESPONSE_REASONS or reason.startswith("model_response_"):
        return "model_response"
    if reason.startswith(("independent_acceptance_", "acceptance_critic_")):
        return "independent_acceptance_review"
    if reason.startswith("model_owned_review_"):
        return "model_owned_review"
    if reason.startswith("completion_contract_"):
        return "completion_contract"
    if reason.startswith("validation_"):
        return "validation"
    if reason.startswith(
        (
            "candidate_",
            "delivery_candidate_",
            "public_candidate_",
            "public_deliverable_",
            "required_artifact_",
        )
    ):
        return "candidate"
    return "work"


def _blocker_id(
    scope: dict[str, Any],
    *,
    revision: int,
    status: str,
    reason: str,
    occurrence_id: str,
) -> str:
    encoded = json.dumps(
        {
            "scope": scope,
            "revision": revision,
            "status": status,
            "reason": reason,
            "occurrence_id": occurrence_id,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def _new_occurrence_id() -> str:
    return uuid.uuid4().hex[:24]


def _event_source_id(context, agent_id: str) -> str:
    stream_id = getattr(context, "_llm_call_journal_stream_id", None)
    if isinstance(stream_id, str) and stream_id:
        source = f"journal:{stream_id}:{agent_id}"
        return hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
    key = f"execution_state_source:{agent_id}"
    context_info = getattr(context, "context_info", None)
    if hasattr(context_info, "get"):
        source_id = context_info.get(key)
        if isinstance(source_id, str) and _REASON_CODE.fullmatch(source_id):
            return source_id
        source_id = _new_occurrence_id()[:16]
        context_info[key] = source_id
        return source_id
    return _new_occurrence_id()[:16]


def _normalized_revision(value: Any, fallback: Any = 0) -> int:
    candidate = (
        value if isinstance(value, int) and not isinstance(value, bool) else fallback
    )
    return max(0, candidate) if isinstance(candidate, int) else 0


def _normalize_record(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    schema = value.get("schema_version")
    if schema not in {EXECUTION_STATE_SCHEMA, _LEGACY_EXECUTION_STATE_SCHEMA}:
        return None
    requested_status = value.get("status")
    if requested_status not in _STATUSES:
        return None
    raw_scope = value.get("scope") if isinstance(value.get("scope"), dict) else {}
    scope = {
        "task_id": raw_scope.get("task_id", value.get("task_id")),
        "task_epoch": raw_scope.get("task_epoch", value.get("task_epoch")),
        "agent_id": raw_scope.get("agent_id", value.get("agent_id")),
    }
    revision = _normalized_revision(
        value.get("revision"), value.get("work_state_revision", 0)
    )
    reason, reason_hash = _bounded_code(
        value.get("reason"), "unclassified_execution_state"
    )
    blockers: list[dict[str, Any]] = []
    raw_blockers = value.get("unresolved_blockers")
    if schema == EXECUTION_STATE_SCHEMA and isinstance(raw_blockers, list):
        for item in raw_blockers[-(_MAX_BLOCKERS * 2) :]:
            if not isinstance(item, dict):
                continue
            status = item.get("status")
            if status not in _BLOCKING_STATUSES:
                continue
            blocker_reason, blocker_reason_hash = _bounded_code(
                item.get("reason"), "unclassified_execution_blocker"
            )
            blocker_revision = _normalized_revision(item.get("revision"), revision)
            category, _ = _bounded_code(item.get("category"), "work")
            legacy_shared_review_category = category == "acceptance_review"
            if legacy_shared_review_category:
                # Rolling migration from the pre-authority-split v2 ledger.
                # The bounded reason, not the incoming category claim, selects
                # which acceptance authority may resolve this blocker.
                category = _blocker_category(status, blocker_reason)
            if category not in _BLOCKER_CATEGORIES:
                category = "work"
            occurrence_id, _ = _bounded_code(item.get("occurrence_id"), "")
            blocker_identifier, _ = _bounded_code(item.get("blocker_id"), "")
            if not occurrence_id:
                occurrence_id = (
                    "legacy-"
                    + hashlib.sha256(
                        (
                            blocker_identifier
                            or json.dumps(
                                {
                                    "scope": scope,
                                    "revision": blocker_revision,
                                    "status": status,
                                    "reason": blocker_reason,
                                },
                                sort_keys=True,
                            )
                        ).encode("utf-8")
                    ).hexdigest()[:16]
                )
            source_id, _ = _bounded_code(item.get("source_id"), "")
            if not source_id:
                source_id = (
                    occurrence_id.split(":", 1)[0]
                    if ":" in occurrence_id
                    else "legacy-source"
                )
            source_sequence = _normalized_revision(
                item.get("source_sequence"), blocker_revision
            )
            if not blocker_identifier:
                blocker_identifier = _blocker_id(
                    scope,
                    revision=blocker_revision,
                    status=status,
                    reason=blocker_reason,
                    occurrence_id=occurrence_id,
                )
            blocker = {
                "blocker_id": blocker_identifier,
                "occurrence_id": occurrence_id,
                "source_id": source_id,
                "source_sequence": source_sequence,
                "category": category,
                "status": status,
                "reason": blocker_reason,
                "revision": blocker_revision,
                "recoverable": bool(item.get("recoverable", True))
                and not (
                    legacy_shared_review_category
                    and category == "work"
                ),
            }
            if blocker_reason_hash:
                blocker["reason_hash"] = blocker_reason_hash
            blockers.append(blocker)
    elif requested_status in _BLOCKING_STATUSES:
        category = _blocker_category(requested_status, reason)
        occurrence_id = (
            "legacy-"
            + hashlib.sha256(
                json.dumps(
                    {
                        "scope": scope,
                        "revision": revision,
                        "status": requested_status,
                        "reason": reason,
                    },
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()[:16]
        )
        blocker = {
            "blocker_id": _blocker_id(
                scope,
                revision=revision,
                status=requested_status,
                reason=reason,
                occurrence_id=occurrence_id,
            ),
            "occurrence_id": occurrence_id,
            "source_id": "legacy-source",
            "source_sequence": revision,
            "category": category,
            "status": requested_status,
            "reason": reason,
            "revision": revision,
            "recoverable": bool(value.get("recoverable", True)),
        }
        if reason_hash:
            blocker["reason_hash"] = reason_hash
        blockers.append(blocker)

    resolutions: list[dict[str, Any]] = []
    raw_resolutions = value.get("resolution_evidence")
    if schema == EXECUTION_STATE_SCHEMA and isinstance(raw_resolutions, list):
        for item in raw_resolutions[-(_MAX_RESOLUTION_EVENTS * 2) :]:
            if not isinstance(item, dict):
                continue
            evidence_kind = item.get("evidence_kind")
            category = item.get("category")
            if (
                category == "acceptance_review"
                and evidence_kind in _RESOLUTION_CATEGORIES
            ):
                # Legacy accepted_critic evidence had one shared category.
                # Each current evidence kind now has exactly one authority.
                category = next(iter(_RESOLUTION_CATEGORIES[evidence_kind]))
            if (
                evidence_kind not in _RESOLUTION_CATEGORIES
                or category not in _RESOLUTION_CATEGORIES[evidence_kind]
            ):
                continue
            blocker_ids = []
            for blocker_id in item.get("blocker_ids", ()):
                blocker_id, _ = _bounded_code(blocker_id, "")
                if blocker_id and blocker_id not in blocker_ids:
                    blocker_ids.append(blocker_id)
            occurrence_id, _ = _bounded_code(item.get("occurrence_id"), "")
            if not occurrence_id:
                occurrence_id = (
                    "legacy-"
                    + hashlib.sha256(
                        json.dumps(item, sort_keys=True, default=str).encode("utf-8")
                    ).hexdigest()[:16]
                )
            blocker_refs = []
            for ref in item.get("blocker_refs", ()):
                if not isinstance(ref, dict):
                    continue
                blocker_id, _ = _bounded_code(ref.get("blocker_id"), "")
                source_id, _ = _bounded_code(ref.get("source_id"), "")
                source_sequence = _normalized_revision(ref.get("source_sequence"))
                if blocker_id and source_id:
                    blocker_refs.append(
                        {
                            "blocker_id": blocker_id,
                            "source_id": source_id,
                            "source_sequence": source_sequence,
                        }
                    )
            resolutions.append(
                {
                    "category": category,
                    "evidence_kind": evidence_kind,
                    "occurrence_id": occurrence_id,
                    "revision": _normalized_revision(item.get("revision"), revision),
                    "watermark_revision": _normalized_revision(
                        item.get("watermark_revision"), revision
                    ),
                    "blocker_ids": blocker_ids[-_MAX_RESOLUTION_IDS:],
                    "blocker_refs": blocker_refs[-_MAX_RESOLUTION_IDS:],
                }
            )

    resolution_watermarks: list[dict[str, Any]] = []
    raw_watermarks = value.get("resolution_watermarks")
    if schema == EXECUTION_STATE_SCHEMA and isinstance(raw_watermarks, list):
        for item in raw_watermarks[-(_MAX_RESOLUTION_SOURCES * 2) :]:
            if not isinstance(item, dict):
                continue
            category = item.get("category")
            source_id, _ = _bounded_code(item.get("source_id"), "")
            if category not in _BLOCKER_CATEGORIES or not source_id:
                continue
            resolution_watermarks.append(
                {
                    "category": category,
                    "source_id": source_id,
                    "through_sequence": _normalized_revision(
                        item.get("through_sequence")
                    ),
                }
            )
    resolution_watermark_digest, _ = _bounded_code(
        value.get("resolution_watermark_digest"), ""
    )

    last_event = value.get("last_event")
    if not isinstance(last_event, dict):
        last_event = {
            "revision": revision,
            "kind": "legacy_state"
            if schema == _LEGACY_EXECUTION_STATE_SCHEMA
            else "state",
            "requested_status": requested_status,
            "reason": reason,
            "occurrence_id": "legacy-event-"
            + hashlib.sha256(
                json.dumps(
                    {
                        "scope": scope,
                        "revision": revision,
                        "status": requested_status,
                        "reason": reason,
                    },
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()[:12],
        }
    else:
        raw_evidence_kind = last_event.get("evidence_kind")
        raw_occurrence_id, _ = _bounded_code(last_event.get("occurrence_id"), "")
        event_reason, _ = _bounded_code(
            last_event.get("reason"), "unclassified_execution_state"
        )
        event_status = last_event.get("requested_status")
        if event_status not in _STATUSES:
            event_status = requested_status
        event_kind, _ = _bounded_code(last_event.get("kind"), "state")
        last_event = {
            "revision": _normalized_revision(last_event.get("revision"), revision),
            "kind": event_kind,
            "requested_status": event_status,
            "reason": event_reason,
            "occurrence_id": raw_occurrence_id or "legacy-event",
        }
        if raw_evidence_kind in _RESOLUTION_CATEGORIES:
            last_event["evidence_kind"] = raw_evidence_kind

    normalized = {
        "schema_version": EXECUTION_STATE_SCHEMA,
        "scope": scope,
        "task_id": scope["task_id"],
        "task_epoch": scope["task_epoch"],
        "agent_id": scope["agent_id"],
        "revision": revision,
        "status": requested_status,
        "reason": reason,
        "recoverable": bool(
            value.get("recoverable", True) and requested_status in _BLOCKING_STATUSES
        ),
        "unresolved_blockers": blockers,
        "resolution_evidence": resolutions,
        "resolution_watermarks": resolution_watermarks,
        "resolution_watermark_overflow": bool(
            value.get("resolution_watermark_overflow", False)
        ),
        "resolution_watermark_digest": (resolution_watermark_digest or None),
        "last_event": last_event,
        "work_state_revision": _normalized_revision(
            value.get("work_state_revision"), 0
        ),
    }
    if reason_hash:
        normalized["reason_hash"] = reason_hash
    return normalized


def reconcile_execution_states(
    records: list[dict[str, Any] | None] | tuple[dict[str, Any] | None, ...],
    *,
    task_id: str | None,
    task_epoch: int | None,
    agent_id: str | None = None,
) -> dict[str, Any] | None:
    """Merge transported execution facts without allowing status promotion.

    Revisions order events within one shared task registry. Explicit blocker IDs
    provide causality when independently transported copies have tied revisions.
    """

    normalized = []
    for value in records:
        record = _normalize_record(value)
        if record is None:
            continue
        scope = record["scope"]
        if scope.get("task_id") != task_id or scope.get("task_epoch") != task_epoch:
            continue
        if agent_id is not None and scope.get("agent_id") != agent_id:
            continue
        normalized.append(record)
    if not normalized:
        return None

    if agent_id is None:
        agent_ids = {item["scope"].get("agent_id") for item in normalized}
        if len(agent_ids) == 1:
            agent_id = next(iter(agent_ids))
        else:
            # A task-level read has no authority to fuse independent Agent
            # scopes. Choose the most severe/newest projection fail closed.
            selected = max(
                normalized,
                key=lambda item: (
                    _STATUS_SEVERITY[item["status"]],
                    item["revision"],
                ),
            )
            agent_id = selected["scope"].get("agent_id")
            normalized = [
                item for item in normalized if item["scope"].get("agent_id") == agent_id
            ]

    resolution_events: list[dict[str, Any]] = []
    resolution_event_keys: set[tuple[Any, ...]] = set()
    for record in normalized:
        for resolution in record["resolution_evidence"]:
            event = dict(resolution)
            event["blocker_ids"] = list(resolution["blocker_ids"])
            event["blocker_refs"] = [
                dict(ref) for ref in resolution.get("blocker_refs", ())
            ]
            key = (
                event["category"],
                event["evidence_kind"],
                event["occurrence_id"],
                event["revision"],
                event["watermark_revision"],
                tuple(event["blocker_ids"]),
                tuple(
                    (
                        ref["blocker_id"],
                        ref["source_id"],
                        ref["source_sequence"],
                    )
                    for ref in event["blocker_refs"]
                ),
            )
            if key not in resolution_event_keys:
                resolution_event_keys.add(key)
                resolution_events.append(event)
    resolution_events.sort(
        key=lambda item: (
            item["revision"],
            item["watermark_revision"],
            item["category"],
            item["evidence_kind"],
            item["occurrence_id"],
        )
    )
    resolution_watermarks_by_key: dict[tuple[str, str], int] = {}
    for record in normalized:
        for watermark in record.get("resolution_watermarks", ()):
            key = (watermark["category"], watermark["source_id"])
            resolution_watermarks_by_key[key] = max(
                resolution_watermarks_by_key.get(key, 0),
                watermark["through_sequence"],
            )

    blockers_by_id: dict[str, dict[str, Any]] = {}
    for record in normalized:
        for blocker in record["unresolved_blockers"]:
            resolved = any(
                resolution["category"] == blocker["category"]
                and blocker["blocker_id"] in resolution["blocker_ids"]
                and resolution["revision"] > blocker["revision"]
                and resolution["watermark_revision"] >= blocker["revision"]
                for resolution in resolution_events
            ) or (
                resolution_watermarks_by_key.get(
                    (blocker["category"], blocker["source_id"]), -1
                )
                >= blocker["source_sequence"]
            )
            if resolved:
                continue
            current = blockers_by_id.get(blocker["blocker_id"])
            if current is None or (
                _STATUS_SEVERITY[blocker["status"]],
                blocker["revision"],
                not blocker["recoverable"],
                blocker["reason"],
            ) > (
                _STATUS_SEVERITY[current["status"]],
                current["revision"],
                not current["recoverable"],
                current["reason"],
            ):
                blockers_by_id[blocker["blocker_id"]] = dict(blocker)

    all_blockers = sorted(
        blockers_by_id.values(),
        key=lambda item: (
            -_STATUS_SEVERITY[item["status"]],
            -item["revision"],
            item["category"],
            item["blocker_id"],
        ),
    )
    # Compact every causally gap-free tombstone into a monotonic per-source
    # watermark. Recent events remain as bounded telemetry, but correctness no
    # longer depends on a separate 16-event window.
    for resolution in resolution_events:
        for ref in resolution.get("blocker_refs", ()):
            if any(
                blocker["category"] == resolution["category"]
                and blocker["source_id"] == ref["source_id"]
                and blocker["source_sequence"] <= ref["source_sequence"]
                for blocker in all_blockers
            ):
                # A lower unresolved occurrence creates a gap, so a high-water
                # compaction would be unsafe. Dropping the tombstone is
                # deliberately fail-closed; the recent explicit event remains.
                continue
            key = (resolution["category"], ref["source_id"])
            resolution_watermarks_by_key[key] = max(
                resolution_watermarks_by_key.get(key, 0),
                ref["source_sequence"],
            )
    retained_resolution_events = resolution_events[-_MAX_RESOLUTION_EVENTS:]

    watermark_items = sorted(resolution_watermarks_by_key.items())
    incoming_watermark_overflow = any(
        record.get("resolution_watermark_overflow") is True for record in normalized
    )
    incoming_digests = sorted(
        {
            record.get("resolution_watermark_digest")
            for record in normalized
            if isinstance(record.get("resolution_watermark_digest"), str)
        }
    )
    watermark_digest = hashlib.sha256(
        json.dumps(
            {
                "incoming": incoming_digests,
                "watermarks": [
                    [category, source_id, through_sequence]
                    for (category, source_id), through_sequence in watermark_items
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:24]
    watermark_overflow = bool(
        incoming_watermark_overflow or len(watermark_items) > _MAX_RESOLUTION_SOURCES
    )
    retained_watermark_items = watermark_items[-_MAX_RESOLUTION_SOURCES:]
    if watermark_overflow:
        overflow_revision = max(record["revision"] for record in normalized)
        overflow_occurrence = f"resolution-overflow:{watermark_digest}"
        all_blockers.append(
            {
                "blocker_id": _blocker_id(
                    {
                        "task_id": task_id,
                        "task_epoch": task_epoch,
                        "agent_id": agent_id,
                    },
                    revision=overflow_revision,
                    status="incomplete",
                    reason="resolution_compaction_overflow",
                    occurrence_id=overflow_occurrence,
                ),
                "occurrence_id": overflow_occurrence,
                "source_id": "resolution-overflow",
                "source_sequence": overflow_revision,
                "category": "work",
                "status": "incomplete",
                "reason": "resolution_compaction_overflow",
                "revision": overflow_revision,
                "recoverable": False,
            }
        )
        all_blockers.sort(
            key=lambda item: (
                -_STATUS_SEVERITY[item["status"]],
                -item["revision"],
                item["category"],
                item["blocker_id"],
            )
        )
    blockers = all_blockers[:_MAX_BLOCKERS]
    if len(all_blockers) > _MAX_BLOCKERS:
        retained = blockers[: _MAX_BLOCKERS - 1]
        compacted = all_blockers[_MAX_BLOCKERS - 1 :]
        compacted_ids = sorted(item["blocker_id"] for item in compacted)
        occurrence_id = (
            "overflow-"
            + hashlib.sha256("|".join(compacted_ids).encode("utf-8")).hexdigest()[:16]
        )
        categories = {item["category"] for item in compacted}
        overflow_status = max(
            (item["status"] for item in compacted),
            key=_STATUS_SEVERITY.__getitem__,
        )
        overflow = {
            "blocker_id": _blocker_id(
                {"task_id": task_id, "task_epoch": task_epoch, "agent_id": agent_id},
                revision=max(item["revision"] for item in compacted),
                status=overflow_status,
                reason="execution_blocker_overflow",
                occurrence_id=occurrence_id,
            ),
            "occurrence_id": occurrence_id,
            "source_id": occurrence_id,
            "source_sequence": max(item["revision"] for item in compacted),
            "category": next(iter(categories)) if len(categories) == 1 else "work",
            "status": overflow_status,
            "reason": "execution_blocker_overflow",
            "revision": max(item["revision"] for item in compacted),
            "recoverable": False,
        }
        blockers = [*retained, overflow]
    last_event = max(
        (record["last_event"] for record in normalized),
        key=lambda item: (
            item["revision"],
            _STATUS_SEVERITY[item["requested_status"]],
            item["reason"],
            item.get("occurrence_id", ""),
        ),
    )
    if blockers:
        primary = blockers[0]
        status = primary["status"]
        reason = primary["reason"]
        recoverable = bool(primary["recoverable"])
    else:
        status = last_event["requested_status"]
        reason = last_event["reason"]
        recoverable = False
    scope = {"task_id": task_id, "task_epoch": task_epoch, "agent_id": agent_id}
    result = {
        "schema_version": EXECUTION_STATE_SCHEMA,
        "scope": scope,
        "task_id": task_id,
        "task_epoch": task_epoch,
        "agent_id": agent_id,
        "revision": max(record["revision"] for record in normalized),
        "status": status,
        "reason": reason,
        "recoverable": recoverable,
        "unresolved_blockers": blockers,
        "resolution_evidence": retained_resolution_events,
        "resolution_watermarks": [
            {
                "category": category,
                "source_id": source_id,
                "through_sequence": through_sequence,
            }
            for (category, source_id), through_sequence in retained_watermark_items
        ],
        "resolution_watermark_overflow": watermark_overflow,
        "resolution_watermark_digest": watermark_digest,
        "last_event": deepcopy(last_event),
        "work_state_revision": max(
            record["work_state_revision"] for record in normalized
        ),
    }
    return result


def _context_records(context, agent_id: str | None) -> list[dict[str, Any]]:
    if context is None:
        return []
    contexts = [context]
    owner = state_context(context)
    if owner is not None and owner is not context:
        contexts.append(owner)
    values: list[dict[str, Any]] = []
    discovered_agent_id = agent_id
    for target in contexts:
        context_info = getattr(target, "context_info", None)
        if not hasattr(context_info, "get"):
            continue
        generic = context_info.get(EXECUTION_STATE_KEY)
        if isinstance(generic, dict):
            values.append(generic)
            if discovered_agent_id is None and isinstance(generic.get("agent_id"), str):
                discovered_agent_id = generic["agent_id"]
        if discovered_agent_id:
            scoped = context_info.get(f"{EXECUTION_STATE_KEY}:{discovered_agent_id}")
            if isinstance(scoped, dict):
                values.append(scoped)
    if discovered_agent_id:
        for target in contexts:
            reader = getattr(target, "read_task_runtime_state", None)
            if callable(reader):
                shared = reader(discovered_agent_id, EXECUTION_STATE_KEY)
                if isinstance(shared, dict):
                    values.append(shared)
            getter = getattr(target, "get", None)
            if callable(getter):
                for key in (
                    EXECUTION_STATE_KEY,
                    f"{EXECUTION_STATE_KEY}:{discovered_agent_id}",
                ):
                    try:
                        persisted = getter(key)
                    except TypeError:
                        persisted = None
                    if isinstance(persisted, dict):
                        values.append(persisted)
    return values


def project_execution_state(context, record: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_record(record)
    if normalized is None:
        raise ValueError("invalid execution state")
    scope = normalized["scope"]
    expected = _scope_for(context, scope.get("agent_id"))
    if scope != expected:
        raise ValueError("execution state scope mismatch")
    owner = state_context(context)
    for target in (context, owner):
        if target is None:
            continue
        target_scope = _scope_for(target, scope.get("agent_id"))
        if target_scope != scope:
            continue
        target.context_info[EXECUTION_STATE_KEY] = deepcopy(normalized)
        target.context_info[f"{EXECUTION_STATE_KEY}:{scope['agent_id']}"] = deepcopy(
            normalized
        )
    writer = getattr(context, "write_task_runtime_state", None)
    if callable(writer):
        writer(scope["agent_id"], EXECUTION_STATE_KEY, normalized)
    put = getattr(context, "put", None)
    if callable(put):
        put(EXECUTION_STATE_KEY, deepcopy(normalized))
        put(f"{EXECUTION_STATE_KEY}:{scope['agent_id']}", deepcopy(normalized))
    return deepcopy(normalized)


def _record_event(
    context,
    agent_id: str,
    *,
    status: str,
    reason: str,
    recoverable: bool,
    evidence_kind: str | None = None,
    observed_revision: int | None = None,
    observed_blocker_ids: tuple[str, ...] | list[str] | None = None,
) -> dict[str, Any]:
    if status not in _STATUSES:
        raise ValueError("unsupported execution status")
    scope = _scope_for(context, agent_id)
    reason, reason_hash = _bounded_code(
        reason,
        (
            "unclassified_execution_blocker"
            if status in _BLOCKING_STATUSES
            else "unclassified_execution_state"
        ),
    )
    existing = _context_records(context, agent_id)
    source_id = _event_source_id(context, agent_id)

    def update(current):
        records = [*existing, current]
        base = reconcile_execution_states(
            records,
            task_id=scope["task_id"],
            task_epoch=scope["task_epoch"],
            agent_id=agent_id,
        )
        revision = (base["revision"] if base is not None else 0) + 1
        event_occurrence_id = f"{source_id}:{revision}"
        blockers = list(base["unresolved_blockers"] if base is not None else [])
        resolutions = list(base["resolution_evidence"] if base is not None else [])
        resolution_watermarks = list(
            base["resolution_watermarks"] if base is not None else []
        )
        resolution_watermark_overflow = bool(
            base is not None and base.get("resolution_watermark_overflow") is True
        )
        resolution_watermark_digest = (
            base.get("resolution_watermark_digest") if base is not None else None
        )
        if status in _BLOCKING_STATUSES:
            category = _blocker_category(status, reason)
            repeated_blocker = max(
                (
                    blocker
                    for blocker in blockers
                    if blocker["category"] == category
                    and blocker["status"] == status
                    and blocker["reason"] == reason
                ),
                key=lambda blocker: blocker["revision"],
                default=None,
            )
            blocker_occurrence_id = (
                repeated_blocker["occurrence_id"]
                if repeated_blocker is not None
                else event_occurrence_id
            )
            blocker_id = (
                repeated_blocker["blocker_id"]
                if repeated_blocker is not None
                else _blocker_id(
                    scope,
                    revision=revision,
                    status=status,
                    reason=reason,
                    occurrence_id=blocker_occurrence_id,
                )
            )
            blockers = [
                blocker for blocker in blockers if blocker["blocker_id"] != blocker_id
            ]
            blocker = {
                "blocker_id": blocker_id,
                "occurrence_id": blocker_occurrence_id,
                "source_id": (
                    repeated_blocker["source_id"]
                    if repeated_blocker is not None
                    else source_id
                ),
                "source_sequence": (
                    repeated_blocker["source_sequence"]
                    if repeated_blocker is not None
                    else revision
                ),
                "category": category,
                "status": status,
                "reason": reason,
                "revision": revision,
                "recoverable": bool(recoverable),
            }
            if reason_hash:
                blocker["reason_hash"] = reason_hash
            blockers.append(blocker)
        elif evidence_kind is not None:
            allowed_categories = _RESOLUTION_CATEGORIES[evidence_kind]
            explicitly_observed = (
                {
                    blocker_id
                    for blocker_id in observed_blocker_ids
                    if isinstance(blocker_id, str)
                }
                if observed_blocker_ids is not None
                else None
            )
            watermark_revision = (
                _normalized_revision(observed_revision)
                if observed_revision is not None
                else base["revision"]
                if base is not None
                else 0
            )
            for category in sorted(allowed_categories):
                compatible_blockers = [
                    blocker
                    for blocker in blockers
                    if blocker["category"] == category
                    and (
                        category
                        not in {
                            "independent_acceptance_review",
                            "model_owned_review",
                        }
                        or blocker.get("recoverable") is True
                    )
                    and blocker["revision"] <= watermark_revision
                    and (
                        explicitly_observed is None
                        or blocker["blocker_id"] in explicitly_observed
                    )
                ]
                # One evidence event resolves one causal blocker per typed
                # requirement category. A later B action must never erase an
                # independent earlier A blocker merely because both are, for
                # example, model-response failures.
                bound_blocker = (
                    max(
                        compatible_blockers,
                        key=lambda blocker: (
                            blocker["revision"],
                            blocker["blocker_id"],
                        ),
                    )
                    if compatible_blockers
                    else None
                )
                observed_ids = (
                    [bound_blocker["blocker_id"]] if bound_blocker is not None else []
                )
                resolutions.append(
                    {
                        "category": category,
                        "evidence_kind": evidence_kind,
                        "occurrence_id": event_occurrence_id,
                        "revision": revision,
                        "watermark_revision": watermark_revision,
                        "blocker_ids": observed_ids,
                        "blocker_refs": (
                            [
                                {
                                    "blocker_id": bound_blocker["blocker_id"],
                                    "source_id": bound_blocker["source_id"],
                                    "source_sequence": bound_blocker["source_sequence"],
                                }
                            ]
                            if bound_blocker is not None
                            else []
                        ),
                    }
                )
        raw = {
            "schema_version": EXECUTION_STATE_SCHEMA,
            "scope": scope,
            "task_id": scope["task_id"],
            "task_epoch": scope["task_epoch"],
            "agent_id": agent_id,
            "revision": revision,
            "status": status,
            "reason": reason,
            "recoverable": bool(recoverable and status in _BLOCKING_STATUSES),
            "unresolved_blockers": blockers,
            "resolution_evidence": resolutions,
            "resolution_watermarks": resolution_watermarks,
            "resolution_watermark_overflow": resolution_watermark_overflow,
            "resolution_watermark_digest": resolution_watermark_digest,
            "last_event": {
                "revision": revision,
                "kind": "resolution" if evidence_kind is not None else "state",
                "requested_status": status,
                "reason": reason,
                "occurrence_id": event_occurrence_id,
                **({"evidence_kind": evidence_kind} if evidence_kind else {}),
            },
            "work_state_revision": 0,
        }
        owner = state_context(context)
        context_info = getattr(owner, "context_info", {}) if owner is not None else {}
        ledger = context_info.get(f"adaptive_work_state:{agent_id}")
        raw["work_state_revision"] = (
            ledger.get("revision", 0) if isinstance(ledger, dict) else 0
        )
        return reconcile_execution_states(
            [raw],
            task_id=scope["task_id"],
            task_epoch=scope["task_epoch"],
            agent_id=agent_id,
        )

    updater = getattr(context, "update_and_project_task_runtime_state", None)
    if callable(updater):
        record = updater(
            agent_id,
            EXECUTION_STATE_KEY,
            update,
            lambda value: project_execution_state(context, value),
        )
    else:
        updater = getattr(context, "update_task_runtime_state", None)
        if callable(updater):
            record = updater(agent_id, EXECUTION_STATE_KEY, update)
        else:
            record = update(None)
        project_execution_state(context, record)
    return deepcopy(record)


def record_execution_state(
    context, agent_id: str, status: str, reason: str, recoverable: bool = True
) -> dict[str, Any]:
    return _record_event(
        context,
        agent_id,
        status=status,
        reason=reason,
        recoverable=recoverable,
    )


def record_execution_resolution(
    context,
    agent_id: str,
    *,
    evidence_kind: str,
    status: str = "running",
    reason: str,
    observation: dict[str, Any],
) -> dict[str, Any]:
    """Resolve only blockers causally observed by allowlisted typed evidence."""

    if evidence_kind not in _RESOLUTION_CATEGORIES:
        raise ValueError("unsupported execution resolution evidence")
    if status not in {"running", "succeeded"}:
        raise ValueError("resolution status must be running or succeeded")
    if not isinstance(observation, dict):
        raise ValueError("execution resolution observation must be a mapping")
    observed_revision = _normalized_revision(observation.get("revision"))
    raw_ids = observation.get("blocker_ids")
    if not isinstance(raw_ids, (list, tuple)):
        raise ValueError("execution resolution observation is missing blocker IDs")
    observed_blocker_ids = [
        blocker_id
        for blocker_id in raw_ids[:_MAX_RESOLUTION_IDS]
        if isinstance(blocker_id, str) and _REASON_CODE.fullmatch(blocker_id)
    ]
    return _record_event(
        context,
        agent_id,
        status=status,
        reason=reason,
        recoverable=False,
        evidence_kind=evidence_kind,
        observed_revision=observed_revision,
        observed_blocker_ids=observed_blocker_ids,
    )


def execution_resolution_observation(context, agent_id: str) -> dict[str, Any]:
    """Capture the bounded blocker watermark visible at an action boundary."""

    state = get_execution_state(context, agent_id=agent_id)
    if state is None:
        return {"revision": 0, "blocker_ids": []}
    return {
        "revision": state["revision"],
        "blocker_ids": [
            blocker["blocker_id"]
            for blocker in state["unresolved_blockers"][-_MAX_RESOLUTION_IDS:]
        ],
    }


def get_execution_state(context, agent_id: str | None = None) -> dict[str, Any] | None:
    if context is None:
        return None
    scope = _scope_for(context, agent_id)
    records = _context_records(context, agent_id)
    return reconcile_execution_states(
        records,
        task_id=scope["task_id"],
        task_epoch=scope["task_epoch"],
        agent_id=agent_id,
    )


def get_execution_states(context) -> list[dict[str, Any]]:
    """Return every task-scoped Agent state visible across transport surfaces."""

    if context is None:
        return []
    contexts = [context]
    owner = state_context(context)
    if owner is not None and owner is not context:
        contexts.append(owner)
    agent_ids: set[str] = set()
    for target in contexts:
        context_info = getattr(target, "context_info", None)
        if not hasattr(context_info, "items"):
            continue
        for key, value in context_info.items():
            if not isinstance(value, dict):
                continue
            if key == EXECUTION_STATE_KEY and isinstance(value.get("agent_id"), str):
                agent_ids.add(value["agent_id"])
            elif isinstance(key, str) and key.startswith(EXECUTION_STATE_KEY + ":"):
                scoped_agent_id = key[len(EXECUTION_STATE_KEY) + 1 :]
                if scoped_agent_id:
                    agent_ids.add(scoped_agent_id)
    states = []
    for scoped_agent_id in sorted(agent_ids):
        state = get_execution_state(context, agent_id=scoped_agent_id)
        if state is not None:
            states.append(state)
    if not states:
        state = get_execution_state(context)
        if state is not None:
            states.append(state)
    return states


async def checkpoint_execution_state(context) -> None:
    """Use the existing durable checkpoint without changing cache epochs."""
    owner = state_context(context)
    snapshot = getattr(owner, "snapshot", None)
    if callable(snapshot):
        parameters = inspect.signature(snapshot).parameters
        kwargs = {"cache_boundary": False}
        supports_lightweight = "checkpoint_only" in parameters or any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values()
        )
        if supports_lightweight:
            kwargs["checkpoint_only"] = True
        elif getattr(owner, "checkpoint_repository", None) is None or not getattr(
            owner, "session_id", None
        ):
            # Plain Context without a repository has no durable destination.
            # Keep the record in memory rather than fabricate checkpoint success.
            owner.context_info["work_state_checkpoint_status"] = "memory_only"
            return
        checkpoint = await snapshot(**kwargs)
        if not supports_lightweight:
            # Base Context historically logs write errors and still returns a
            # Checkpoint object. Read it back before acknowledging persistence.
            repository = getattr(owner, "checkpoint_repository", None)
            readback = getattr(repository, "aget", None)
            checkpoint_id = getattr(checkpoint, "id", None)
            if (
                callable(readback)
                and checkpoint_id
                and await readback(checkpoint_id) is None
            ):
                owner.context_info["work_state_checkpoint_status"] = "checkpoint_failed"
                raise RuntimeError("work progress checkpoint was not persisted")
        owner.context_info["work_state_checkpoint_status"] = "checkpointed"
