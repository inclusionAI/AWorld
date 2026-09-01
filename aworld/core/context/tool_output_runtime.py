"""Runtime owner for predeclared, reversible Tool output bounding."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable

from aworld.core.context.compiler import (
    ArtifactRetrievalPlan,
    ArtifactRetrievalReceipt,
    ArtifactReceipt,
    ToolOutputPlan,
    ToolOutputMode,
    ToolOutputRecord,
    UpstreamToolArtifactReceipt,
    bind_tool_output,
    estimate_canonical_json_tokens,
    freeze_json,
    plan_tool_output,
    thaw_json,
    canonical_json_hash,
    hashed_identity,
)


def _canonical_sha256(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized.startswith("sha256:"):
        normalized = normalized[7:]
    if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
        return None
    return f"sha256:{normalized}"


def _extract_upstream_artifacts(
    content: Any,
    *,
    owner_tool: str,
) -> tuple[UpstreamToolArtifactReceipt, ...]:
    """Discover a Tool-owned artifact contract without trusting ambient metadata.

    Tool servers commonly return JSON through an MCP text block, so this walks
    both native values and full JSON strings.  A reference is accepted only when
    it is bound to a byte count and checksum; a path-like string by itself is not
    enough to become a retrieval capability.
    """

    found: list[UpstreamToolArtifactReceipt] = []
    seen_refs: set[str] = set()
    visited = 0

    def visit(value: Any, depth: int = 0) -> None:
        nonlocal visited
        if depth > 12 or visited >= 4096:
            return
        visited += 1
        if isinstance(value, str):
            stripped = value.strip()
            if (
                len(stripped) >= 2
                and stripped[0] in "[{"
                and stripped[-1] in "]}"
            ):
                try:
                    visit(json.loads(stripped), depth + 1)
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
            return
        if isinstance(value, list):
            for item in value:
                visit(item, depth + 1)
            return
        if not isinstance(value, dict):
            return

        ref = value.get("artifact_ref")
        checksum = _canonical_sha256(
            value.get("content_sha256")
            or value.get("content_hash")
            or value.get("raw_checksum")
        )
        byte_count = value.get("raw_bytes", value.get("byte_count"))
        if (
            isinstance(ref, str)
            and ref.strip()
            and checksum is not None
            and isinstance(byte_count, int)
            and not isinstance(byte_count, bool)
            and byte_count >= 0
            and ref not in seen_refs
        ):
            seen_refs.add(ref)
            found.append(
                UpstreamToolArtifactReceipt(
                    ref=ref,
                    content_hash=checksum,
                    byte_count=byte_count,
                    owner_tool=owner_tool,
                )
            )
        for nested in value.values():
            visit(nested, depth + 1)

    visit(content)
    return tuple(found)


def _raw_bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    except Exception:
        return str(value).encode("utf-8", errors="replace")


def prepare_tool_output_plans(context, actions: Iterable[Any]) -> dict[str, ToolOutputPlan]:
    """Freeze output limits before the Tool is invoked."""
    policy = getattr(context, "_tool_output_policy", None) if context else None
    plans: dict[str, ToolOutputPlan] = {}
    for action in actions:
        tool_call_id = getattr(action, "tool_call_id", None)
        retrieval_planned = _prepare_artifact_retrieval(context, action)
        if not isinstance(tool_call_id, str) or not tool_call_id:
            if policy is not None or retrieval_planned:
                raise ValueError("enforced Tool boundary requires a tool_call_id")
            continue
        if tool_call_id in plans:
            raise ValueError("Tool output plan ids must be unique")
        if policy is not None:
            plans[tool_call_id] = plan_tool_output(
                tool_call_id=tool_call_id,
                policy=policy,
            )
    return plans


def _prepare_artifact_retrieval(context, action: Any) -> bool:
    if context is None:
        return False
    owner_tool = getattr(action, "tool_name", None)
    action_name = getattr(action, "action_name", None)
    declared = [
        receipt
        for record in context.get_tool_output_records()
        for receipt in record.upstream_artifacts
        if receipt.owner_tool == owner_tool and receipt.retrieval_action == action_name
    ]
    if not declared:
        return False
    tool_call_id = getattr(action, "tool_call_id", None)
    if not isinstance(tool_call_id, str) or not tool_call_id:
        return True
    params = getattr(action, "params", None) or {}
    artifact_ref = params.get("artifact_ref")
    matches = [receipt for receipt in declared if receipt.ref == artifact_ref]
    if len(matches) != 1:
        raise ValueError("artifact_retrieval_ref_mismatch")
    source = matches[0]
    offset = params.get("offset", 0)
    limit = params.get("limit")
    if limit is None and isinstance(offset, int) and not isinstance(offset, bool):
        limit = max(1, source.byte_count - offset)
    plan = ArtifactRetrievalPlan(
        owner_tool=source.owner_tool,
        retrieval_action=source.retrieval_action,
        artifact_ref=source.ref,
        artifact_content_hash=source.content_hash,
        artifact_byte_count=source.byte_count,
        offset=offset,
        limit=limit,
        consumer_tool_call_id_hash=hashed_identity("tool_call_id", tool_call_id),
    )
    context.register_artifact_retrieval_plan(tool_call_id, plan)
    return True


def _retrieval_result_fields(value: Any) -> dict[str, Any] | None:
    visited = 0

    def visit(current: Any, depth: int = 0) -> dict[str, Any] | None:
        nonlocal visited
        if depth > 12 or visited >= 4096:
            return None
        visited += 1
        if isinstance(current, str):
            stripped = current.strip()
            if len(stripped) >= 2 and stripped[0] in "[{" and stripped[-1] in "]}":
                try:
                    return visit(json.loads(stripped), depth + 1)
                except (TypeError, ValueError, json.JSONDecodeError):
                    return None
            return None
        if isinstance(current, list):
            for item in current:
                found = visit(item, depth + 1)
                if found is not None:
                    return found
            return None
        if not isinstance(current, dict):
            return None
        required = {
            "artifact_ref", "offset", "next_offset", "returned_bytes",
            "total_bytes", "content_sha256", "chunk_sha256", "complete",
        }
        if required.issubset(current):
            return current
        for nested in current.values():
            found = visit(nested, depth + 1)
            if found is not None:
                return found
        return None

    return visit(value)


def _record_turn_and_retrieval(
    context, action: Any, action_result: Any, *, retrieval_fields: dict[str, Any] | None = None
) -> None:
    if context is None:
        return
    try:
        metadata = dict(getattr(action_result, "metadata", None) or {})
    except Exception:
        metadata = {}
    try:
        turn = context.record_tool_turn(action.tool_call_id)
        metadata["turn_economics"] = turn.to_redacted_dict()
    except Exception:
        metadata["turn_economics"] = {
            "status": "unavailable",
            "reason_code": "turn_economics_record_failed",
        }
    plan = getattr(context, "_artifact_retrieval_plans", {}).get(action.tool_call_id)
    if plan is not None:
        try:
            fields = retrieval_fields or _retrieval_result_fields(action_result.content)
            if fields is None:
                raise ValueError("artifact_retrieval_receipt_missing")
            source_hash = _canonical_sha256(fields.get("content_sha256"))
            chunk_hash = _canonical_sha256(fields.get("chunk_sha256"))
            if source_hash is None or chunk_hash is None:
                raise ValueError("artifact_retrieval_checksum_missing")
            if fields.get("artifact_ref") != plan.artifact_ref or fields.get("total_bytes") != plan.artifact_byte_count:
                raise ValueError("artifact_retrieval_source_mismatch")
            content = fields.get("content")
            content_type = fields.get("type", "text")
            chunk = (
                base64.b64decode(content, validate=True)
                if content_type == "base64" and isinstance(content, str)
                else content.encode("utf-8")
                if content_type == "text" and isinstance(content, str)
                else None
            )
            if chunk is None:
                raise ValueError("artifact_retrieval_chunk_missing")
            actual_chunk_hash = f"sha256:{hashlib.sha256(chunk).hexdigest()}"
            if len(chunk) != fields.get("returned_bytes") or actual_chunk_hash != chunk_hash:
                raise ValueError("artifact_retrieval_chunk_mismatch")
            receipt = ArtifactRetrievalReceipt(
                plan=plan,
                returned_offset=fields.get("offset"),
                next_offset=fields.get("next_offset"),
                returned_byte_count=fields.get("returned_bytes"),
                chunk_checksum=chunk_hash,
                source_content_hash=source_hash,
                result_content_hash=canonical_json_hash(action_result.content),
                complete=fields.get("complete"),
            )
            context.record_artifact_retrieval(action.tool_call_id, receipt)
            metadata["artifact_retrieval"] = receipt.to_redacted_dict()
        except Exception:
            metadata["artifact_retrieval"] = {
                "status": "unavailable",
                "reason_code": "artifact_retrieval_receipt_failed",
                "plan_fingerprint": plan.fingerprint,
            }
    action_result.metadata = metadata


def _artifact_root(context) -> Path:
    workspace = getattr(context, "workspace_path", None)
    if workspace:
        return Path(workspace).resolve() / ".aworld" / "artifacts" / "tool-output"
    session = str(getattr(context, "session_id", "unbound"))
    namespace = hashlib.sha256(session.encode("utf-8")).hexdigest()[:20]
    return Path(tempfile.gettempdir()) / "aworld-tool-output" / namespace


def _observe_unbounded_tool_output(action: Any, action_result: Any) -> None:
    """Attach byte economics in legacy/off mode without changing Tool content."""
    raw = _raw_bytes(action_result.content)
    owner_tool = str(
        getattr(action_result, "tool_name", None)
        or getattr(action, "tool_name", None)
        or "unknown"
    )
    upstream = _extract_upstream_artifacts(
        action_result.content, owner_tool=owner_tool
    )
    metadata = dict(getattr(action_result, "metadata", None) or {})
    metadata["tool_output_policy"] = {
        "policy_version": "off-v1",
        "reason_code": "unbounded_inline_output_observed",
        "raw_byte_count": len(raw),
        "raw_checksum": f"sha256:{hashlib.sha256(raw).hexdigest()}",
        "inline_tokens": estimate_canonical_json_tokens(
            raw.decode("utf-8", errors="replace")
        ).value or 0,
        "offloaded_tokens": 0,
        "artifact_ref": upstream[0].ref if upstream else None,
        "context_artifact_ref": None,
        "context_artifact_role": None,
        "upstream_artifacts": [receipt.to_dict() for receipt in upstream],
    }
    action_result.metadata = metadata


def _persist_artifact(context, raw: bytes) -> tuple[ArtifactReceipt, Path]:
    digest = hashlib.sha256(raw).hexdigest()
    checksum = f"sha256:{digest}"
    root = _artifact_root(context)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{digest}.bin"
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("existing Tool output artifact checksum mismatch")
    else:
        temporary = root / f".{digest}.{os.getpid()}.tmp"
        try:
            with temporary.open("xb") as stream:
                os.chmod(temporary, 0o600)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()
    return (
        ArtifactReceipt(
            ref=f"aworld-tool-output://{digest}",
            content_hash=checksum,
            byte_count=len(raw),
            media_type="application/octet-stream",
            retention_policy="task",
            source_content_hash=checksum,
        ),
        path,
    )


def _bounded_inline(
    raw: bytes,
    *,
    original: Any,
    plan: ToolOutputPlan,
    context_artifact_ref: str | None,
    upstream_artifacts: tuple[UpstreamToolArtifactReceipt, ...],
) -> Any:
    max_tokens = plan.policy.max_inline_tokens
    text = raw.decode("utf-8", errors="replace")
    mode = plan.policy.mode
    upstream = upstream_artifacts[0] if upstream_artifacts else None
    primary_artifact_ref = upstream.ref if upstream is not None else context_artifact_ref
    artifact_fields: dict[str, Any] = {"artifact_ref": primary_artifact_ref}
    if context_artifact_ref and context_artifact_ref != primary_artifact_ref:
        artifact_fields["context_artifact_ref"] = context_artifact_ref
    if upstream is not None:
        artifact_fields["artifact_retrieval"] = {
            "tool": upstream.owner_tool,
            "action": upstream.retrieval_action,
            "artifact_ref": upstream.ref,
            "content_hash": upstream.content_hash,
            "byte_count": upstream.byte_count,
        }
    if mode is ToolOutputMode.ARTIFACT_STREAM:
        payload: Any = {**artifact_fields, "byte_count": len(raw)}
    elif mode is ToolOutputMode.QUIET:
        payload = {
            **artifact_fields,
            "byte_count": len(raw),
            "content_hash": f"sha256:{hashlib.sha256(raw).hexdigest()}",
        }
    elif mode is ToolOutputMode.STRUCTURED and isinstance(original, dict):
        preserved = {
            key: original[key]
            for key in plan.policy.preserve_fields
            if key in original
        }
        payload = {
            **artifact_fields,
            "preserved": preserved,
            "omitted_fields": sorted(set(original) - set(preserved)),
        }
        if (estimate_canonical_json_tokens(payload).value or 0) > max_tokens:
            payload["preserved"] = {
                key: {
                    "content_hash": (
                        "sha256:"
                        + hashlib.sha256(_raw_bytes(value)).hexdigest()
                    ),
                    "byte_count": len(_raw_bytes(value)),
                }
                for key, value in preserved.items()
            }
    else:
        # HEAD_TAIL is the general text fallback.  Tail allocation is explicit
        # so error endings and command summaries remain visible.
        char_budget = max(0, max_tokens * 3)
        configured_tail = plan.policy.tail_tokens
        tail_chars = min(
            char_budget,
            (configured_tail * 3 if configured_tail is not None else char_budget // 2),
        )
        head_chars = max(0, char_budget - tail_chars)
        omitted = max(0, len(text) - head_chars - tail_chars)
        preview = (
            text
            if omitted == 0
            else text[:head_chars] + (text[-tail_chars:] if tail_chars else "")
        )
        payload = {
            **artifact_fields,
            "head": preview[:head_chars] if omitted else preview,
            "tail": preview[-tail_chars:] if omitted and tail_chars else "",
            "omitted_chars": omitted,
        }
    # Deterministically shrink previews while retaining the artifact receipt.
    while (estimate_canonical_json_tokens(payload).value or 0) > max_tokens:
        preview_key = next(
            (
                key
                for key in ("head", "tail")
                if isinstance(payload.get(key), str) and payload[key]
            ),
            None,
        )
        if preview_key is None:
            break
        value = payload[preview_key]
        payload[preview_key] = value[: len(value) // 2]
        payload["omitted_chars"] = len(text) - sum(
            len(payload.get(key, "")) for key in ("head", "tail")
        )
    if (estimate_canonical_json_tokens(payload).value or 0) > max_tokens:
        payload = dict(artifact_fields)
    if (estimate_canonical_json_tokens(payload).value or 0) > max_tokens:
        payload.pop("context_artifact_ref", None)
    if (estimate_canonical_json_tokens(payload).value or 0) > max_tokens:
        payload = {"artifact_ref": primary_artifact_ref}
    return payload


def enforce_tool_output_boundary(
    step_result,
    actions: Iterable[Any],
    context,
    plans: dict[str, ToolOutputPlan],
):
    """Bind raw results to artifacts before any result enters Context history."""
    action_values = tuple(actions)
    observation = step_result[0]
    results = getattr(observation, "action_result", None)
    if not isinstance(results, list) or len(results) < len(action_values):
        raise ValueError("Tool output policy cannot bind missing action results")
    for index, action in enumerate(action_values):
        tool_call_id = action.tool_call_id
        action_result = results[index]
        retrieval_fields = _retrieval_result_fields(action_result.content)
        if tool_call_id not in plans:
            try:
                _observe_unbounded_tool_output(action, action_result)
            except Exception:
                try:
                    metadata = dict(getattr(action_result, "metadata", None) or {})
                except Exception:
                    metadata = {}
                metadata["tool_output_policy"] = {
                    "status": "unavailable",
                    "reason_code": "tool_output_observation_failed",
                }
                action_result.metadata = metadata
            _record_turn_and_retrieval(
                context, action, action_result, retrieval_fields=retrieval_fields
            )
            continue
        plan = plans[tool_call_id]
        raw = _raw_bytes(action_result.content)
        owner_tool = str(
            getattr(action_result, "tool_name", None)
            or getattr(action, "tool_name", None)
            or "unknown"
        )
        upstream_artifacts = _extract_upstream_artifacts(
            action_result.content,
            owner_tool=owner_tool,
        )
        raw_tokens = estimate_canonical_json_tokens(
            raw.decode("utf-8", errors="replace")
        ).value or 0
        artifact = None
        artifact_path = None
        if (
            raw_tokens <= plan.policy.max_inline_tokens
            and not plan.artifact_required
        ):
            try:
                inline = freeze_json(action_result.content)
            except TypeError:
                inline = raw.decode("utf-8", errors="replace")
        else:
            if getattr(context, "_tool_output_artifact_offload", True):
                artifact, artifact_path = _persist_artifact(context, raw)
            else:
                raise ValueError(
                    "oversized Tool output requires artifact offload"
                )
            inline = _bounded_inline(
                raw,
                original=action_result.content,
                plan=plan,
                context_artifact_ref=artifact.ref if artifact is not None else None,
                upstream_artifacts=upstream_artifacts,
            )
        record = bind_tool_output(
            plan,
            raw_bytes=raw,
            inline_payload=inline,
            artifact=artifact,
            upstream_artifacts=upstream_artifacts,
        )
        action_result.content = thaw_json(record.inline_payload)
        metadata = dict(getattr(action_result, "metadata", None) or {})
        primary_artifact_ref = (
            record.upstream_artifacts[0].ref
            if record.upstream_artifacts
            else record.artifact.ref
            if record.artifact
            else None
        )
        metadata["tool_output_policy"] = {
            "policy_version": record.policy_version,
            "reason_code": record.reason_code,
            "raw_byte_count": record.raw_byte_count,
            "raw_checksum": record.raw_checksum,
            "inline_tokens": record.inline_tokens,
            "offloaded_tokens": record.offloaded_tokens,
            "artifact_ref": primary_artifact_ref,
            "context_artifact_ref": record.artifact.ref if record.artifact else None,
            "context_artifact_role": (
                "audit_snapshot"
                if record.artifact is not None and record.upstream_artifacts
                else "primary"
                if record.artifact is not None
                else None
            ),
            "upstream_artifacts": [
                receipt.to_dict() for receipt in record.upstream_artifacts
            ],
        }
        action_result.metadata = metadata
        try:
            context.record_tool_output(
                record,
                artifact_path=str(artifact_path) if artifact_path is not None else None,
            )
        except Exception:
            metadata = dict(action_result.metadata)
            metadata["tool_output_record"] = {
                "status": "unavailable",
                "reason_code": "tool_output_record_failed",
            }
            action_result.metadata = metadata
        _record_turn_and_retrieval(
            context, action, action_result, retrieval_fields=retrieval_fields
        )
    return step_result


__all__ = [
    "enforce_tool_output_boundary",
    "prepare_tool_output_plans",
]
