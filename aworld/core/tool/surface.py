"""Immutable live-registration receipts for model-visible Tool capabilities.

This module deliberately does not discover or execute Tools.  The Tool owner
supplies its final provider-visible schemas and, for capabilities that need an
execution canary, a typed probe result.  Reconciliation is exact and contains
no task-text, description, or fuzzy-name inference.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import re
from typing import Any, ClassVar, Iterable, Mapping, Sequence


_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def _require_identifier(field: str, value: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{field} must be a stable identifier")


class ToolLifecycle(str, Enum):
    """Lifecycle needed by a Tool capability."""

    IMMEDIATE = "immediate"
    DURABLE = "durable"
    BACKGROUND = "background"


class CapabilityMatch(str, Enum):
    """How a capability binds to its explicitly declared schema ids."""

    ALL = "all"
    ANY = "any"


class CapabilityStatus(str, Enum):
    AVAILABLE = "available"
    PROFILE_EXCLUDED = "profile_excluded"
    SCHEMA_MISSING = "schema_missing"
    SCHEMA_AMBIGUOUS = "schema_ambiguous"
    PROBE_MISSING = "probe_missing"
    PROBE_FAILED = "probe_failed"


@dataclass(frozen=True, slots=True)
class ToolSurfaceProfile:
    """Execution-lifecycle policy, independent from task identity or text."""

    profile_id: str = "general"
    allowed_lifecycles: tuple[ToolLifecycle, ...] = (
        ToolLifecycle.IMMEDIATE,
        ToolLifecycle.DURABLE,
        ToolLifecycle.BACKGROUND,
    )

    def __post_init__(self) -> None:
        _require_identifier("profile_id", self.profile_id)
        lifecycles = tuple(ToolLifecycle(value) for value in self.allowed_lifecycles)
        if len(set(lifecycles)) != len(lifecycles):
            raise ValueError("allowed_lifecycles must be unique")
        object.__setattr__(self, "allowed_lifecycles", lifecycles)


@dataclass(frozen=True, slots=True)
class ToolCapabilitySpec:
    """Exact schema closure and health requirements for one advertised name."""

    capability_id: str
    schema_ids: tuple[str, ...]
    lifecycle: ToolLifecycle = ToolLifecycle.IMMEDIATE
    match: CapabilityMatch = CapabilityMatch.ALL
    requires_probe: bool = False
    required: bool = False

    def __post_init__(self) -> None:
        _require_identifier("capability_id", self.capability_id)
        if isinstance(self.schema_ids, (str, bytes)):
            raise TypeError("schema_ids must be a sequence of identifiers")
        schema_ids = tuple(self.schema_ids)
        if not schema_ids:
            raise ValueError("schema_ids must not be empty")
        if len(set(schema_ids)) != len(schema_ids):
            raise ValueError("schema_ids must be unique")
        for schema_id in schema_ids:
            _require_identifier("schema id", schema_id)
        object.__setattr__(self, "schema_ids", schema_ids)
        object.__setattr__(self, "lifecycle", ToolLifecycle(self.lifecycle))
        object.__setattr__(self, "match", CapabilityMatch(self.match))
        if not isinstance(self.requires_probe, bool) or not isinstance(self.required, bool):
            raise TypeError("requires_probe and required must be booleans")


@dataclass(frozen=True, slots=True)
class CapabilityProbe:
    """Result of an owner-executed, capability-specific runtime canary."""

    capability_id: str
    succeeded: bool
    probe_id: str
    reason_code: str | None = None

    def __post_init__(self) -> None:
        _require_identifier("capability_id", self.capability_id)
        _require_identifier("probe_id", self.probe_id)
        if not isinstance(self.succeeded, bool):
            raise TypeError("succeeded must be a boolean")
        if self.reason_code is not None:
            _require_identifier("reason_code", self.reason_code)
        if self.succeeded and self.reason_code is not None:
            raise ValueError("successful probes cannot carry a failure reason")
        if not self.succeeded and self.reason_code is None:
            raise ValueError("failed probes require a reason_code")


@dataclass(frozen=True, slots=True)
class ToolCapabilityEvidence:
    capability_id: str
    status: CapabilityStatus
    lifecycle: ToolLifecycle
    required: bool
    declared_schema_ids: tuple[str, ...]
    matched_schema_ids: tuple[str, ...]
    missing_schema_ids: tuple[str, ...]
    probe_id: str | None = None
    reason_code: str | None = None

    def __post_init__(self) -> None:
        _require_identifier("capability_id", self.capability_id)
        object.__setattr__(self, "status", CapabilityStatus(self.status))
        object.__setattr__(self, "lifecycle", ToolLifecycle(self.lifecycle))
        for schema_ids in (
            self.declared_schema_ids,
            self.matched_schema_ids,
            self.missing_schema_ids,
        ):
            if isinstance(schema_ids, (str, bytes)):
                raise TypeError("schema id collections must be sequences")
        object.__setattr__(self, "declared_schema_ids", tuple(self.declared_schema_ids))
        object.__setattr__(self, "matched_schema_ids", tuple(self.matched_schema_ids))
        object.__setattr__(self, "missing_schema_ids", tuple(self.missing_schema_ids))
        if not isinstance(self.required, bool):
            raise TypeError("required must be a boolean")
        if not self.declared_schema_ids:
            raise ValueError("declared_schema_ids must not be empty")
        for field_name, schema_ids in (
            ("declared_schema_ids", self.declared_schema_ids),
            ("matched_schema_ids", self.matched_schema_ids),
            ("missing_schema_ids", self.missing_schema_ids),
        ):
            if len(set(schema_ids)) != len(schema_ids):
                raise ValueError(f"{field_name} must be unique")
        for schema_id in self.declared_schema_ids:
            _require_identifier("schema id", schema_id)
        if not set(self.matched_schema_ids).issubset(self.declared_schema_ids):
            raise ValueError("matched schema ids must be declared")
        if not set(self.missing_schema_ids).issubset(self.declared_schema_ids):
            raise ValueError("missing schema ids must be declared")
        if set(self.matched_schema_ids) & set(self.missing_schema_ids):
            raise ValueError("schema ids cannot be both matched and missing")
        if set(self.matched_schema_ids) | set(self.missing_schema_ids) != set(
            self.declared_schema_ids
        ):
            raise ValueError("matched and missing schema ids must partition declarations")
        if self.probe_id is not None:
            _require_identifier("probe_id", self.probe_id)
        if self.reason_code is not None:
            _require_identifier("reason_code", self.reason_code)
        if self.status is CapabilityStatus.AVAILABLE and self.reason_code is not None:
            raise ValueError("available capability evidence cannot carry a reason_code")
        if self.status is not CapabilityStatus.AVAILABLE and self.reason_code is None:
            raise ValueError("unavailable capability evidence requires a reason_code")
        if self.status is CapabilityStatus.PROBE_FAILED and self.probe_id is None:
            raise ValueError("failed probe evidence requires a probe_id")
        if self.status is CapabilityStatus.PROBE_MISSING and self.probe_id is not None:
            raise ValueError("missing probe evidence cannot carry a probe_id")

    @property
    def advertised(self) -> bool:
        return self.status is CapabilityStatus.AVAILABLE

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "status": self.status.value,
            "lifecycle": self.lifecycle.value,
            "required": self.required,
            "declared_schema_ids": list(self.declared_schema_ids),
            "matched_schema_ids": list(self.matched_schema_ids),
            "missing_schema_ids": list(self.missing_schema_ids),
            "probe_id": self.probe_id,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True, slots=True)
class ToolSurfaceReceipt:
    """One immutable reconciliation of advertised intent and live schemas."""

    SCHEMA_VERSION: ClassVar[str] = "aworld.tool-surface-receipt.v1"

    profile_id: str
    live_schema_ids: tuple[str, ...]
    duplicate_schema_ids: tuple[str, ...]
    malformed_schema_count: int
    evidence: tuple[ToolCapabilityEvidence, ...]
    receipt_hash: str

    def __post_init__(self) -> None:
        _require_identifier("profile_id", self.profile_id)
        if isinstance(self.live_schema_ids, (str, bytes)) or isinstance(
            self.duplicate_schema_ids, (str, bytes)
        ):
            raise TypeError("schema id collections must be sequences")
        object.__setattr__(self, "live_schema_ids", tuple(self.live_schema_ids))
        object.__setattr__(
            self, "duplicate_schema_ids", tuple(self.duplicate_schema_ids)
        )
        object.__setattr__(self, "evidence", tuple(self.evidence))
        if (
            isinstance(self.malformed_schema_count, bool)
            or not isinstance(self.malformed_schema_count, int)
            or self.malformed_schema_count < 0
        ):
            raise ValueError("malformed_schema_count must be non-negative")
        if not all(isinstance(item, ToolCapabilityEvidence) for item in self.evidence):
            raise TypeError("evidence must contain ToolCapabilityEvidence values")
        for field_name, schema_ids in (
            ("live_schema_ids", self.live_schema_ids),
            ("duplicate_schema_ids", self.duplicate_schema_ids),
        ):
            for schema_id in schema_ids:
                _require_identifier(field_name, schema_id)
        if len({item.capability_id for item in self.evidence}) != len(self.evidence):
            raise ValueError("evidence capability ids must be unique")
        counts = Counter(self.live_schema_ids)
        expected_duplicates = tuple(
            sorted(name for name, count in counts.items() if count > 1)
        )
        if expected_duplicates != self.duplicate_schema_ids:
            raise ValueError("duplicate_schema_ids do not match live schemas")
        expected_hash = _canonical_hash(_receipt_hash_payload(
            profile_id=self.profile_id,
            live_schema_ids=self.live_schema_ids,
            duplicate_schema_ids=self.duplicate_schema_ids,
            malformed_schema_count=self.malformed_schema_count,
            evidence=self.evidence,
        ))
        if self.receipt_hash != expected_hash:
            raise ValueError("Tool surface receipt hash mismatch")

    @property
    def advertised_capability_ids(self) -> tuple[str, ...]:
        return tuple(item.capability_id for item in self.evidence if item.advertised)

    @property
    def ready(self) -> bool:
        unhealthy = {
            CapabilityStatus.SCHEMA_MISSING,
            CapabilityStatus.SCHEMA_AMBIGUOUS,
            CapabilityStatus.PROBE_MISSING,
            CapabilityStatus.PROBE_FAILED,
        }
        return not any(item.required and item.status in unhealthy for item in self.evidence)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "profile_id": self.profile_id,
            "live_schema_ids": list(self.live_schema_ids),
            "duplicate_schema_ids": list(self.duplicate_schema_ids),
            "malformed_schema_count": self.malformed_schema_count,
            "evidence": [item.to_dict() for item in self.evidence],
            "ready": self.ready,
            "advertised_capability_ids": list(self.advertised_capability_ids),
            "receipt_hash": self.receipt_hash,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ToolSurfaceReceipt":
        expected_keys = {
            "schema_version",
            "profile_id",
            "live_schema_ids",
            "duplicate_schema_ids",
            "malformed_schema_count",
            "evidence",
            "ready",
            "advertised_capability_ids",
            "receipt_hash",
        }
        if not isinstance(value, Mapping) or set(value) != expected_keys:
            raise ValueError("invalid Tool surface receipt")
        if value["schema_version"] != cls.SCHEMA_VERSION:
            raise ValueError("unsupported Tool surface receipt schema")
        try:
            raw_evidence = value["evidence"]
            if isinstance(raw_evidence, (str, bytes)):
                raise TypeError
            evidence = tuple(
                ToolCapabilityEvidence(
                    capability_id=item["capability_id"],
                    status=CapabilityStatus(item["status"]),
                    lifecycle=ToolLifecycle(item["lifecycle"]),
                    required=item["required"],
                    declared_schema_ids=tuple(item["declared_schema_ids"]),
                    matched_schema_ids=tuple(item["matched_schema_ids"]),
                    missing_schema_ids=tuple(item["missing_schema_ids"]),
                    probe_id=item["probe_id"],
                    reason_code=item["reason_code"],
                )
                for item in raw_evidence
            )
            receipt = cls(
                profile_id=value["profile_id"],
                live_schema_ids=tuple(value["live_schema_ids"]),
                duplicate_schema_ids=tuple(value["duplicate_schema_ids"]),
                malformed_schema_count=value["malformed_schema_count"],
                evidence=evidence,
                receipt_hash=value["receipt_hash"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid Tool surface receipt payload") from exc
        if value["ready"] != receipt.ready:
            raise ValueError("Tool surface ready projection mismatch")
        if tuple(value["advertised_capability_ids"]) != receipt.advertised_capability_ids:
            raise ValueError("Tool surface advertised projection mismatch")
        return receipt


class RequiredToolSurfaceUnavailable(RuntimeError):
    """Raised when an enforce-mode Agent lacks a declared live capability."""

    def __init__(self, receipt: ToolSurfaceReceipt) -> None:
        self.receipt = receipt
        failures = tuple(
            f"{item.capability_id}:{item.status.value}"
            for item in receipt.evidence
            if item.required and not item.advertised
        )
        super().__init__(
            "required Tool surface is unavailable"
            + (f" ({', '.join(failures)})" if failures else "")
        )


def _schema_id(schema: Any) -> str | None:
    if not isinstance(schema, Mapping):
        return None
    function = schema.get("function")
    if isinstance(function, Mapping):
        value = function.get("name")
    else:
        value = schema.get("name")
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        return None
    return value


def _canonical_hash(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _receipt_hash_payload(
    *,
    profile_id: str,
    live_schema_ids: Sequence[str],
    duplicate_schema_ids: Sequence[str],
    malformed_schema_count: int,
    evidence: Sequence[ToolCapabilityEvidence],
) -> dict[str, Any]:
    return {
        "schema_version": ToolSurfaceReceipt.SCHEMA_VERSION,
        "profile_id": profile_id,
        "live_schema_ids": list(live_schema_ids),
        "duplicate_schema_ids": list(duplicate_schema_ids),
        "malformed_schema_count": malformed_schema_count,
        "evidence": [item.to_dict() for item in evidence],
    }


def reconcile_tool_surface(
    specs: Iterable[ToolCapabilitySpec],
    *,
    live_tool_schemas: Sequence[Any],
    probes: Iterable[CapabilityProbe] = (),
    profile: ToolSurfaceProfile | None = None,
) -> ToolSurfaceReceipt:
    """Reconcile exact capability bindings against owner-finalized schemas.

    Duplicate provider-visible schema ids are treated as ambiguous rather than
    resolved by list order.  Capabilities requiring an execution canary remain
    unavailable until the caller supplies a successful probe bound by id.
    """

    if isinstance(specs, (str, bytes)):
        raise TypeError("specs must contain ToolCapabilitySpec values")
    if isinstance(live_tool_schemas, (str, bytes)):
        raise TypeError("live_tool_schemas must contain schema objects")
    if isinstance(probes, (str, bytes)):
        raise TypeError("probes must contain CapabilityProbe values")

    capability_specs = tuple(specs)
    if not all(isinstance(item, ToolCapabilitySpec) for item in capability_specs):
        raise TypeError("specs must contain ToolCapabilitySpec values")
    if len({item.capability_id for item in capability_specs}) != len(capability_specs):
        raise ValueError("capability ids must be unique")

    selected_profile = profile or ToolSurfaceProfile()
    if not isinstance(selected_profile, ToolSurfaceProfile):
        raise TypeError("profile must be a ToolSurfaceProfile")

    probe_values = tuple(probes)
    if not all(isinstance(item, CapabilityProbe) for item in probe_values):
        raise TypeError("probes must contain CapabilityProbe values")
    if len({item.capability_id for item in probe_values}) != len(probe_values):
        raise ValueError("probe capability ids must be unique")
    unknown_probe_capabilities = {
        item.capability_id for item in probe_values
    } - {item.capability_id for item in capability_specs}
    if unknown_probe_capabilities:
        raise ValueError("probe references an unknown capability")
    unnecessary_probe_capabilities = {
        item.capability_id for item in probe_values
    } - {item.capability_id for item in capability_specs if item.requires_probe}
    if unnecessary_probe_capabilities:
        raise ValueError("probe supplied for a capability that does not require one")
    probe_by_capability = {item.capability_id: item for item in probe_values}

    extracted = tuple(_schema_id(schema) for schema in live_tool_schemas)
    malformed_schema_count = sum(schema_id is None for schema_id in extracted)
    live_schema_ids = tuple(schema_id for schema_id in extracted if schema_id is not None)
    counts = Counter(live_schema_ids)
    duplicate_schema_ids = tuple(sorted(name for name, count in counts.items() if count > 1))
    live = set(live_schema_ids)
    duplicate = set(duplicate_schema_ids)
    allowed_lifecycles = set(selected_profile.allowed_lifecycles)

    evidence: list[ToolCapabilityEvidence] = []
    for spec in capability_specs:
        matched = tuple(schema_id for schema_id in spec.schema_ids if schema_id in live)
        missing = tuple(schema_id for schema_id in spec.schema_ids if schema_id not in live)
        ambiguous = tuple(schema_id for schema_id in spec.schema_ids if schema_id in duplicate)
        probe = probe_by_capability.get(spec.capability_id)

        if spec.lifecycle not in allowed_lifecycles:
            status = CapabilityStatus.PROFILE_EXCLUDED
            reason_code = "lifecycle_not_allowed"
        elif ambiguous:
            status = CapabilityStatus.SCHEMA_AMBIGUOUS
            reason_code = "duplicate_live_schema_id"
        elif (
            spec.match is CapabilityMatch.ALL and missing
        ) or (
            spec.match is CapabilityMatch.ANY and not matched
        ):
            status = CapabilityStatus.SCHEMA_MISSING
            reason_code = "live_schema_missing"
        elif spec.requires_probe and probe is None:
            status = CapabilityStatus.PROBE_MISSING
            reason_code = "runtime_probe_missing"
        elif spec.requires_probe and not probe.succeeded:
            status = CapabilityStatus.PROBE_FAILED
            reason_code = probe.reason_code
        else:
            status = CapabilityStatus.AVAILABLE
            reason_code = None

        evidence.append(
            ToolCapabilityEvidence(
                capability_id=spec.capability_id,
                status=status,
                lifecycle=spec.lifecycle,
                required=spec.required,
                declared_schema_ids=spec.schema_ids,
                matched_schema_ids=matched,
                missing_schema_ids=missing,
                probe_id=probe.probe_id if probe is not None else None,
                reason_code=reason_code,
            )
        )

    hash_payload = _receipt_hash_payload(
        profile_id=selected_profile.profile_id,
        live_schema_ids=live_schema_ids,
        duplicate_schema_ids=duplicate_schema_ids,
        malformed_schema_count=malformed_schema_count,
        evidence=evidence,
    )
    return ToolSurfaceReceipt(
        profile_id=selected_profile.profile_id,
        live_schema_ids=live_schema_ids,
        duplicate_schema_ids=duplicate_schema_ids,
        malformed_schema_count=malformed_schema_count,
        evidence=tuple(evidence),
        receipt_hash=_canonical_hash(hash_payload),
    )


__all__ = [
    "CapabilityMatch",
    "CapabilityProbe",
    "CapabilityStatus",
    "ToolCapabilityEvidence",
    "ToolCapabilitySpec",
    "ToolLifecycle",
    "ToolSurfaceProfile",
    "ToolSurfaceReceipt",
    "RequiredToolSurfaceUnavailable",
    "reconcile_tool_surface",
]
