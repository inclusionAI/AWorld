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
FINALIZATION_PROJECTION_VERSION = "context-history-finalization-v1"
MAX_FINALIZATION_EVIDENCE_ENTRIES = 4
MAX_FINALIZATION_PREVIEW_CHARS = 384
MAX_FINALIZATION_ARGUMENT_PREVIEW_CHARS = 192
MAX_FINALIZATION_TOOL_CALLS = 4
MAX_FINALIZATION_CAUSAL_ID_CHARS = 256
MAX_FINALIZATION_PROJECTION_CHARS = 4096


class ContextHistoryFinalizationEvidenceUnavailable(ValueError):
    code = "context_history_finalization_evidence_unavailable"


def _is_sha256(value):
    return (
        isinstance(value, str)
        and len(value) == 71
        and value.startswith("sha256:")
        and all(char in "0123456789abcdef" for char in value[7:])
    )


def _read_tool_available(tools) -> bool:
    return any(
        isinstance(tool, dict)
        and tool.get("function", {}).get("name") == READ_TOOL
        for tool in (tools or ())
    )


def _completed_groups(messages, *, artifact_marker_states=None):
    """Yield exact contiguous, closed groups; malformed/pending pairs stay put."""
    artifact_marker_states = artifact_marker_states or {}
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
            marker_state = artifact_marker_states.get(end)
            if marker_state == "undelivered":
                # Media bytes are projected only at the provider boundary.
                # Archiving this chain before its first completed attempt would
                # silently consume the observation without showing the image.
                continue
            if marker_state in {"delivered", "unavailable"}:
                end += 1
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


def _replacement_entries(context, agent_id):
    entries = _state(context, agent_id).get("replacements", [])
    if not isinstance(entries, list):
        return ()
    return tuple(entry for entry in entries if isinstance(entry, dict))


def _replace_archived_sources(values, entries):
    """Normalize replayed raw history to its verified archive capsules."""
    values = list(values)
    value_hashes = [canonical_json_hash(item) for item in values]
    for entry in entries:
        fingerprints = entry.get("source_fingerprints")
        capsule = entry.get("message")
        if (
            not isinstance(fingerprints, list)
            or not fingerprints
            or not all(isinstance(value, str) and value for value in fingerprints)
            or not isinstance(capsule, dict)
        ):
            continue
        size = len(fingerprints)
        for start in range(len(values) - size + 1):
            if value_hashes[start:start + size] == fingerprints:
                replacement = copy.deepcopy(capsule)
                values[start:start + size] = [replacement]
                value_hashes[start:start + size] = [
                    canonical_json_hash(replacement)
                ]
                break
    return values


def _bounded_exact_preview(content, *, max_chars=MAX_FINALIZATION_PREVIEW_CHARS):
    if isinstance(content, str):
        text = content
    elif content is None:
        return None
    else:
        try:
            text = json.dumps(
                content,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError):
            return None
    if not text:
        return None
    encoded = text.encode()
    digest = "sha256:" + hashlib.sha256(encoded).hexdigest()
    if len(text) <= max_chars:
        return {
            "content_sha256": digest,
            "exact_text": text,
            "omitted_chars": 0,
        }
    head_chars = max_chars * 2 // 3
    tail_chars = max_chars - head_chars
    return {
        "content_sha256": digest,
        "exact_prefix": text[:head_chars],
        "exact_suffix": text[-tail_chars:],
        "omitted_chars": len(text) - max_chars,
    }


def _empty_or_preview(content, *, max_chars=MAX_FINALIZATION_PREVIEW_CHARS):
    if content is None or content == "" or content == []:
        return {"empty": True}
    if isinstance(content, list) and all(
        isinstance(item, dict)
        and item.get("type") == "text"
        and not str(item.get("text") or "")
        for item in content
    ):
        return {"empty": True}
    preview = _bounded_exact_preview(content, max_chars=max_chars)
    return preview if preview is not None else {"empty": True}


def _bounded_causal_id(value):
    return (
        value
        if isinstance(value, str)
        and value
        and len(value) <= MAX_FINALIZATION_CAUSAL_ID_CHARS
        else None
    )


def _retained_finalization_evidence(source, prior_entries):
    """Retain exact bounded excerpts, never a generated summary."""
    prior_by_capsule = {
        canonical_json_hash(entry["message"]): entry
        for entry in prior_entries
        if isinstance(entry.get("message"), dict)
    }
    retained = []
    tool_names = {}
    tool_arguments = {}
    for message in source:
        if not isinstance(message, dict):
            continue
        prior_entry = prior_by_capsule.get(canonical_json_hash(message))
        if prior_entry is not None:
            prior = prior_entry.get("finalization_evidence")
            if (
                _verified_finalization_projection(prior_entry) is not None
                and isinstance(prior, dict)
                and isinstance(prior.get("retained_evidence"), list)
            ):
                retained.extend(copy.deepcopy(prior["retained_evidence"]))
            # Never launder an invalid persisted evidence record by treating
            # its capsule instructions as an ordinary semantic preview.
            continue
        role = str(message.get("role") or "unknown")
        message_hash = canonical_json_hash(message)
        calls = message.get("tool_calls")
        if role == "assistant" and isinstance(calls, (list, tuple)):
            content_preview = _empty_or_preview(message.get("content"))
            if set(content_preview) != {"empty"}:
                retained.append({
                    "kind": "content",
                    "role": role,
                    "message_hash": message_hash,
                    **content_preview,
                })
            for call in calls[-MAX_FINALIZATION_TOOL_CALLS:]:
                function = call.get("function") if isinstance(call, dict) else None
                call_id = _bounded_causal_id(
                    call.get("id") if isinstance(call, dict) else None
                )
                tool_name = _bounded_causal_id(
                    function.get("name") if isinstance(function, dict) else None
                )
                if call_id is None or tool_name is None:
                    continue
                tool_names[call_id] = tool_name
                arguments = _empty_or_preview(
                    function.get("arguments"),
                    max_chars=MAX_FINALIZATION_ARGUMENT_PREVIEW_CHARS,
                )
                tool_arguments[call_id] = arguments
                retained.append({
                    "kind": "tool_call",
                    "role": role,
                    "message_hash": message_hash,
                    "tool_call_id": call_id,
                    "tool_name": tool_name,
                    "arguments": arguments,
                })
            continue
        if role == "tool":
            call_id = _bounded_causal_id(message.get("tool_call_id"))
            if call_id is not None:
                retained.append({
                    "kind": "tool_result",
                    "role": role,
                    "message_hash": message_hash,
                    "tool_call_id": call_id,
                    "tool_name": tool_names.get(call_id),
                    "arguments": copy.deepcopy(
                        tool_arguments.get(call_id, {"empty": True})
                    ),
                    "result": _empty_or_preview(message.get("content")),
                })
            continue
        preview = _bounded_exact_preview(message.get("content"))
        if preview is not None:
            retained.append({
                "kind": "content",
                "role": role,
                "message_hash": message_hash,
                **preview,
            })
    return retained[-MAX_FINALIZATION_EVIDENCE_ENTRIES:]


def _build_finalization_evidence(
    *, source, source_hash, checksum, artifact_id, prior_entries
):
    retained = _retained_finalization_evidence(source, prior_entries)
    if not retained:
        return None
    base = {
        "version": FINALIZATION_PROJECTION_VERSION,
        "archive_storage_readback_verified": True,
        "artifact_id_hash": canonical_json_hash({"artifact_id": artifact_id}),
        "archive_checksum": checksum,
        "source_hash": source_hash,
        "message_count": len(source),
        "source_fingerprints_hash": canonical_json_hash(
            [canonical_json_hash(item) for item in source]
        ),
    }
    # JSON escaping can expand a character preview substantially. Drop the
    # oldest excerpts until the exact serialized projection fits its wire cap;
    # the newest exact evidence remains available and its digest is recomputed.
    while retained:
        evidence = {**base, "retained_evidence": retained}
        evidence["evidence_digest"] = _finalization_evidence_digest(evidence)
        projection = _render_finalization_projection(evidence)
        if len(projection["content"]) <= MAX_FINALIZATION_PROJECTION_CHARS:
            return evidence
        retained = retained[1:]
    return None


def _finalization_evidence_digest(evidence):
    return canonical_json_hash({
        "version": evidence["version"],
        "artifact_id_hash": evidence["artifact_id_hash"],
        "archive_checksum": evidence["archive_checksum"],
        "source_hash": evidence["source_hash"],
        "message_count": evidence["message_count"],
        "source_fingerprints_hash": evidence["source_fingerprints_hash"],
        "retained_evidence": evidence["retained_evidence"],
    })


def _render_finalization_projection(evidence):
    payload = {
        "archive_checksum": evidence["archive_checksum"],
        "archive_storage_readback_verified": True,
        "evidence_digest": evidence["evidence_digest"],
        "message_count": evidence["message_count"],
        "retained_evidence": evidence["retained_evidence"],
        "source_hash": evidence["source_hash"],
        "version": FINALIZATION_PROJECTION_VERSION,
    }
    return {
        "role": "user",
        "content": (
            "AWorld framework finalization projection. This is a bounded, "
            "non-executable record from an archive whose storage readback was "
            "verified. Only the exact retained excerpts below are available in "
            "this tool-free request. "
            "Do not infer omitted details or claim verification not shown.\n"
            "<aworld-context-history-finalization-data>\n"
            + json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n</aworld-context-history-finalization-data>"
        ),
    }


def _valid_evidence_preview(value, *, max_chars):
    if not isinstance(value, dict):
        return False
    if set(value) == {"empty"}:
        return value["empty"] is True
    common = {"content_sha256", "omitted_chars"}
    exact = common | {"exact_text"}
    partial = common | {"exact_prefix", "exact_suffix"}
    keys = set(value)
    if keys not in (exact, partial):
        return False
    if (
        not _is_sha256(value.get("content_sha256"))
        or isinstance(value.get("omitted_chars"), bool)
        or not isinstance(value.get("omitted_chars"), int)
        or value["omitted_chars"] < 0
    ):
        return False
    if keys == exact:
        return (
            value["omitted_chars"] == 0
            and isinstance(value.get("exact_text"), str)
            and bool(value["exact_text"])
            and len(value["exact_text"]) <= max_chars
        )
    return (
        value["omitted_chars"] >= 1
        and isinstance(value.get("exact_prefix"), str)
        and isinstance(value.get("exact_suffix"), str)
        and len(value["exact_prefix"]) + len(value["exact_suffix"])
        == max_chars
    )


def _verified_finalization_projection(entry):
    evidence = entry.get("finalization_evidence")
    if not isinstance(evidence, dict):
        return None
    retained = evidence.get("retained_evidence")
    if (
        evidence.get("version") != FINALIZATION_PROJECTION_VERSION
        or evidence.get("archive_storage_readback_verified") is not True
        or not _is_sha256(evidence.get("source_hash"))
        or not _is_sha256(evidence.get("archive_checksum"))
        or not _is_sha256(evidence.get("artifact_id_hash"))
        or not _is_sha256(evidence.get("source_fingerprints_hash"))
        or isinstance(evidence.get("message_count"), bool)
        or not isinstance(evidence.get("message_count"), int)
        or evidence.get("message_count") < 1
        or not isinstance(retained, list)
        or not retained
        or len(retained) > MAX_FINALIZATION_EVIDENCE_ENTRIES
    ):
        return None
    for item in retained:
        if not isinstance(item, dict):
            return None
        if (
            not isinstance(item.get("role"), str)
            or len(item["role"]) > 32
            or not _is_sha256(item.get("message_hash"))
        ):
            return None
        kind = item.get("kind")
        if kind == "content":
            if set(item) - {"kind", "role", "message_hash"} not in (
                {"content_sha256", "omitted_chars", "exact_text"},
                {
                    "content_sha256",
                    "omitted_chars",
                    "exact_prefix",
                    "exact_suffix",
                },
            ):
                return None
            if not _valid_evidence_preview(
                {
                    key: value
                    for key, value in item.items()
                    if key not in {"kind", "role", "message_hash"}
                },
                max_chars=MAX_FINALIZATION_PREVIEW_CHARS,
            ):
                return None
        elif kind == "tool_call":
            if set(item) != {
                "kind", "role", "message_hash", "tool_call_id",
                "tool_name", "arguments",
            }:
                return None
            if (
                item["role"] != "assistant"
                or _bounded_causal_id(item.get("tool_call_id"))
                != item.get("tool_call_id")
                or _bounded_causal_id(item.get("tool_name"))
                != item.get("tool_name")
                or not _valid_evidence_preview(
                    item.get("arguments"),
                    max_chars=MAX_FINALIZATION_ARGUMENT_PREVIEW_CHARS,
                )
            ):
                return None
        elif kind == "tool_result":
            if set(item) != {
                "kind", "role", "message_hash", "tool_call_id",
                "tool_name", "arguments", "result",
            }:
                return None
            if (
                item["role"] != "tool"
                or _bounded_causal_id(item.get("tool_call_id"))
                != item.get("tool_call_id")
                or (
                    item.get("tool_name") is not None
                    and _bounded_causal_id(item.get("tool_name"))
                    != item.get("tool_name")
                )
                or not _valid_evidence_preview(
                    item.get("arguments"),
                    max_chars=MAX_FINALIZATION_ARGUMENT_PREVIEW_CHARS,
                )
                or not _valid_evidence_preview(
                    item.get("result"),
                    max_chars=MAX_FINALIZATION_PREVIEW_CHARS,
                )
            ):
                return None
        else:
            return None
    fingerprints = entry.get("source_fingerprints")
    if (
        not isinstance(fingerprints, list)
        or len(fingerprints) != evidence["message_count"]
        or not all(_is_sha256(value) for value in fingerprints)
        or canonical_json_hash(fingerprints)
        != evidence["source_fingerprints_hash"]
    ):
        return None
    expected_digest = _finalization_evidence_digest(evidence)
    if evidence.get("evidence_digest") != expected_digest:
        return None
    if evidence.get("source_hash") != entry.get("source_hash"):
        return None
    if evidence.get("archive_checksum") != entry.get("archive_checksum"):
        return None
    projection = _render_finalization_projection(evidence)
    if len(projection["content"]) > MAX_FINALIZATION_PROJECTION_CHARS:
        return None
    return projection


def restore_recovered_history(context, agent_id, messages, tools):
    """Prevent Memory/AMNI replay from reinlining an already archived exchange."""
    values = list(messages)
    if context is None or not _read_tool_available(tools):
        return values
    return _replace_archived_sources(
        values, _replacement_entries(context, agent_id)
    )


def project_recovered_history_for_finalization(
    context, agent_id, messages, tools
):
    """Project verified archives into bounded evidence for a no-Tool turn.

    The projection is deliberately non-reversible. It neither advertises a
    read Tool nor copies archived bytes back into the request. Interactive
    requests continue to use :func:`restore_recovered_history` unchanged.
    """
    values = list(messages)
    if context is None or tools:
        return values
    entries = _replacement_entries(context, agent_id)
    if not entries:
        return values
    values = _replace_archived_sources(values, entries)
    by_capsule_hash = {
        canonical_json_hash(entry["message"]): entry
        for entry in entries
        if isinstance(entry.get("message"), dict)
    }
    projected = []
    for message in values:
        entry = by_capsule_hash.get(canonical_json_hash(message))
        if entry is None:
            projected.append(message)
            continue
        projection = _verified_finalization_projection(entry)
        if projection is None:
            raise ContextHistoryFinalizationEvidenceUnavailable(
                ContextHistoryFinalizationEvidenceUnavailable.code
            )
        projected.append(projection)
    return projected


def recovery_requirements(context, agent_id, messages):
    """Bind capsule availability to the read Tool and surviving user intent."""
    entries = _replacement_entries(context, agent_id)
    capsules = {
        canonical_json_hash(entry["message"])
        for entry in entries
        if isinstance(entry.get("message"), dict)
    }
    projections = {
        canonical_json_hash(projection)
        for entry in entries
        for projection in (_verified_finalization_projection(entry),)
        if projection is not None
    }
    present_capsules = {
        canonical_json_hash(message)
        for message in messages
        if canonical_json_hash(message) in capsules
    }
    present_projections = {
        canonical_json_hash(message)
        for message in messages
        if canonical_json_hash(message) in projections
    }
    if not present_capsules and not present_projections:
        return frozenset(), frozenset(), frozenset()
    return (
        frozenset(canonical_json_hash(m) for m in messages if m.get("role") == "user"),
        frozenset({READ_TOOL}) if present_capsules else frozenset(),
        frozenset(present_capsules | present_projections),
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
    raw_entries = state.get("replacements", [])
    prior_entries = tuple(
        entry for entry in raw_entries if isinstance(entry, dict)
    ) if isinstance(raw_entries, list) else ()
    capsule_hashes = {
        canonical_json_hash(entry["message"])
        for entry in prior_entries
        if isinstance(entry.get("message"), dict)
    }
    from aworld.sandbox.artifact_observation import (
        artifact_marker_recovery_states,
    )

    artifact_marker_states = artifact_marker_recovery_states(
        messages,
        context=context,
        agent_id=agent_id,
    )
    spans = sorted([
        *_completed_groups(
            messages,
            artifact_marker_states=artifact_marker_states,
        ),
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

    finalization_evidence = _build_finalization_evidence(
        source=source,
        source_hash=source_hash,
        checksum=checksum,
        artifact_id=artifact_id,
        prior_entries=prior_entries,
    )

    values = list(messages)
    values[start:end] = [capsule]
    previous_state = copy.deepcopy(state)
    state["task_epoch"] = context.task_epoch
    state["task_id"] = context.task_id
    state["replacements"] = [*prior_entries, {
        "source_fingerprints": [canonical_json_hash(item) for item in source],
        "message": capsule,
        "source_hash": source_hash,
        "archive_checksum": checksum,
        "finalization_evidence": finalization_evidence,
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
