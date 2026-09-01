"""Execution-time Tool output bounding before results enter Context history."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import re
from .frozen_json import FrozenJSON, freeze_json
from .reducers import ArtifactReceipt
from .runtime import estimate_canonical_json_tokens


class ToolOutputMode(str, Enum):
    STRUCTURED = "structured"
    QUIET = "quiet"
    HEAD_TAIL = "head_tail"
    ARTIFACT_STREAM = "artifact_stream"


@dataclass(frozen=True, slots=True)
class ToolOutputPolicy:
    max_inline_tokens: int
    mode: ToolOutputMode
    preserve_fields: tuple[str, ...]
    tail_tokens: int | None
    artifact_retention: str
    policy_version: str

    def __post_init__(self) -> None:
        if isinstance(self.max_inline_tokens, bool) or not isinstance(
            self.max_inline_tokens, int
        ) or self.max_inline_tokens <= 0:
            raise ValueError("max_inline_tokens must be positive")
        object.__setattr__(self, "mode", ToolOutputMode(self.mode))
        object.__setattr__(self, "preserve_fields", tuple(self.preserve_fields))
        if self.tail_tokens is not None and (
            isinstance(self.tail_tokens, bool)
            or not isinstance(self.tail_tokens, int)
            or self.tail_tokens < 0
        ):
            raise ValueError("tail_tokens must be non-negative or None")
        if (
            self.tail_tokens is not None
            and self.tail_tokens > self.max_inline_tokens
        ):
            raise ValueError("tail_tokens cannot exceed max_inline_tokens")
        if not self.artifact_retention or not self.policy_version:
            raise ValueError("retention and policy_version must be non-empty")


@dataclass(frozen=True, slots=True)
class ToolOutputPlan:
    tool_call_id: str
    policy: ToolOutputPolicy
    artifact_required: bool


@dataclass(frozen=True, slots=True)
class UpstreamToolArtifactReceipt:
    """Artifact already owned and retrievable by the originating Tool.

    This receipt is deliberately separate from ``ToolOutputRecord.artifact``.
    The latter binds the exact ActionResult snapshot owned by Context, while an
    upstream receipt can bind a larger stream (for example stdout) that the Tool
    bounded before AWorld received the ActionResult.
    """

    ref: str
    content_hash: str
    byte_count: int
    owner_tool: str
    retrieval_action: str = "read_output_artifact"

    def __post_init__(self) -> None:
        for name in ("ref", "owner_tool", "retrieval_action"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.content_hash):
            raise ValueError("content_hash must be a canonical sha256 hash")
        if isinstance(self.byte_count, bool) or not isinstance(self.byte_count, int):
            raise TypeError("byte_count must be an integer")
        if self.byte_count < 0:
            raise ValueError("byte_count must be non-negative")

    def to_dict(self) -> dict[str, object]:
        return {
            "ref": self.ref,
            "content_hash": self.content_hash,
            "byte_count": self.byte_count,
            "owner_tool": self.owner_tool,
            "retrieval_action": self.retrieval_action,
        }


@dataclass(frozen=True, slots=True)
class ToolOutputRecord:
    tool_call_id: str
    policy_version: str
    raw_byte_count: int
    raw_checksum: str
    inline_payload: FrozenJSON
    inline_tokens: int
    offloaded_tokens: int
    artifact: ArtifactReceipt | None
    reason_code: str
    upstream_artifacts: tuple[UpstreamToolArtifactReceipt, ...] = ()


def plan_tool_output(
    *, tool_call_id: str, policy: ToolOutputPolicy
) -> ToolOutputPlan:
    if not isinstance(tool_call_id, str) or not tool_call_id.strip():
        raise ValueError("tool_call_id must be a non-empty string")
    return ToolOutputPlan(
        tool_call_id=tool_call_id,
        policy=policy,
        artifact_required=policy.mode is ToolOutputMode.ARTIFACT_STREAM,
    )


def bind_tool_output(
    plan: ToolOutputPlan,
    *,
    raw_bytes: bytes,
    inline_payload: FrozenJSON,
    artifact: ArtifactReceipt | None = None,
    upstream_artifacts: tuple[UpstreamToolArtifactReceipt, ...] = (),
) -> ToolOutputRecord:
    """Validate owner output; artifact creation remains outside compiler core."""
    if not isinstance(raw_bytes, bytes):
        raise TypeError("raw_bytes must be bytes")
    frozen_inline = freeze_json(inline_payload)
    inline_estimate = estimate_canonical_json_tokens(frozen_inline)
    inline_tokens = inline_estimate.value or 0
    if inline_tokens > plan.policy.max_inline_tokens:
        raise ValueError("inline Tool output exceeds the predeclared token limit")
    if plan.artifact_required and artifact is None:
        raise ValueError("artifact_stream output requires an ArtifactReceipt")
    if artifact is not None and artifact.byte_count != len(raw_bytes):
        raise ValueError("artifact byte count does not match raw Tool output")
    raw_checksum = f"sha256:{hashlib.sha256(raw_bytes).hexdigest()}"
    if artifact is not None and artifact.content_hash != raw_checksum:
        raise ValueError("artifact checksum does not match raw Tool output")
    raw_token_estimate = estimate_canonical_json_tokens(
        raw_bytes.decode("utf-8", errors="replace")
    ).value or 0
    upstream_artifacts = tuple(upstream_artifacts)
    if any(
        not isinstance(receipt, UpstreamToolArtifactReceipt)
        for receipt in upstream_artifacts
    ):
        raise TypeError(
            "upstream_artifacts must contain UpstreamToolArtifactReceipt values"
        )
    return ToolOutputRecord(
        tool_call_id=plan.tool_call_id,
        policy_version=plan.policy.policy_version,
        raw_byte_count=len(raw_bytes),
        raw_checksum=raw_checksum,
        inline_payload=frozen_inline,
        inline_tokens=inline_tokens,
        offloaded_tokens=max(0, raw_token_estimate - inline_tokens),
        artifact=artifact,
        reason_code=(
            "artifact_offloaded_upstream_preserved"
            if artifact is not None and upstream_artifacts
            else "artifact_offloaded"
            if artifact is not None
            else "upstream_artifact_preserved"
            if upstream_artifacts
            else "bounded_inline_output"
        ),
        upstream_artifacts=upstream_artifacts,
    )


__all__ = [
    "ToolOutputMode",
    "ToolOutputPlan",
    "ToolOutputPolicy",
    "ToolOutputRecord",
    "UpstreamToolArtifactReceipt",
    "bind_tool_output",
    "plan_tool_output",
]
