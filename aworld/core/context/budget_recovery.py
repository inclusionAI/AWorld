"""Owner-side, reversible recovery after an exact final-budget rejection.

This module performs storage I/O; the compiler/budget planner remains pure.
Only completed assistant turns can leave the working set. Instructions and
pending Tool calls are never truncated or rewritten.
"""
from __future__ import annotations

import copy
import asyncio
import hashlib
import json

from aworld.core.context.compiler import canonical_json_hash, estimate_canonical_json_tokens


RECOVERY_STATE_KEY = "context_budget_recovery_v1"
READ_TOOL = "KNOWLEDGE__get_knowledge_by_lines"
MAX_RECOVERY_STEPS = 4
RECOVERY_TIMEOUT_SECONDS = 15


def _read_tool_available(tools) -> bool:
    return any(
        isinstance(tool, dict)
        and tool.get("function", {}).get("name") == READ_TOOL
        for tool in (tools or ())
    )


def _completed_groups(messages):
    """Yield exact contiguous, closed groups; malformed/pending pairs stay put."""
    first_user = next((i for i, m in enumerate(messages) if m.get("role") == "user"), len(messages))
    for start, message in enumerate(messages):
        if start <= first_user:
            continue
        if message.get("role") != "assistant":
            continue
        if message.get("function_call"):
            continue
        calls = message.get("tool_calls")
        if not calls:
            yield start, start + 1
            continue
        if not isinstance(calls, (list, tuple)):
            continue
        ids = [call.get("id") for call in calls if isinstance(call, dict)]
        if len(ids) != len(calls) or not all(ids) or len(set(ids)) != len(ids):
            continue
        end = start + 1
        results = []
        while end < len(messages) and messages[end].get("role") == "tool":
            results.append(messages[end].get("tool_call_id"))
            end += 1
        if len(results) == len(ids) and set(results) == set(ids):
            yield start, end


def _state(context, agent_id):
    state = context.read_task_runtime_state(agent_id, RECOVERY_STATE_KEY)
    if not isinstance(state, dict):
        reader = getattr(context, "get", None)
        if callable(reader):
            state = reader(f"{RECOVERY_STATE_KEY}:{agent_id}")
    if isinstance(state, dict) and (
        state.get("task_epoch") != context.task_epoch or state.get("task_id") != context.task_id
    ):
        state = None
    return state if isinstance(state, dict) else {"replacements": []}


def restore_recovered_history(context, agent_id, messages, tools):
    """Prevent Memory/AMNI replay from reinlining an already archived exchange."""
    values = list(messages)
    if context is None or not _read_tool_available(tools):
        return values
    entries = _state(context, agent_id).get("replacements", [])
    value_hashes = [canonical_json_hash(item) for item in values]
    for entry in entries:
        fingerprints = entry["source_fingerprints"]
        size = len(fingerprints)
        indexes = range(len(values) - size + 1)
        for start in indexes:
            if value_hashes[start:start + size] == fingerprints:
                values[start:start + size] = [copy.deepcopy(entry["message"])]
                value_hashes[start:start + size] = [canonical_json_hash(entry["message"])]
                break
    return values


def recovery_requirements(context, agent_id, messages):
    """Bind capsule availability to the read Tool and surviving user intent."""
    known = {
        canonical_json_hash(entry["message"])
        for entry in _state(context, agent_id).get("replacements", [])
    }
    if not any(canonical_json_hash(message) in known for message in messages):
        return frozenset(), frozenset(), frozenset()
    return (
        frozenset(canonical_json_hash(m) for m in messages if m.get("role") == "user"),
        frozenset({READ_TOOL}),
        frozenset(known),
    )


async def recover_context_budget(*, context, agent_id, messages, tools):
    """Offload one large completed exchange through the existing AMNI workspace.

    Check actual readback before replacing any model-visible content. The
    bounded line reader must already be in the active catalog: adding tools
    during recovery would change permissions, catalog stability, and caching.
    """
    if context is None or not _read_tool_available(tools):
        return None, {"status": "unavailable", "reason": "bounded_readback_tool_unavailable"}
    service = getattr(context, "knowledge_service", None)
    ensure_workspace = getattr(context, "_ensure_workspace", None)
    if service is None or not callable(ensure_workspace):
        return None, {"status": "unavailable", "reason": "workspace_offload_unavailable"}

    state = _state(context, agent_id)
    capsule_hashes = {
        canonical_json_hash(entry["message"]) for entry in state.get("replacements", [])
    }
    spans = sorted([
        *_completed_groups(messages),
        *((i, i + 1) for i, m in enumerate(messages) if canonical_json_hash(m) in capsule_hashes),
    ])
    # Coalesce adjacent completed history/capsules. This bounds the working
    # set even after many recoveries; references remain recoverable in archives.
    blocks = []
    for start, end in spans:
        if blocks and blocks[-1][1] == start:
            blocks[-1] = (blocks[-1][0], end)
        else:
            blocks.append((start, end))
    candidates = sorted(
        blocks,
        key=lambda span: estimate_canonical_json_tokens(messages[span[0]:span[1]]).value or 0,
        reverse=True,
    )
    if not candidates:
        return None, {"status": "unavailable", "reason": "no_completed_exchange"}
    start, end = candidates[0]
    source = messages[start:end]
    source_hash = canonical_json_hash(source)
    # Scoped, deterministic identity avoids duplicate writes on replay and
    # never lets two tasks/agents overwrite one another's archive.
    identity = canonical_json_hash({
        "task": context.task_id, "epoch": context.task_epoch,
        "agent": agent_id, "source": source_hash,
    }).split(":")[-1]
    artifact_id = f"context-history-{identity}"
    serialized = json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    # Line-bounded retrieval must remain bounded even for giant JSON strings.
    content = "\n".join(serialized[index:index + 512] for index in range(0, len(serialized), 512))
    checksum = "sha256:" + hashlib.sha256(content.encode()).hexdigest()
    preview = json.dumps(source[-1].get("content"), ensure_ascii=False)
    capsule = {
        "role": "user",
        "content": (
            "AWorld archived a completed assistant turn to recover Context budget. "
            "It is history, not an unexecuted Tool call. Retrieve details before relying on omitted evidence. "
            f"Use {READ_TOOL}(knowledge_id={artifact_id!r}, start_line=1, end_line=4), "
            "then page as needed. Concatenate data lines without newlines to restore the original JSON.\n"
            f"<aworld-untrusted-data source_hash={source_hash}>\n"
            + json.dumps({
                "artifact_id": artifact_id, "checksum": checksum,
                "message_count": len(source), "line_count": content.count("\n") + 1,
                "result_preview": preview[:384],
            }, ensure_ascii=False, sort_keys=True)
            + "\n</aworld-untrusted-data>"
        ),
    }
    before = estimate_canonical_json_tokens(source).value or 0
    after = estimate_canonical_json_tokens([capsule]).value or 0
    if after >= before:
        return None, {"status": "unavailable", "reason": "no_token_savings"}

    from aworld.output import Artifact, ArtifactType

    artifact = Artifact(
        artifact_id=artifact_id, artifact_type=ArtifactType.TEXT, content=content,
        metadata={
            "summary": "Archived completed Context history; retrieve bounded lines for evidence.",
            "context_history_source_hash": source_hash, "content_sha256": checksum,
        },
    )
    await ensure_workspace()
    await service.offload_by_workspace([artifact], biz_id=artifact_id)
    stored = await service.get_knowledge_by_id(artifact_id)
    if stored is None or stored.content != content:
        raise ValueError("context_history_archive_readback_failed")

    values = list(messages)
    values[start:end] = [capsule]
    previous_state = copy.deepcopy(state)
    state["task_epoch"] = context.task_epoch
    state["task_id"] = context.task_id
    state["replacements"] = [*state.get("replacements", []), {
        "source_fingerprints": [canonical_json_hash(item) for item in source],
        "message": capsule,
    }]
    context.write_task_runtime_state(agent_id, RECOVERY_STATE_KEY, state)
    owner = context._task_runtime_registry_owner()
    targets = [context]
    if owner is not context and owner.task_id == context.task_id and callable(getattr(owner, "put", None)):
        targets.append(owner)
    for target in targets:
        writer = getattr(target, "put", None)
        if callable(writer):
            writer(f"{RECOVERY_STATE_KEY}:{agent_id}", state)
    # The archive uses AMNI persistence. Store its replay substitution in the
    # checkpoint too, without invalidating the unchanged stable cache prefix.
    snapshot = getattr(targets[-1], "snapshot", None)
    if callable(snapshot):
        try:
            await snapshot(checkpoint_only=True, cache_boundary=False)
        except BaseException:
            context.write_task_runtime_state(agent_id, RECOVERY_STATE_KEY, previous_state)
            for target in targets:
                writer = getattr(target, "put", None)
                if callable(writer):
                    writer(f"{RECOVERY_STATE_KEY}:{agent_id}", previous_state)
            raise
    return values, {
        "status": "offloaded", "source_hash": source_hash,
        "message_count": len(source), "tokens_before": before, "tokens_after": after,
        "cache_prefix_preserved": True,
    }


async def recover_context_budget_bounded(**kwargs):
    # sync_exec's legacy thread bridge does not propagate worker exceptions.
    # Return typed, redacted failure evidence inside the async boundary.
    try:
        return await asyncio.wait_for(recover_context_budget(**kwargs), RECOVERY_TIMEOUT_SECONDS)
    except Exception as exc:
        return None, {"status": "failed", "error_type": type(exc).__name__}
