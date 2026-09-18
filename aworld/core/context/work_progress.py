"""Retain task intent and observed delivery state alongside the Tool ledger.

Agent plans are claims, never verification. Only caller-supplied completion
contracts and runtime evidence can declare required or verified artifacts.
"""
from __future__ import annotations

from copy import deepcopy

from aworld.core.context.compiler.adaptive import semantic_fingerprint
from aworld.core.context.compiler.work_state import ADAPTIVE_WORK_STATE_KEY
from aworld.core.context.execution_state import state_context, checkpoint_execution_state


def _load(context, agent_id):
    key = f"{ADAPTIVE_WORK_STATE_KEY}:{agent_id}"
    reader = getattr(context, "read_task_runtime_state", None)
    state = reader(agent_id, ADAPTIVE_WORK_STATE_KEY) if callable(reader) else None
    if not isinstance(state, dict):
        state = context.context_info.get(key)
    if not isinstance(state, dict):
        get = getattr(context, "get", None)
        state = get(key) if callable(get) else None
    state = deepcopy(state) if isinstance(state, dict) else {}
    scope = state.get("scope")
    expected = {"task_id": getattr(context, "task_id", None), "task_epoch": getattr(context, "task_epoch", None)}
    if scope is not None and scope != expected:
        state = {}
    state["scope"] = expected
    return state


def retain_work_progress(context, agent_id: str, *, plan: str | None = None):
    owner = state_context(context)
    state = _load(owner, agent_id)
    request = getattr(context, "origin_user_input", None) or getattr(context, "task_input", None)
    if isinstance(request, str) and request.strip():
        request_key = "current_task_request" if state.get("carried_from") else "public_requirements"
        state[request_key] = {
            "source": "task_input", "text": request,
            "content_hash": semantic_fingerprint(request),
        }
    if isinstance(plan, str) and plan.strip():
        state["current_plan"] = {
            "source": "agent_claim", "text": plan,
            "content_hash": semantic_fingerprint(plan),
        }
    contract = getattr(context, "completion_contract", None)
    if contract is not None:
        mode = getattr(getattr(context, "completion_mode", None), "value", None)
        source = context.context_info.get("runtime_completion_contract", {}).get("source", "caller_contract")
        evidence = {e.requirement_id: e for e in getattr(context, "_completion_artifact_evidence", ())}
        state["candidate_submission"] = [
            {"path": r.path, "requirement_id": r.requirement_id, "source": source,
             "required": r.required and mode == "enforce", "exists": evidence[r.requirement_id].exists if r.requirement_id in evidence else None,
             "observed_hash": evidence[r.requirement_id].content_hash if r.requirement_id in evidence else None}
            for r in contract.required_artifacts
        ]
        state["pending_artifacts"] = [
            item["path"] for item in state["candidate_submission"]
            if item["required"] and item["exists"] is not True
        ]
        state["validation_evidence"] = [
            {"command_id": e.command_id, "exit_code": e.exit_code,
             "output_hash": e.output_hash, "source": "runtime_self_check"}
            for e in getattr(context, "_completion_self_checks", ())
        ][-8:]
    key = f"{ADAPTIVE_WORK_STATE_KEY}:{agent_id}"
    updater = getattr(owner, "update_task_runtime_state", None)
    if callable(updater):
        preserved = {key: value for key, value in state.items() if key in {
            "scope", "carried_from", "public_requirements", "current_task_request", "current_plan",
            "candidate_submission", "pending_artifacts", "validation_evidence",
        }}
        def merge_current(current):
            if not isinstance(current, dict) or current.get("scope") not in (None, state["scope"]):
                current = state
            return {**current, **preserved}
        state = updater(agent_id, ADAPTIVE_WORK_STATE_KEY, merge_current)
    owner.context_info[key] = deepcopy(state)
    if owner is not context:
        context.context_info[key] = deepcopy(state)
    writer = getattr(owner, "write_task_runtime_state", None)
    if callable(writer):
        writer(agent_id, ADAPTIVE_WORK_STATE_KEY, state)
    put = getattr(owner, "put", None)
    if callable(put):
        put(key, deepcopy(state))
    return state


async def checkpoint_work_progress(context, agent_id: str, *, force: bool = False):
    owner = state_context(context)
    state = _load(owner, agent_id)
    fingerprint = semantic_fingerprint(state)
    key = f"work_progress_checkpoint:{agent_id}"
    if force or owner.context_info.get(key) != fingerprint:
        await checkpoint_execution_state(owner)
        owner.context_info[key] = fingerprint


def carry_goal_work_state(old_context, new_context, *, agent_id_mapping=None) -> int:
    """Explicit trusted goal continuation across execution-segment task IDs.

    Evidence remains historical. Neither completion state, step counters nor
    fresh validation receipts are copied into the new execution segment.
    """
    old, new = state_context(old_context), state_context(new_context)
    if old is None or new is None:
        return 0
    values = {}
    working = getattr(getattr(old, "task_state", None), "working_state", None)
    values.update(getattr(working, "kv_store", None) or {})
    values.update(dict(old.context_info))
    source_task_id = getattr(old, "task_id", None)
    source_task_epoch = getattr(old, "task_epoch", None)
    copied = 0
    for key, value in values.items():
        if not key.startswith(ADAPTIVE_WORK_STATE_KEY + ":") or not isinstance(value, dict):
            continue
        scope = value.get("scope")
        if not isinstance(scope, dict) or scope.get("task_id") != source_task_id:
            continue
        if source_task_epoch is not None and scope.get("task_epoch") != source_task_epoch:
            continue
        agent_id = key[len(ADAPTIVE_WORK_STATE_KEY) + 1:]
        agent_id = (agent_id_mapping or {}).get(agent_id, agent_id)
        state = deepcopy(value)
        state["carried_from"] = state.get("scope") or {"task_id": getattr(old, "task_id", None)}
        state["scope"] = {"task_id": getattr(new, "task_id", None), "task_epoch": getattr(new, "task_epoch", None)}
        state["repeated_read_evidence"] = None
        for evidence in state.get("validation_evidence", []):
            evidence["historical"] = True
        for candidate in state.get("candidate_submission", []):
            candidate["historical"] = True
        key = f"{ADAPTIVE_WORK_STATE_KEY}:{agent_id}"
        new.context_info[key] = deepcopy(state)
        writer = getattr(new, "write_task_runtime_state", None)
        if callable(writer):
            writer(agent_id, ADAPTIVE_WORK_STATE_KEY, state)
        put = getattr(new, "put", None)
        if callable(put):
            put(key, deepcopy(state))
        copied += 1
    return copied


def resume_goal_work_state(context, *, source_task_id: str, source_task_epoch=None,
                           agent_id_mapping=None) -> int:
    """Rebind only an explicitly named prior segment loaded from a checkpoint."""
    owner = state_context(context)
    if not source_task_id or owner is None:
        return 0
    working = getattr(getattr(owner, "task_state", None), "working_state", None)
    values = {**(getattr(working, "kv_store", None) or {}), **dict(owner.context_info)}
    scoped = {}
    for key, value in values.items():
        if not key.startswith(ADAPTIVE_WORK_STATE_KEY + ":") or not isinstance(value, dict):
            continue
        scope = value.get("scope")
        if not isinstance(scope, dict) or scope.get("task_id") != source_task_id:
            continue
        if source_task_epoch is not None and scope.get("task_epoch") != source_task_epoch:
            continue
        scoped[key] = value
    # An in-memory source containing only the caller-authorized old task's
    # recovery records prevents an unrelated checkpoint from being imported.
    from types import SimpleNamespace
    source = SimpleNamespace(context_info=scoped, task_id=source_task_id,
                             task_epoch=source_task_epoch)
    return carry_goal_work_state(source, owner, agent_id_mapping=agent_id_mapping)


def record_budget_handoff(context, agent_id: str) -> bool:
    """Pause only an exhausted segment that repeats the prior exhausted state.

    Re-reading during a normal turn is unrestricted. A changed result, artifact
    or validation result permits continuation; model prose alone is not progress.
    """
    owner = state_context(context)
    state = _load(owner, agent_id)
    recent = state.get("recent_operations", [])
    signature = semantic_fingerprint({
        "artifact": state.get("artifact_fingerprint"),
        "results": sorted({str(item.get("result_hash")) for item in recent}),
        "submission": [{key:item.get(key) for key in ("path", "exists", "observed_hash")}
                       for item in state.get("candidate_submission", [])],
        "validation": [{key:item.get(key) for key in ("command_id", "exit_code", "output_hash")}
                       for item in state.get("validation_evidence", [])],
    })
    previous = state.get("budget_handoff", {})
    repeated_reads = bool(state.get("repeated_read_evidence"))
    unchanged = previous.get("progress_fingerprint") == signature
    recoverable = not (unchanged and repeated_reads)
    state["budget_handoff"] = {"progress_fingerprint": signature,
                               "recoverable": recoverable,
                               "reason": "repeated_exhausted_state" if not recoverable else "progress_checkpointed"}
    key = f"{ADAPTIVE_WORK_STATE_KEY}:{agent_id}"
    owner.context_info[key] = state
    writer = getattr(owner, "write_task_runtime_state", None)
    if callable(writer):
        writer(agent_id, ADAPTIVE_WORK_STATE_KEY, state)
    put = getattr(owner, "put", None)
    if callable(put):
        put(key, deepcopy(state))
    return recoverable
