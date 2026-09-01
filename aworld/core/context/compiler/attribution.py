"""Privacy-safe occurrence attribution for provider-bound Context bytes.

The compiler plan contains provenance metadata and hashes only.  Provider
adapters bind that plan to the actual ordinal values they are about to send;
they never search payload text or use a content hash to choose provenance.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, ClassVar, Iterable

from .frozen_json import canonical_json_bytes, canonical_json_hash
from .models import (
    ContextItem,
    ContextKind,
    ProviderRequestSnapshot,
    SourceKind,
    Stability,
    TokenEstimate,
)


_SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}")


class AttributionOwnerCode(str, Enum):
    UNKNOWN = "unknown"
    MODEL_FINAL_MESSAGES = "model_final_messages"
    MODEL_FINAL_TOOL_CATALOG = "model_final_tool_catalog"
    SCOPED_INSTRUCTION = "scoped_instruction"
    PROGRESSIVE_SKILL = "progressive_skill"
    DELEGATION_CONTEXT = "delegation_context"
    AMNI_FOLDED_SYSTEM = "amni_folded_system"


class AttributionCollection(str, Enum):
    MESSAGES = "messages"
    TOOLS = "tools"


class LogicalResidency(str, Enum):
    STABLE = "stable"
    DYNAMIC = "dynamic"


class AttributionSerialization(str, Enum):
    PROVIDER_PREPARED_CANONICAL_JSON = "provider_prepared_canonical_json"
    HTTP_SERIALIZED_CANONICAL_JSON = "http_serialized_canonical_json"


@dataclass(frozen=True, slots=True)
class ContextAttributionPlanEntry:
    """One emitted occurrence; deliberately contains no raw owner data."""

    item_identity_hash: str
    owner_code: AttributionOwnerCode
    kind: str
    source_kind: str
    stability: str
    collection: AttributionCollection
    ordinal: int
    content_hash: str
    token_estimate: TokenEstimate
    residency: LogicalResidency

    def __post_init__(self) -> None:
        for name in ("item_identity_hash", "content_hash"):
            if not isinstance(getattr(self, name), str) or not _SHA256_RE.fullmatch(
                getattr(self, name)
            ):
                raise ValueError(f"{name} must be a canonical sha256 hash")
        object.__setattr__(self, "owner_code", AttributionOwnerCode(self.owner_code))
        object.__setattr__(self, "collection", AttributionCollection(self.collection))
        object.__setattr__(self, "residency", LogicalResidency(self.residency))
        if isinstance(self.ordinal, bool) or not isinstance(self.ordinal, int) or self.ordinal < 0:
            raise ValueError("ordinal must be a non-negative integer")
        for name in ("kind", "source_kind", "stability"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty code")
        if isinstance(self.token_estimate, dict):
            object.__setattr__(
                self, "token_estimate", TokenEstimate.from_dict(self.token_estimate)
            )
        if not isinstance(self.token_estimate, TokenEstimate):
            raise TypeError("token_estimate must be a TokenEstimate")

    @classmethod
    def from_item(
        cls,
        *,
        item: ContextItem,
        owner_code: AttributionOwnerCode,
        collection: AttributionCollection,
        ordinal: int,
        token_estimate: TokenEstimate,
        residency: LogicalResidency,
    ) -> "ContextAttributionPlanEntry":
        return cls(
            item_identity_hash=canonical_json_hash({"item_id": item.id}),
            owner_code=owner_code,
            kind=item.kind.value,
            source_kind=item.source.kind.value,
            stability=item.stability.value,
            collection=collection,
            ordinal=ordinal,
            content_hash=item.content_hash or canonical_json_hash(item.payload),
            token_estimate=token_estimate,
            residency=residency,
        )

    def to_redacted_dict(self) -> dict[str, Any]:
        return {
            "item_identity_hash": self.item_identity_hash,
            "owner_code": self.owner_code.value,
            "kind": self.kind,
            "source_kind": self.source_kind,
            "stability": self.stability,
            "collection": self.collection.value,
            "ordinal": self.ordinal,
            "content_hash": self.content_hash,
            "token_estimate": self.token_estimate.to_dict(),
            "residency": self.residency.value,
        }


@dataclass(frozen=True, slots=True)
class ProviderRequestAttributionPlan:
    SCHEMA_VERSION: ClassVar[str] = "aworld.context.attribution-plan.v1"

    request_id_hash: str
    candidate_content_hash: str
    entries: tuple[ContextAttributionPlanEntry, ...]

    def __post_init__(self) -> None:
        for name in ("request_id_hash", "candidate_content_hash"):
            if not isinstance(getattr(self, name), str) or not _SHA256_RE.fullmatch(
                getattr(self, name)
            ):
                raise ValueError(f"{name} must be a canonical sha256 hash")
        object.__setattr__(self, "entries", tuple(self.entries))
        if not all(isinstance(value, ContextAttributionPlanEntry) for value in self.entries):
            raise TypeError("entries must contain ContextAttributionPlanEntry values")
        positions = [(value.collection, value.ordinal) for value in self.entries]
        if len(set(positions)) != len(positions):
            raise ValueError("attribution entries must have unique collection ordinals")
        for collection in AttributionCollection:
            ordinals = sorted(
                value.ordinal for value in self.entries if value.collection is collection
            )
            if ordinals != list(range(len(ordinals))):
                raise ValueError("attribution collection ordinals must be contiguous")

    def to_redacted_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "request_id_hash": self.request_id_hash,
            "candidate_content_hash": self.candidate_content_hash,
            "entry_count": len(self.entries),
            "entries": [entry.to_redacted_dict() for entry in self.entries],
        }


@dataclass(frozen=True, slots=True)
class ProviderRequestAttributionEntry:
    plan: ContextAttributionPlanEntry
    canonical_value_bytes: int

    def __post_init__(self) -> None:
        if not isinstance(self.plan, ContextAttributionPlanEntry):
            raise TypeError("plan must be a ContextAttributionPlanEntry")
        if (
            isinstance(self.canonical_value_bytes, bool)
            or not isinstance(self.canonical_value_bytes, int)
            or self.canonical_value_bytes < 0
        ):
            raise ValueError("canonical_value_bytes must be non-negative")

    def to_redacted_dict(self) -> dict[str, Any]:
        return {
            **self.plan.to_redacted_dict(),
            "canonical_value_bytes": self.canonical_value_bytes,
        }


@dataclass(frozen=True, slots=True)
class ProviderRequestAttributionReceipt:
    SCHEMA_VERSION: ClassVar[str] = "aworld.context.provider-attribution.v1"
    OVERHEAD_BUCKET: ClassVar[str] = "provider_envelope_and_params"

    serialization: AttributionSerialization
    provider_request_content_hash: str
    canonical_request_checksum: str
    total_canonical_bytes: int
    attributed_value_bytes: int
    provider_envelope_and_params_bytes: int
    entries: tuple[ProviderRequestAttributionEntry, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "serialization", AttributionSerialization(self.serialization))
        for name in ("provider_request_content_hash", "canonical_request_checksum"):
            if not isinstance(getattr(self, name), str) or not _SHA256_RE.fullmatch(
                getattr(self, name)
            ):
                raise ValueError(f"{name} must be a canonical sha256 hash")
        object.__setattr__(self, "entries", tuple(self.entries))
        if not all(isinstance(value, ProviderRequestAttributionEntry) for value in self.entries):
            raise TypeError("entries must contain ProviderRequestAttributionEntry values")
        for name in (
            "total_canonical_bytes",
            "attributed_value_bytes",
            "provider_envelope_and_params_bytes",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.attributed_value_bytes != sum(
            value.canonical_value_bytes for value in self.entries
        ):
            raise ValueError("attributed bytes do not equal entry bytes")
        if (
            self.attributed_value_bytes + self.provider_envelope_and_params_bytes
            != self.total_canonical_bytes
        ):
            raise ValueError("attribution bytes do not conserve canonical request bytes")

    def to_redacted_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "status": "available",
            "serialization": self.serialization.value,
            "provider_request_content_hash": self.provider_request_content_hash,
            "canonical_request_checksum": self.canonical_request_checksum,
            "total_canonical_bytes": self.total_canonical_bytes,
            "attributed_value_bytes": self.attributed_value_bytes,
            self.OVERHEAD_BUCKET: self.provider_envelope_and_params_bytes,
            "byte_conservation": True,
            "entry_count": len(self.entries),
            "entries": [entry.to_redacted_dict() for entry in self.entries],
        }


class ProviderAttributionMismatch(ValueError):
    code = "provider_attribution_mismatch"


def build_provider_attribution_receipt(
    *,
    plan: ProviderRequestAttributionPlan,
    provider_request: dict[str, Any],
    serialization: AttributionSerialization,
    canonical_request_body: bytes | None = None,
) -> ProviderRequestAttributionReceipt:
    """Bind plan entries by collection+ordinal and verify hashes in place."""
    if not isinstance(plan, ProviderRequestAttributionPlan):
        raise TypeError("plan must be a ProviderRequestAttributionPlan")
    canonical_body = canonical_json_bytes(provider_request)
    if canonical_request_body is not None and canonical_request_body != canonical_body:
        raise ProviderAttributionMismatch(ProviderAttributionMismatch.code)
    entries: list[ProviderRequestAttributionEntry] = []
    expected_counts = {
        collection: sum(entry.collection is collection for entry in plan.entries)
        for collection in AttributionCollection
    }
    for collection in AttributionCollection:
        raw = provider_request.get(collection.value)
        values = [] if raw is None else raw
        if not isinstance(values, list) or len(values) != expected_counts[collection]:
            raise ProviderAttributionMismatch(ProviderAttributionMismatch.code)
    for plan_entry in plan.entries:
        value = provider_request[plan_entry.collection.value][plan_entry.ordinal]
        if canonical_json_hash(value) != plan_entry.content_hash:
            raise ProviderAttributionMismatch(ProviderAttributionMismatch.code)
        entries.append(
            ProviderRequestAttributionEntry(
                plan=plan_entry,
                canonical_value_bytes=len(canonical_json_bytes(value)),
            )
        )
    attributed = sum(entry.canonical_value_bytes for entry in entries)
    total = len(canonical_body)
    if attributed > total:
        raise ProviderAttributionMismatch(ProviderAttributionMismatch.code)
    return ProviderRequestAttributionReceipt(
        serialization=serialization,
        provider_request_content_hash=canonical_json_hash(provider_request),
        canonical_request_checksum=canonical_json_hash(provider_request),
        total_canonical_bytes=total,
        attributed_value_bytes=attributed,
        provider_envelope_and_params_bytes=total - attributed,
        entries=tuple(entries),
    )


def build_unknown_attribution_plan(
    snapshot: ProviderRequestSnapshot,
) -> ProviderRequestAttributionPlan:
    """Compatibility plan for reviewed candidates without final compiler provenance."""
    if not isinstance(snapshot, ProviderRequestSnapshot):
        raise TypeError("snapshot must be a ProviderRequestSnapshot")
    payload = snapshot.payload
    entries: list[ContextAttributionPlanEntry] = []
    for collection in AttributionCollection:
        values = payload.get(collection.value)
        if values is None:
            values = ()
        if not isinstance(values, tuple):
            raise TypeError("candidate message/tool collections must be arrays or null")
        for ordinal, value in enumerate(values):
            kind = (
                ContextKind.TOOL_CATALOG.value
                if collection is AttributionCollection.TOOLS
                else ContextKind.UNKNOWN.value
            )
            entries.append(
                ContextAttributionPlanEntry(
                    item_identity_hash=canonical_json_hash(
                        {"collection": collection.value, "ordinal": ordinal}
                    ),
                    owner_code=AttributionOwnerCode.UNKNOWN,
                    kind=kind,
                    source_kind=SourceKind.UNKNOWN.value,
                    stability=Stability.UNKNOWN.value,
                    collection=collection,
                    ordinal=ordinal,
                    content_hash=canonical_json_hash(value),
                    token_estimate=TokenEstimate(
                        value=(len(canonical_json_bytes(value)) + 3) // 4,
                        estimator="aworld-canonical-json-byte4-v1",
                        exact=False,
                    ),
                    residency=LogicalResidency.DYNAMIC,
                )
            )
    return ProviderRequestAttributionPlan(
        request_id_hash=canonical_json_hash({"request_id": snapshot.request_id}),
        candidate_content_hash=snapshot.content_hash,
        entries=tuple(entries),
    )


def summarize_attribution_plan(
    entries: Iterable[ContextAttributionPlanEntry],
) -> dict[str, Any]:
    values = tuple(entries)
    return {
        "entry_count": len(values),
        "by_collection": {
            collection.value: sum(value.collection is collection for value in values)
            for collection in AttributionCollection
        },
        "unknown_owner_count": sum(
            value.owner_code is AttributionOwnerCode.UNKNOWN for value in values
        ),
    }


__all__ = [
    "AttributionCollection",
    "AttributionOwnerCode",
    "AttributionSerialization",
    "ContextAttributionPlanEntry",
    "LogicalResidency",
    "ProviderAttributionMismatch",
    "ProviderRequestAttributionEntry",
    "ProviderRequestAttributionPlan",
    "ProviderRequestAttributionReceipt",
    "build_provider_attribution_receipt",
    "build_unknown_attribution_plan",
    "summarize_attribution_plan",
]
