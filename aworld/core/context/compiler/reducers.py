"""Deterministic reducer receipts and artifact-offload handoff contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re

from .final import ReducerReplacement
from .frozen_json import FrozenJSON, FrozenMap, freeze_json
from .models import ContextItem, ResolutionAction, TokenEstimate


class ReducerKind(str, Enum):
    TRUNCATE_HEAD_TAIL = "truncate_head_tail"
    STRUCTURED_SUMMARY = "structured_summary"
    TOOL_RESULT_SUMMARY = "tool_result_summary"
    HISTORY_DECISION_SUMMARY = "history_decision_summary"
    ARTIFACT_OFFLOAD = "artifact_offload"


@dataclass(frozen=True, slots=True)
class ArtifactReceipt:
    """Receipt created by the artifact owner before pure final compilation."""

    ref: str
    content_hash: str
    byte_count: int
    media_type: str
    retention_policy: str
    source_content_hash: str | None = None

    def __post_init__(self) -> None:
        for name in ("ref", "media_type", "retention_policy"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.content_hash):
            raise ValueError("content_hash must be a canonical sha256 hash")
        if self.source_content_hash is not None and not re.fullmatch(
            r"sha256:[0-9a-f]{64}", self.source_content_hash
        ):
            raise ValueError("source_content_hash must be canonical or None")
        if isinstance(self.byte_count, bool) or not isinstance(self.byte_count, int):
            raise TypeError("byte_count must be an integer")
        if self.byte_count < 0:
            raise ValueError("byte_count must be non-negative")


@dataclass(frozen=True, slots=True)
class ReductionReceipt:
    item_id: str
    expected_content_hash: str
    kind: ReducerKind
    reducer_version: str
    replacement_payload: FrozenJSON
    tokens_before: TokenEstimate
    tokens_after: TokenEstimate
    preserved_fields: tuple[str, ...] = ()
    loss_risks: tuple[str, ...] = ()
    artifact: ArtifactReceipt | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.item_id, str) or not self.item_id.strip():
            raise ValueError("item_id must be a non-empty string")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.expected_content_hash):
            raise ValueError("expected_content_hash must be a canonical sha256 hash")
        object.__setattr__(self, "kind", ReducerKind(self.kind))
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", self.reducer_version):
            raise ValueError("reducer_version must be a stable identifier")
        object.__setattr__(self, "replacement_payload", freeze_json(self.replacement_payload))
        if not isinstance(self.tokens_before, TokenEstimate) or not isinstance(
            self.tokens_after, TokenEstimate
        ):
            raise TypeError("token estimates must be TokenEstimate values")
        object.__setattr__(self, "preserved_fields", tuple(self.preserved_fields))
        object.__setattr__(self, "loss_risks", tuple(self.loss_risks))
        for collection_name in ("preserved_fields", "loss_risks"):
            if any(
                not isinstance(value, str) or not value.strip()
                for value in getattr(self, collection_name)
            ):
                raise ValueError(f"{collection_name} must contain non-empty strings")
        if self.kind is ReducerKind.ARTIFACT_OFFLOAD and self.artifact is None:
            raise ValueError("artifact offload requires an ArtifactReceipt")
        if self.artifact is not None and (
            self.artifact.source_content_hash or self.artifact.content_hash
        ) != self.expected_content_hash:
            raise ValueError("artifact receipt must bind the original item content hash")
        if (
            self.tokens_before.value is not None
            and self.tokens_after.value is not None
            and self.tokens_after.value > self.tokens_before.value
        ):
            raise ValueError("a reducer replacement cannot increase token count")

    def to_replacement(self) -> ReducerReplacement:
        return ReducerReplacement(
            item_id=self.item_id,
            expected_content_hash=self.expected_content_hash,
            replacement_payload=self.replacement_payload,
            replacement_tokens=self.tokens_after,
            reducer_identity=f"{self.kind.value}-{self.reducer_version}",
            action=(
                ResolutionAction.OFFLOADED
                if self.kind is ReducerKind.ARTIFACT_OFFLOAD
                else ResolutionAction.COMPACTED
            ),
            artifact_ref=self.artifact.ref if self.artifact is not None else None,
        )


def truncate_head_tail(
    *,
    item: ContextItem,
    tokens_before: TokenEstimate,
    tokens_after: TokenEstimate,
    head_chars: int,
    tail_chars: int,
    reducer_version: str = "v1",
) -> ReductionReceipt:
    """Pure bounded reducer for string payloads; never reads an artifact."""
    if not isinstance(item.payload, str):
        raise TypeError("truncate_head_tail requires a string payload")
    for name, value in (("head_chars", head_chars), ("tail_chars", tail_chars)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")
    payload = item.payload
    omitted = max(0, len(payload) - head_chars - tail_chars)
    replacement = (
        payload
        if omitted == 0
        else payload[:head_chars]
        + f"\n<aworld-context-omitted chars={omitted}>\n"
        + (payload[-tail_chars:] if tail_chars else "")
    )
    return ReductionReceipt(
        item_id=item.id,
        expected_content_hash=item.content_hash or "",
        kind=ReducerKind.TRUNCATE_HEAD_TAIL,
        reducer_version=reducer_version,
        replacement_payload=replacement,
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        preserved_fields=("head", "tail", "omitted_char_count"),
        loss_risks=(("middle_content_omitted",) if omitted else ()),
    )


def artifact_offload_receipt(
    *,
    item: ContextItem,
    inline_payload: FrozenJSON,
    tokens_before: TokenEstimate,
    tokens_after: TokenEstimate,
    artifact: ArtifactReceipt,
    preserved_fields: tuple[str, ...],
    loss_risks: tuple[str, ...] = (),
    reducer_version: str = "v1",
) -> ReductionReceipt:
    """Bind an owner-created artifact to the bounded inline replacement."""
    frozen = freeze_json(inline_payload)
    if isinstance(frozen, FrozenMap) and "artifact_ref" in frozen:
        if frozen["artifact_ref"] != artifact.ref:
            raise ValueError("inline artifact_ref does not match the receipt")
    return ReductionReceipt(
        item_id=item.id,
        expected_content_hash=item.content_hash or "",
        kind=ReducerKind.ARTIFACT_OFFLOAD,
        reducer_version=reducer_version,
        replacement_payload=frozen,
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        preserved_fields=preserved_fields,
        loss_risks=loss_risks,
        artifact=artifact,
    )


__all__ = [
    "ArtifactReceipt",
    "ReducerKind",
    "ReductionReceipt",
    "artifact_offload_receipt",
    "truncate_head_tail",
]
