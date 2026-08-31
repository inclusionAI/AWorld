"""Pure stable-prefix partitioning and cache identity comparison."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
from typing import Iterable

from .frozen_json import canonical_json_hash
from .models import (
    CacheBreakReason,
    CacheIdentity,
    ContextItem,
    InferenceProfile,
    ProviderRequestFidelity,
    RequestCaptureStage,
    Stability,
)


def _sha256_bytes(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _logical_item_identity(item: ContextItem) -> dict[str, str | None]:
    return {
        "item_id": item.id,
        "version": item.version,
        "content_hash": item.content_hash,
    }


class SerializedPrefixProvenance(str, Enum):
    UNKNOWN = "unknown"
    LOGICAL_CANONICAL_JSON = "logical_canonical_json"
    PROVIDER_WIRE_BYTES = "provider_wire_bytes"


@dataclass(frozen=True, slots=True, init=False)
class SerializedPrefixEvidence:
    """Redacted attestation produced only by explicit evidence factories."""

    provenance: SerializedPrefixProvenance
    provider_name: str | None = None
    adapter_identity_hash: str | None = None
    serialization_version: str | None = None
    request_id_hash: str | None = None
    serialized_prefix_hash: str | None = None
    serialized_prefix_bytes: int | None = None
    request_serialized_checksum: str | None = None
    serialized_request_bytes: int | None = None
    capture_stage: RequestCaptureStage = RequestCaptureStage.UNKNOWN
    fidelity: ProviderRequestFidelity = ProviderRequestFidelity.UNKNOWN

    def __init__(self, *args, **kwargs) -> None:
        raise TypeError("use SerializedPrefixEvidence factory methods")

    @classmethod
    def _create(cls, **values: object) -> "SerializedPrefixEvidence":
        evidence = object.__new__(cls)
        for name in cls.__dataclass_fields__:
            object.__setattr__(evidence, name, values.get(name))
        return evidence

    @classmethod
    def unverified(
        cls,
        provenance: SerializedPrefixProvenance = SerializedPrefixProvenance.UNKNOWN,
    ) -> "SerializedPrefixEvidence":
        resolved = SerializedPrefixProvenance(provenance)
        if resolved is SerializedPrefixProvenance.PROVIDER_WIRE_BYTES:
            raise ValueError("provider wire evidence requires provider_wire()")
        return cls._create(
            provenance=resolved,
            capture_stage=RequestCaptureStage.UNKNOWN,
            fidelity=ProviderRequestFidelity.UNKNOWN,
        )

    @classmethod
    def provider_wire(
        cls,
        *,
        serialized_prefix: bytes,
        serialized_request: bytes,
        provider_name: str,
        adapter_identity: str,
        serialization_version: str,
        request_id: str,
    ) -> "SerializedPrefixEvidence":
        if not isinstance(serialized_prefix, bytes):
            raise TypeError("serialized_prefix must be bytes")
        if not isinstance(serialized_request, bytes):
            raise TypeError("serialized_request must be bytes")
        if not serialized_request.startswith(serialized_prefix):
            raise InsufficientSerializedPrefixEvidence(
                f"{InsufficientSerializedPrefixEvidence.code}: not_request_prefix"
            )
        for name, value in (
            ("provider_name", provider_name),
            ("adapter_identity", adapter_identity),
            ("serialization_version", serialization_version),
            ("request_id", request_id),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        return cls._create(
            provenance=SerializedPrefixProvenance.PROVIDER_WIRE_BYTES,
            provider_name=provider_name,
            adapter_identity_hash=canonical_json_hash(
                {"adapter_identity": adapter_identity}
            ),
            serialization_version=serialization_version,
            request_id_hash=canonical_json_hash({"request_id": request_id}),
            serialized_prefix_hash=serialized_prefix_checksum(serialized_prefix),
            serialized_prefix_bytes=len(serialized_prefix),
            request_serialized_checksum=serialized_prefix_checksum(
                serialized_request
            ),
            serialized_request_bytes=len(serialized_request),
            capture_stage=RequestCaptureStage.HTTP_SERIALIZED,
            fidelity=ProviderRequestFidelity.HTTP_SERIALIZED,
        )


class InsufficientSerializedPrefixEvidence(ValueError):
    code = "serialized_prefix_evidence_insufficient"


def _validate_provider_wire_evidence(
    evidence: SerializedPrefixEvidence,
    profile: InferenceProfile,
) -> None:
    sufficient = (
        evidence.provenance is SerializedPrefixProvenance.PROVIDER_WIRE_BYTES
        and evidence.capture_stage is RequestCaptureStage.HTTP_SERIALIZED
        and evidence.fidelity is ProviderRequestFidelity.HTTP_SERIALIZED
        and evidence.provider_name is not None
        and evidence.adapter_identity_hash is not None
        and evidence.serialization_version is not None
        and evidence.request_id_hash is not None
        and evidence.serialized_prefix_hash is not None
        and evidence.serialized_prefix_bytes is not None
        and evidence.request_serialized_checksum is not None
        and evidence.serialized_request_bytes is not None
    )
    if not sufficient:
        raise InsufficientSerializedPrefixEvidence(
            InsufficientSerializedPrefixEvidence.code
        )
    if evidence.provider_name != profile.provider:
        raise InsufficientSerializedPrefixEvidence(
            f"{InsufficientSerializedPrefixEvidence.code}: provider_mismatch"
        )


@dataclass(frozen=True, slots=True)
class ProviderVerifiedCacheIdentity:
    """Runtime-only wrapper; serialized CacheIdentity alone is not verified."""

    identity: CacheIdentity
    evidence: SerializedPrefixEvidence

    def __post_init__(self) -> None:
        if not isinstance(self.identity, CacheIdentity):
            raise TypeError("identity must be a CacheIdentity")
        if not isinstance(self.evidence, SerializedPrefixEvidence):
            raise TypeError("evidence must be SerializedPrefixEvidence")
        _validate_provider_wire_evidence(
            self.evidence,
            self.identity.inference_profile,
        )
        assert self.evidence.serialization_version is not None
        assert self.evidence.serialized_prefix_hash is not None
        if self.identity.serialization_version != self.evidence.serialization_version:
            raise ValueError("identity serialization version does not match evidence")
        if self.identity.serialized_prefix_hash != self.evidence.serialized_prefix_hash:
            raise ValueError("identity serialized prefix hash does not match evidence")

    def to_redacted_dict(self) -> dict[str, object]:
        """Serialize audit metadata without retaining provider request bytes."""
        evidence = self.evidence
        return {
            "identity": self.identity.to_dict(),
            "evidence": {
                "provenance": evidence.provenance.value,
                "capture_stage": evidence.capture_stage.value,
                "fidelity": evidence.fidelity.value,
                "provider_name": evidence.provider_name,
                "adapter_identity_hash": evidence.adapter_identity_hash,
                "serialization_version": evidence.serialization_version,
                "request_id_hash": evidence.request_id_hash,
                "request_serialized_checksum": evidence.request_serialized_checksum,
                "serialized_prefix_bytes": evidence.serialized_prefix_bytes,
                "serialized_request_bytes": evidence.serialized_request_bytes,
            },
        }


@dataclass(frozen=True, slots=True)
class StablePrefixPartition:
    """A contiguous stable request prefix plus its complete dynamic suffix."""

    stable_items: tuple[ContextItem, ...]
    dynamic_items: tuple[ContextItem, ...]
    stable_prefix_hash: str
    dynamic_context_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "stable_items", tuple(self.stable_items))
        object.__setattr__(self, "dynamic_items", tuple(self.dynamic_items))
        if not all(
            isinstance(item, ContextItem)
            for item in (*self.stable_items, *self.dynamic_items)
        ):
            raise TypeError("partition items must contain ContextItem values")
        for name in ("stable_prefix_hash", "dynamic_context_hash"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        if any(
            item.stability not in {Stability.STABLE, Stability.SESSION_STABLE}
            for item in self.stable_items
        ):
            raise ValueError("stable_items must declare stable or session_stable")
        if self.dynamic_items and self.dynamic_items[0].stability in {
            Stability.STABLE,
            Stability.SESSION_STABLE,
        }:
            raise ValueError("dynamic suffix must begin at a non-stable item")
        all_ids = [item.id for item in (*self.stable_items, *self.dynamic_items)]
        if len(set(all_ids)) != len(all_ids):
            raise ValueError("partition item ids must be unique")
        expected_stable = canonical_json_hash(
            [_logical_item_identity(item) for item in self.stable_items]
        )
        expected_dynamic = canonical_json_hash(
            [_logical_item_identity(item) for item in self.dynamic_items]
        )
        if self.stable_prefix_hash != expected_stable:
            raise ValueError("stable_prefix_hash does not match stable_items")
        if self.dynamic_context_hash != expected_dynamic:
            raise ValueError("dynamic_context_hash does not match dynamic_items")


def partition_stable_prefix(
    items: Iterable[ContextItem],
) -> StablePrefixPartition:
    """Partition without reordering; stable items after dynamics stay dynamic.

    Cache stability never changes authority, scope, trust, inclusion, or item
    content. Unknown stability is conservatively dynamic.
    """
    values = tuple(items)
    if not all(isinstance(item, ContextItem) for item in values):
        raise TypeError("items must contain ContextItem values")

    stable: list[ContextItem] = []
    dynamic: list[ContextItem] = []
    dynamic_started = False
    for item in values:
        declared_stable = item.stability in {
            Stability.STABLE,
            Stability.SESSION_STABLE,
        }
        if declared_stable and not dynamic_started:
            stable.append(item)
        else:
            dynamic_started = True
            dynamic.append(item)

    return StablePrefixPartition(
        stable_items=tuple(stable),
        dynamic_items=tuple(dynamic),
        stable_prefix_hash=canonical_json_hash(
            [_logical_item_identity(item) for item in stable]
        ),
        dynamic_context_hash=canonical_json_hash(
            [_logical_item_identity(item) for item in dynamic]
        ),
    )


def serialized_prefix_checksum(serialized_prefix: bytes) -> str:
    """Hash the exact provider-lowered prefix bytes, not a logical object."""
    if not isinstance(serialized_prefix, bytes):
        raise TypeError("serialized_prefix must be bytes")
    return _sha256_bytes(serialized_prefix)


def build_cache_identity(
    *,
    inference_profile: InferenceProfile,
    policy_version: str,
    tool_catalog_hash: str,
    skill_set_hash: str,
    serialized_prefix_evidence: SerializedPrefixEvidence,
    provider_cache_namespace: str | None = None,
) -> ProviderVerifiedCacheIdentity:
    """Build identity only when exact serialized prefix bytes are available."""
    if not isinstance(inference_profile, InferenceProfile):
        raise TypeError("inference_profile must be an InferenceProfile")
    if not isinstance(serialized_prefix_evidence, SerializedPrefixEvidence):
        raise TypeError(
            "serialized_prefix_evidence must be SerializedPrefixEvidence"
        )
    evidence = serialized_prefix_evidence
    _validate_provider_wire_evidence(evidence, inference_profile)
    assert evidence.serialization_version is not None
    assert evidence.serialized_prefix_hash is not None
    return ProviderVerifiedCacheIdentity(
        identity=CacheIdentity(
            inference_profile=inference_profile,
            serialization_version=evidence.serialization_version,
            policy_version=policy_version,
            tool_catalog_hash=tool_catalog_hash,
            skill_set_hash=skill_set_hash,
            serialized_prefix_hash=evidence.serialized_prefix_hash,
            provider_cache_namespace=provider_cache_namespace,
        ),
        evidence=evidence,
    )


def cache_break_reasons(
    previous: CacheIdentity | None,
    current: CacheIdentity,
    *,
    history_compaction: bool = False,
    task_reset: bool = False,
    resume_cache_expired: bool = False,
    provider_cache_unknown: bool = False,
) -> tuple[CacheBreakReason, ...]:
    """Return every deterministic reason in stable diagnostic order."""
    if previous is not None and not isinstance(previous, CacheIdentity):
        raise TypeError("previous must be a CacheIdentity or None")
    if not isinstance(current, CacheIdentity):
        raise TypeError("current must be a CacheIdentity")
    lifecycle_flags = {
        "history_compaction": history_compaction,
        "task_reset": task_reset,
        "resume_cache_expired": resume_cache_expired,
        "provider_cache_unknown": provider_cache_unknown,
    }
    for name, value in lifecycle_flags.items():
        if not isinstance(value, bool):
            raise TypeError(f"{name} must be a boolean")

    reasons: list[CacheBreakReason] = []

    def changed(condition: bool, reason: CacheBreakReason) -> None:
        if condition and reason not in reasons:
            reasons.append(reason)

    if previous is not None:
        before = previous.inference_profile
        after = current.inference_profile
        changed(before.provider != after.provider, CacheBreakReason.PROVIDER_CHANGE)
        changed(before.model != after.model, CacheBreakReason.MODEL_CHANGE)
        changed(
            before.reasoning_effort != after.reasoning_effort,
            CacheBreakReason.EFFORT_CHANGE,
        )
        changed(
            before.execution_mode != after.execution_mode,
            CacheBreakReason.EXECUTION_MODE_CHANGE,
        )
        changed(
            before.response_format_hash != after.response_format_hash,
            CacheBreakReason.RESPONSE_FORMAT_CHANGE,
        )
        changed(
            before.context_limit != after.context_limit,
            CacheBreakReason.CONTEXT_LIMIT_CHANGE,
        )
        changed(
            previous.tool_catalog_hash != current.tool_catalog_hash,
            CacheBreakReason.TOOL_CATALOG_CHANGE,
        )
        changed(
            previous.skill_set_hash != current.skill_set_hash,
            CacheBreakReason.SKILL_SET_CHANGE,
        )
        changed(
            previous.policy_version != current.policy_version,
            CacheBreakReason.POLICY_VERSION_CHANGE,
        )
        changed(
            previous.serialization_version != current.serialization_version,
            CacheBreakReason.SERIALIZATION_CHANGE,
        )
        changed(
            previous.serialized_prefix_hash != current.serialized_prefix_hash,
            CacheBreakReason.SERIALIZED_PREFIX_CHANGE,
        )
        changed(
            previous.provider_cache_namespace != current.provider_cache_namespace,
            CacheBreakReason.PROVIDER_CACHE_NAMESPACE_CHANGE,
        )
    changed(history_compaction, CacheBreakReason.HISTORY_COMPACTION)
    changed(task_reset, CacheBreakReason.TASK_RESET)
    changed(resume_cache_expired, CacheBreakReason.RESUME_CACHE_EXPIRED)
    changed(provider_cache_unknown, CacheBreakReason.PROVIDER_CACHE_UNKNOWN)
    return tuple(reasons)


__all__ = [
    "InsufficientSerializedPrefixEvidence",
    "ProviderVerifiedCacheIdentity",
    "SerializedPrefixEvidence",
    "SerializedPrefixProvenance",
    "StablePrefixPartition",
    "build_cache_identity",
    "cache_break_reasons",
    "partition_stable_prefix",
    "serialized_prefix_checksum",
]
