"""Framework-owned completion state, independent of an agent's final prose.

Control records contain no model/tool text. WorkingState is only a recovery
surface: restored records are scoped to the task and never prove completion.
"""
from __future__ import annotations

from copy import deepcopy
import inspect
from typing import Any

EXECUTION_STATE_KEY = "agent_execution_state"
EXECUTION_STATE_SCHEMA = "aworld.agent.execution-state/v1"
_STATUSES = {"running", "succeeded", "incomplete", "budget_exhausted"}


def state_context(context):
    manager = getattr(context, "event_manager", None)
    return getattr(manager, "context", None) or context


def record_execution_state(context, agent_id: str, status: str, reason: str,
                           recoverable: bool = True) -> dict[str, Any]:
    if status not in _STATUSES:
        raise ValueError("unsupported execution status")
    owner = state_context(context)
    record = {
        "schema_version": EXECUTION_STATE_SCHEMA,
        "task_id": getattr(owner, "task_id", None),
        "task_epoch": getattr(owner, "task_epoch", None),
        "agent_id": agent_id,
        "status": status,
        "reason": reason,
        "recoverable": bool(recoverable and status in {"incomplete", "budget_exhausted"}),
    }
    if owner is None:
        return record
    ledger = owner.context_info.get(f"adaptive_work_state:{agent_id}")
    record["work_state_revision"] = ledger.get("revision", 0) if isinstance(ledger, dict) else 0
    for target in (owner,) if owner is context else (owner, context):
        target.context_info[EXECUTION_STATE_KEY] = deepcopy(record)
        target.context_info[f"{EXECUTION_STATE_KEY}:{agent_id}"] = deepcopy(record)
    writer = getattr(owner, "write_task_runtime_state", None)
    if callable(writer):
        writer(agent_id, EXECUTION_STATE_KEY, record)
    put = getattr(owner, "put", None)
    if callable(put):
        put(EXECUTION_STATE_KEY, deepcopy(record))
    return record


def get_execution_state(context) -> dict[str, Any] | None:
    owner = state_context(context)
    if owner is None:
        return None
    record = owner.context_info.get(EXECUTION_STATE_KEY)
    if not isinstance(record, dict):
        get = getattr(owner, "get", None)
        record = get(EXECUTION_STATE_KEY) if callable(get) else None
    if not isinstance(record, dict) or record.get("schema_version") != EXECUTION_STATE_SCHEMA:
        return None
    if (record.get("task_id") != getattr(owner, "task_id", None)
            or record.get("task_epoch") != getattr(owner, "task_epoch", None)
            or record.get("status") not in _STATUSES):
        return None
    return deepcopy(record)


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
        elif (getattr(owner, "checkpoint_repository", None) is None
              or not getattr(owner, "session_id", None)):
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
            if callable(readback) and checkpoint_id and await readback(checkpoint_id) is None:
                owner.context_info["work_state_checkpoint_status"] = "checkpoint_failed"
                raise RuntimeError("work progress checkpoint was not persisted")
        owner.context_info["work_state_checkpoint_status"] = "checkpointed"
