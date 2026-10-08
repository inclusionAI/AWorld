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

_MODEL_RESPONSE_REASONS = {
    "model_output_truncated",
    "model_stream_ended_without_finish_reason",
    "model_output_interrupted",
    "malformed_tool_call_batch",
    "incomplete_tool_arguments",
    "invalid_tool_arguments",
    "reasoning_only_response",
    "context_window_exceeded",
    "transient_model_recovery_deadline_exhausted",
}
_RESOLUTION_CATEGORIES = {
    "complete_provider_tool_action": frozenset({"model_response"}),
    "complete_provider_final_action": frozenset({"model_response"}),
    "accepted_critic": frozenset({"acceptance_review"}),
    "completion_contract_satisfied": frozenset(
        {"completion_contract", "validation", "work"}
    ),
    "candidate_advanced": frozenset({"work"}),
    "validation_passed": frozenset({"validation", "work"}),
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
        return "acceptance_review"
    if reason.startswith("completion_contract_"):
        return "completion_contract"
    if reason.startswith("validation_"):
        return "validation"
    return "work"


def _blocker_id(
    scope: dict[str, Any], *, revision: int, status: str, reason: str
) -> str:
    encoded = json.dumps(
        {
            "scope": scope,
            "revision": revision,
            "status": status,
            "reason": reason,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


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
        for item in raw_blockers[: _MAX_BLOCKERS * 2]:
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
            if category not in {
                "model_response",
                "acceptance_review",
                "completion_contract",
                "validation",
                "work",
                "budget",
            }:
                category = "work"
            blocker_identifier, _ = _bounded_code(item.get("blocker_id"), "")
            if not blocker_identifier:
                blocker_identifier = _blocker_id(
                    scope,
                    revision=blocker_revision,
                    status=status,
                    reason=blocker_reason,
                )
            blocker = {
                "blocker_id": blocker_identifier,
                "category": category,
                "status": status,
                "reason": blocker_reason,
                "revision": blocker_revision,
                "recoverable": bool(item.get("recoverable", True)),
            }
            if blocker_reason_hash:
                blocker["reason_hash"] = blocker_reason_hash
            blockers.append(blocker)
    elif requested_status in _BLOCKING_STATUSES:
        category = _blocker_category(requested_status, reason)
        blocker = {
            "blocker_id": _blocker_id(
                scope, revision=revision, status=requested_status, reason=reason
            ),
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
        for item in raw_resolutions[:_MAX_RESOLUTION_EVENTS]:
            if not isinstance(item, dict):
                continue
            evidence_kind = item.get("evidence_kind")
            category = item.get("category")
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
            resolutions.append(
                {
                    "category": category,
                    "evidence_kind": evidence_kind,
                    "revision": _normalized_revision(item.get("revision"), revision),
                    "watermark_revision": _normalized_revision(
                        item.get("watermark_revision"), revision
                    ),
                    "blocker_ids": blocker_ids[-_MAX_RESOLUTION_IDS:],
                }
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
        }
    else:
        raw_evidence_kind = last_event.get("evidence_kind")
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
            key = (
                event["category"],
                event["evidence_kind"],
                event["revision"],
                event["watermark_revision"],
                tuple(event["blocker_ids"]),
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
        )
    )
    resolution_events = resolution_events[-_MAX_RESOLUTION_EVENTS:]

    blockers_by_category: dict[str, dict[str, Any]] = {}
    for record in normalized:
        for blocker in record["unresolved_blockers"]:
            resolved = any(
                resolution["category"] == blocker["category"]
                and blocker["blocker_id"] in resolution["blocker_ids"]
                and resolution["revision"] > blocker["revision"]
                and resolution["watermark_revision"] >= blocker["revision"]
                for resolution in resolution_events
            )
            if resolved:
                continue
            current = blockers_by_category.get(blocker["category"])
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
                blockers_by_category[blocker["category"]] = dict(blocker)

    blockers = sorted(
        blockers_by_category.values(),
        key=lambda item: (
            -_STATUS_SEVERITY[item["status"]],
            -item["revision"],
            item["category"],
        ),
    )[:_MAX_BLOCKERS]
    last_event = max(
        (record["last_event"] for record in normalized),
        key=lambda item: (
            item["revision"],
            _STATUS_SEVERITY[item["requested_status"]],
            item["reason"],
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
        "resolution_evidence": resolution_events,
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

    def update(current):
        records = [*existing, current]
        base = reconcile_execution_states(
            records,
            task_id=scope["task_id"],
            task_epoch=scope["task_epoch"],
            agent_id=agent_id,
        )
        revision = (base["revision"] if base is not None else 0) + 1
        blockers = list(base["unresolved_blockers"] if base is not None else [])
        resolutions = list(base["resolution_evidence"] if base is not None else [])
        if status in _BLOCKING_STATUSES:
            category = _blocker_category(status, reason)
            blocker = {
                "blocker_id": _blocker_id(
                    scope, revision=revision, status=status, reason=reason
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
                observed_ids = [
                    blocker["blocker_id"]
                    for blocker in blockers
                    if blocker["category"] == category
                    and blocker["revision"] <= watermark_revision
                    and (
                        explicitly_observed is None
                        or blocker["blocker_id"] in explicitly_observed
                    )
                ][-_MAX_RESOLUTION_IDS:]
                resolutions.append(
                    {
                        "category": category,
                        "evidence_kind": evidence_kind,
                        "revision": revision,
                        "watermark_revision": watermark_revision,
                        "blocker_ids": observed_ids,
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
            "last_event": {
                "revision": revision,
                "kind": "resolution" if evidence_kind is not None else "state",
                "requested_status": status,
                "reason": reason,
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
    observation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve only blockers causally observed by allowlisted typed evidence."""

    if evidence_kind not in _RESOLUTION_CATEGORIES:
        raise ValueError("unsupported execution resolution evidence")
    if status not in {"running", "succeeded"}:
        raise ValueError("resolution status must be running or succeeded")
    observed_revision = None
    observed_blocker_ids = None
    if observation is not None:
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
