"""Runtime owner for predeclared, reversible Tool output bounding."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable

from aworld.core.context.compiler import (
    ArtifactReceipt,
    ToolOutputPlan,
    ToolOutputMode,
    ToolOutputRecord,
    bind_tool_output,
    estimate_canonical_json_tokens,
    freeze_json,
    plan_tool_output,
    thaw_json,
)


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
    if policy is None:
        return {}
    plans: dict[str, ToolOutputPlan] = {}
    for action in actions:
        tool_call_id = getattr(action, "tool_call_id", None)
        if not isinstance(tool_call_id, str) or not tool_call_id:
            raise ValueError("enforced Tool output policy requires a tool_call_id")
        if tool_call_id in plans:
            raise ValueError("Tool output plan ids must be unique")
        plans[tool_call_id] = plan_tool_output(
            tool_call_id=tool_call_id,
            policy=policy,
        )
    return plans


def _artifact_root(context) -> Path:
    workspace = getattr(context, "workspace_path", None)
    if workspace:
        return Path(workspace).resolve() / ".aworld" / "artifacts" / "tool-output"
    session = str(getattr(context, "session_id", "unbound"))
    namespace = hashlib.sha256(session.encode("utf-8")).hexdigest()[:20]
    return Path(tempfile.gettempdir()) / "aworld-tool-output" / namespace


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
    artifact_ref: str | None,
) -> Any:
    max_tokens = plan.policy.max_inline_tokens
    text = raw.decode("utf-8", errors="replace")
    mode = plan.policy.mode
    if mode is ToolOutputMode.ARTIFACT_STREAM:
        payload: Any = {"artifact_ref": artifact_ref, "byte_count": len(raw)}
    elif mode is ToolOutputMode.QUIET:
        payload = {
            "artifact_ref": artifact_ref,
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
            "artifact_ref": artifact_ref,
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
            "artifact_ref": artifact_ref,
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
        payload = {"artifact_ref": artifact_ref}
    return payload


def enforce_tool_output_boundary(
    step_result,
    actions: Iterable[Any],
    context,
    plans: dict[str, ToolOutputPlan],
):
    """Bind raw results to artifacts before any result enters Context history."""
    if not plans:
        return step_result
    action_values = tuple(actions)
    observation = step_result[0]
    results = getattr(observation, "action_result", None)
    if not isinstance(results, list) or len(results) < len(action_values):
        raise ValueError("Tool output policy cannot bind missing action results")
    for index, action in enumerate(action_values):
        tool_call_id = action.tool_call_id
        plan = plans[tool_call_id]
        action_result = results[index]
        raw = _raw_bytes(action_result.content)
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
                artifact_ref=artifact.ref if artifact is not None else None,
            )
        record = bind_tool_output(
            plan,
            raw_bytes=raw,
            inline_payload=inline,
            artifact=artifact,
        )
        action_result.content = thaw_json(record.inline_payload)
        metadata = dict(getattr(action_result, "metadata", None) or {})
        metadata["tool_output_policy"] = {
            "policy_version": record.policy_version,
            "reason_code": record.reason_code,
            "raw_byte_count": record.raw_byte_count,
            "raw_checksum": record.raw_checksum,
            "inline_tokens": record.inline_tokens,
            "offloaded_tokens": record.offloaded_tokens,
            "artifact_ref": record.artifact.ref if record.artifact else None,
        }
        action_result.metadata = metadata
        context.record_tool_output(
            record,
            artifact_path=str(artifact_path) if artifact_path is not None else None,
        )
    return step_result


__all__ = [
    "enforce_tool_output_boundary",
    "prepare_tool_output_plans",
]
