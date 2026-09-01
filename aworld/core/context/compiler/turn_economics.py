"""Immutable, privacy-safe receipts for turn and artifact economics."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import re
from typing import Any

from .frozen_json import canonical_json_hash


_SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}")


def hashed_identity(kind: str, value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{kind} identity must be non-empty")
    return canonical_json_hash({kind: value})


class TurnKind(str, Enum):
    MODEL = "model"
    TOOL = "tool"


class TurnCauseCode(str, Enum):
    INITIAL_INPUT = "initial_input"
    MODEL_CHOICE = "model_choice"
    VALIDATION_REPAIR = "validation_repair"
    FRAMEWORK_RETRY = "framework_retry"
    DEFERRED_CATALOG_EXPANSION = "deferred_catalog_expansion"
    DEFERRED_SKILL_EXPANSION = "deferred_skill_expansion"
    ARTIFACT_RETRIEVAL = "artifact_retrieval"
    UNAVAILABLE = "unavailable"


SUPPORTED_TURN_CAUSES = frozenset({
    TurnCauseCode.INITIAL_INPUT,
    TurnCauseCode.MODEL_CHOICE,
    TurnCauseCode.FRAMEWORK_RETRY,
    TurnCauseCode.ARTIFACT_RETRIEVAL,
})


@dataclass(frozen=True, slots=True)
class TurnEconomicsReceipt:
    task_epoch: int
    turn_kind: TurnKind
    cause: TurnCauseCode
    turn_id_hash: str
    request_id_hash: str | None = None
    tool_call_id_hash: str | None = None
    parent_turn_id_hash: str | None = None
    evidence_hash: str | None = None
    cause_supported: bool = True

    SCHEMA_VERSION = "aworld.context.turn-economics.v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "turn_kind", TurnKind(self.turn_kind))
        object.__setattr__(self, "cause", TurnCauseCode(self.cause))
        if isinstance(self.task_epoch, bool) or not isinstance(self.task_epoch, int) or self.task_epoch < 0:
            raise ValueError("task_epoch must be non-negative")
        for name in (
            "turn_id_hash", "request_id_hash", "tool_call_id_hash",
            "parent_turn_id_hash", "evidence_hash",
        ):
            value = getattr(self, name)
            if value is not None and not _SHA256_RE.fullmatch(value):
                raise ValueError(f"{name} must be canonical or None")
        if not isinstance(self.cause_supported, bool):
            raise TypeError("cause_supported must be boolean")
        if self.cause in SUPPORTED_TURN_CAUSES and not self.cause_supported:
            raise ValueError("implemented causes cannot be marked unsupported")
        if self.cause not in SUPPORTED_TURN_CAUSES and self.cause_supported:
            raise ValueError("unimplemented causes must be explicitly unsupported")
        if self.turn_kind is TurnKind.MODEL and self.request_id_hash is None:
            raise ValueError("model turn requires request_id_hash")
        if self.turn_kind is TurnKind.TOOL and self.tool_call_id_hash is None:
            raise ValueError("tool turn requires tool_call_id_hash")

    def to_redacted_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "task_epoch": self.task_epoch,
            "turn_kind": self.turn_kind.value,
            "cause": self.cause.value,
            "cause_supported": self.cause_supported,
            "turn_id_hash": self.turn_id_hash,
            "request_id_hash": self.request_id_hash,
            "tool_call_id_hash": self.tool_call_id_hash,
            "parent_turn_id_hash": self.parent_turn_id_hash,
            "evidence_hash": self.evidence_hash,
        }


@dataclass(frozen=True, slots=True)
class ArtifactRetrievalPlan:
    owner_tool: str
    retrieval_action: str
    artifact_ref: str
    artifact_content_hash: str
    artifact_byte_count: int
    offset: int
    limit: int
    consumer_tool_call_id_hash: str

    SCHEMA_VERSION = "aworld.context.artifact-retrieval-plan.v1"

    def __post_init__(self) -> None:
        for name in ("owner_tool", "retrieval_action", "artifact_ref"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty")
        for name in ("artifact_content_hash", "consumer_tool_call_id_hash"):
            if not _SHA256_RE.fullmatch(getattr(self, name)):
                raise ValueError(f"{name} must be canonical")
        for name in ("artifact_byte_count", "offset", "limit"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.limit <= 0 or self.offset > self.artifact_byte_count:
            raise ValueError("retrieval range is invalid")

    @property
    def fingerprint(self) -> str:
        return canonical_json_hash(self.to_redacted_dict())

    def to_redacted_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "owner_code": canonical_json_hash({"owner_tool": self.owner_tool}),
            "action_code": canonical_json_hash({"retrieval_action": self.retrieval_action}),
            "artifact_ref_hash": canonical_json_hash({"artifact_ref": self.artifact_ref}),
            "artifact_content_hash": self.artifact_content_hash,
            "artifact_byte_count": self.artifact_byte_count,
            "offset": self.offset,
            "limit": self.limit,
            "consumer_tool_call_id_hash": self.consumer_tool_call_id_hash,
        }


@dataclass(frozen=True, slots=True)
class ArtifactRetrievalReceipt:
    plan: ArtifactRetrievalPlan
    returned_offset: int
    next_offset: int
    returned_byte_count: int
    chunk_checksum: str
    source_content_hash: str
    result_content_hash: str
    complete: bool
    next_request_id_hash: str | None = None
    consumed_content_hash: str | None = None

    SCHEMA_VERSION = "aworld.context.artifact-retrieval-receipt.v1"

    def __post_init__(self) -> None:
        if not isinstance(self.plan, ArtifactRetrievalPlan):
            raise TypeError("plan must be ArtifactRetrievalPlan")
        for name in ("returned_offset", "next_offset", "returned_byte_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.returned_offset != self.plan.offset:
            raise ValueError("retrieval offset does not match plan")
        if self.next_offset - self.returned_offset != self.returned_byte_count:
            raise ValueError("retrieval range does not match returned bytes")
        if self.returned_byte_count > self.plan.limit or self.next_offset > self.plan.artifact_byte_count:
            raise ValueError("retrieval result exceeds authorized range")
        for name in (
            "chunk_checksum", "source_content_hash", "result_content_hash",
            "next_request_id_hash", "consumed_content_hash",
        ):
            value = getattr(self, name)
            if value is not None and not _SHA256_RE.fullmatch(value):
                raise ValueError(f"{name} must be canonical or None")
        if self.source_content_hash != self.plan.artifact_content_hash:
            raise ValueError("retrieval source checksum mismatch")
        if not isinstance(self.complete, bool):
            raise TypeError("complete must be boolean")
        if (self.next_request_id_hash is None) != (self.consumed_content_hash is None):
            raise ValueError("next request consumption evidence must be atomic")

    @property
    def consumed(self) -> bool:
        return self.next_request_id_hash is not None

    def bind_consumption(self, *, request_id: str, content_hash: str) -> "ArtifactRetrievalReceipt":
        if content_hash != self.result_content_hash:
            raise ValueError("retrieval consumption content mismatch")
        return replace(
            self,
            next_request_id_hash=hashed_identity("request_id", request_id),
            consumed_content_hash=content_hash,
        )

    def to_redacted_dict(self) -> dict[str, Any]:
        plan = self.plan.to_redacted_dict()
        plan.pop("schema_version", None)
        return {
            **plan,
            "schema_version": self.SCHEMA_VERSION,
            "plan_fingerprint": self.plan.fingerprint,
            "returned_offset": self.returned_offset,
            "next_offset": self.next_offset,
            "returned_byte_count": self.returned_byte_count,
            "chunk_checksum": self.chunk_checksum,
            "source_content_hash": self.source_content_hash,
            "result_content_hash": self.result_content_hash,
            "complete": self.complete,
            "next_request_id_hash": self.next_request_id_hash,
            "consumed_content_hash": self.consumed_content_hash,
            "consumed": self.consumed,
        }


def turn_cause_support() -> dict[str, bool]:
    return {
        cause.value: cause in SUPPORTED_TURN_CAUSES
        for cause in TurnCauseCode
        if cause is not TurnCauseCode.UNAVAILABLE
    }


__all__ = [
    "ArtifactRetrievalPlan", "ArtifactRetrievalReceipt", "TurnCauseCode",
    "TurnEconomicsReceipt", "TurnKind", "hashed_identity", "turn_cause_support",
]
