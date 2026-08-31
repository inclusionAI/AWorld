"""Pure observation of an already-finalized legacy provider request.

This module has no resolver, budget, cache, provider, hook, or runtime behavior.
It freezes facts supplied by the caller and emits a redacted decision trace; it
never constructs or sends another request.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from .adapters import (
    AdapterDiagnostic,
    AdapterDiagnosticSeverity,
    LegacyFinalMessageAdapter,
    LegacyToolSchemaAdapter,
)
from .frozen_json import FrozenMap, canonical_json_hash, freeze_json
from .models import (
    ContextItem,
    ProviderRequestFidelity,
    ProviderRequestSnapshot,
    RequestCaptureStage,
    ResolutionAction,
    ResolutionDecision,
    ResolutionReason,
    TokenAccounting,
    TokenEstimate,
)
from .trace import ContextDecisionTrace


OBSERVE_COMPILER_VERSION = "legacy-request-observe-v1"


class HashEvidenceProvenance(str, Enum):
    UNKNOWN = "unknown"
    CANONICAL_JSON_PAYLOAD = "canonical_json_payload"
    CALLER_PROVIDED_PROVIDER_SERIALIZATION = "caller_provided_provider_serialization"


@dataclass(frozen=True, slots=True)
class HashEvidence:
    value: str | None
    provenance: HashEvidenceProvenance

    def __post_init__(self) -> None:
        object.__setattr__(self, "provenance", HashEvidenceProvenance(self.provenance))
        if self.provenance is HashEvidenceProvenance.UNKNOWN:
            if self.value is not None:
                raise ValueError("unknown hash evidence cannot carry a value")
        elif not isinstance(self.value, str) or not self.value.strip():
            raise ValueError("known hash evidence must carry a non-empty value")

    @classmethod
    def unknown(cls) -> "HashEvidence":
        return cls(value=None, provenance=HashEvidenceProvenance.UNKNOWN)

    @classmethod
    def canonical_payload(cls, value: str) -> "HashEvidence":
        return cls(
            value=value,
            provenance=HashEvidenceProvenance.CANONICAL_JSON_PAYLOAD,
        )

    @classmethod
    def caller_serialized(cls, value: str | None) -> "HashEvidence":
        if value is None:
            return cls.unknown()
        return cls(
            value=value,
            provenance=HashEvidenceProvenance.CALLER_PROVIDED_PROVIDER_SERIALIZATION,
        )

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "provenance": self.provenance.value}


@dataclass(frozen=True, slots=True)
class ObserveHashEvidence:
    request_content_hash: HashEvidence
    stable_prefix_hash: HashEvidence
    serialized_prefix_hash: HashEvidence
    dynamic_context_hash: HashEvidence
    serialized_request_checksum: HashEvidence

    def __post_init__(self) -> None:
        for name in (
            "request_content_hash",
            "stable_prefix_hash",
            "serialized_prefix_hash",
            "dynamic_context_hash",
            "serialized_request_checksum",
        ):
            if not isinstance(getattr(self, name), HashEvidence):
                raise TypeError(f"{name} must be HashEvidence")

    def to_dict(self) -> dict[str, Any]:
        return {
            name: getattr(self, name).to_dict()
            for name in (
                "request_content_hash",
                "stable_prefix_hash",
                "serialized_prefix_hash",
                "dynamic_context_hash",
                "serialized_request_checksum",
            )
        }


@dataclass(frozen=True, slots=True)
class RequestTraceMatch:
    exact: bool
    mismatch_paths: tuple[str, ...]
    mismatch_count: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "mismatch_paths", tuple(self.mismatch_paths))
        if any(not isinstance(path, str) or not path for path in self.mismatch_paths):
            raise ValueError("mismatch paths must be non-empty JSON pointers")
        if len(set(self.mismatch_paths)) != len(self.mismatch_paths):
            raise ValueError("mismatch paths must not contain duplicates")
        if isinstance(self.mismatch_count, bool) or not isinstance(self.mismatch_count, int):
            raise TypeError("mismatch_count must be an integer")
        if self.mismatch_count != len(self.mismatch_paths):
            raise ValueError("mismatch_count must equal the number of mismatch paths")
        if self.exact != (self.mismatch_count == 0):
            raise ValueError("exact must agree with mismatch_count")

    def to_dict(self) -> dict[str, Any]:
        return {
            "exact": self.exact,
            "mismatch_paths": list(self.mismatch_paths),
            "mismatch_count": self.mismatch_count,
        }


@dataclass(frozen=True, slots=True)
class ObserveCompilerResult:
    request_snapshot: ProviderRequestSnapshot
    items: tuple[ContextItem, ...]
    diagnostics: tuple[AdapterDiagnostic, ...]
    decisions: tuple[ResolutionDecision, ...]
    token_accounting: TokenAccounting
    trace: ContextDecisionTrace
    hash_evidence: ObserveHashEvidence

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))
        object.__setattr__(self, "decisions", tuple(self.decisions))
        if not isinstance(self.request_snapshot, ProviderRequestSnapshot):
            raise TypeError("request_snapshot must be ProviderRequestSnapshot")
        if not all(isinstance(item, ContextItem) for item in self.items):
            raise TypeError("items must contain ContextItem values")
        if not all(isinstance(item, AdapterDiagnostic) for item in self.diagnostics):
            raise TypeError("diagnostics must contain AdapterDiagnostic values")
        if not all(isinstance(item, ResolutionDecision) for item in self.decisions):
            raise TypeError("decisions must contain ResolutionDecision values")
        if not isinstance(self.token_accounting, TokenAccounting):
            raise TypeError("token_accounting must be TokenAccounting")
        if not isinstance(self.trace, ContextDecisionTrace):
            raise TypeError("trace must be ContextDecisionTrace")
        if not isinstance(self.hash_evidence, ObserveHashEvidence):
            raise TypeError("hash_evidence must be ObserveHashEvidence")
        item_ids = tuple(item.id for item in self.items)
        decision_ids = tuple(decision.item_id for decision in self.decisions)
        if item_ids != decision_ids:
            raise ValueError("decisions must map one-to-one in item occurrence order")

    def to_redacted_dict(self) -> dict[str, Any]:
        """Serialize observation metadata without request or item payloads."""

        return {
            "request": {
                "request_id": self.request_snapshot.request_id,
                "provider_name": self.request_snapshot.provider_name,
                "capture_stage": self.request_snapshot.capture_stage.value,
                "fidelity": self.request_snapshot.fidelity.value,
                "content_hash": self.request_snapshot.content_hash,
                "serialized_checksum": self.request_snapshot.serialized_checksum,
            },
            "hash_evidence": self.hash_evidence.to_dict(),
            "diagnostics": [
                {
                    "code": item.code,
                    "severity": item.severity.value,
                    "occurrence": item.occurrence,
                    "unknown_fields": list(item.unknown_fields),
                }
                for item in self.diagnostics
            ],
            "trace": self.trace.to_dict(),
        }


def _legacy_decision(item: ContextItem) -> ResolutionDecision:
    unknown = TokenEstimate.unknown()
    return ResolutionDecision(
        item_id=item.id,
        action=ResolutionAction.INCLUDED,
        reason=ResolutionReason.LEGACY_INCLUDED,
        tokens_before=unknown,
        tokens_after=unknown,
        authority=item.authority,
        scope=item.scope,
        trust=item.trust,
        content_hash=item.content_hash or "",
        artifact_ref=None,
    )


def observe_legacy_provider_request(
    *,
    messages: Sequence[Any],
    tools: Sequence[Any] | None,
    params: Mapping[str, Any],
    provider_name: str | None = None,
    request_id: str | None = None,
    capture_stage: RequestCaptureStage = RequestCaptureStage.UNKNOWN,
    fidelity: ProviderRequestFidelity = ProviderRequestFidelity.UNKNOWN,
    source_identity: str = "legacy-provider-request",
    task_id: str | None = None,
    session_id: str | None = None,
    task_epoch: int | None = None,
    trace_id: str | None = None,
    created_at: datetime | None = None,
    serialized_checksum: str | None = None,
    serialized_prefix_hash: str | None = None,
) -> ObserveCompilerResult:
    """Freeze and trace a final legacy request without altering or executing it."""

    if isinstance(messages, (str, bytes, bytearray)) or not isinstance(
        messages, Sequence
    ):
        raise TypeError("messages must be a sequence")
    if tools is not None and (
        isinstance(tools, (str, bytes, bytearray)) or not isinstance(tools, Sequence)
    ):
        raise TypeError("tools must be a sequence or None")
    if not isinstance(params, Mapping):
        raise TypeError("params must be a mapping")
    if not isinstance(source_identity, str) or not source_identity.strip():
        raise ValueError("source_identity must be a non-empty string")

    snapshot = ProviderRequestSnapshot(
        request_id=request_id,
        provider_name=provider_name,
        payload={"messages": messages, "tools": tools, "params": params},
        capture_stage=capture_stage,
        fidelity=fidelity,
        serialized_checksum=serialized_checksum,
    )
    frozen_messages = snapshot.payload["messages"]
    frozen_tools = snapshot.payload["tools"]
    if not isinstance(frozen_messages, tuple):
        raise TypeError("frozen request messages must be a JSON array")
    if frozen_tools is not None and not isinstance(frozen_tools, tuple):
        raise TypeError("frozen request tools must be a JSON array or null")
    message_result = LegacyFinalMessageAdapter().adapt(
        frozen_messages,
        source_identity=f"{source_identity}/messages",
        task_epoch=task_epoch,
    )
    tool_result = LegacyToolSchemaAdapter().adapt(
        frozen_tools if frozen_tools is not None else (),
        source_identity=f"{source_identity}/tools",
        task_epoch=task_epoch,
    )
    items = (*message_result.items, *tool_result.items)
    unknown_hash_fields = ["stable_prefix_hash", "dynamic_context_hash"]
    if serialized_prefix_hash is None:
        unknown_hash_fields.append("serialized_prefix_hash")
    if serialized_checksum is None:
        unknown_hash_fields.append("serialized_request_checksum")
    diagnostics = (
        *message_result.diagnostics,
        *tool_result.diagnostics,
        AdapterDiagnostic(
            code="observe_hash_evidence",
            message=(
                "Observe mode records only canonical request identity and explicit "
                "caller-provided provider serialization evidence."
            ),
            severity=AdapterDiagnosticSeverity.INFO,
            source_identity=source_identity,
            unknown_fields=tuple(unknown_hash_fields),
        ),
    )
    decisions = tuple(_legacy_decision(item) for item in items)
    token_accounting = TokenAccounting.unknown()
    evidence = ObserveHashEvidence(
        request_content_hash=HashEvidence.canonical_payload(snapshot.content_hash or ""),
        stable_prefix_hash=HashEvidence.unknown(),
        serialized_prefix_hash=HashEvidence.caller_serialized(serialized_prefix_hash),
        dynamic_context_hash=HashEvidence.unknown(),
        serialized_request_checksum=HashEvidence.caller_serialized(serialized_checksum),
    )
    trace = ContextDecisionTrace.build(
        trace_id=trace_id,
        task_id=task_id,
        session_id=session_id,
        task_epoch=task_epoch,
        compiler_version=OBSERVE_COMPILER_VERSION,
        items=items,
        decisions=decisions,
        token_accounting=token_accounting,
        stable_prefix_hash=None,
        serialized_prefix_hash=serialized_prefix_hash,
        dynamic_context_hash=None,
        request_snapshot=snapshot,
        created_at=created_at or datetime.now(timezone.utc),
        redact_item_ids=True,
    )
    return ObserveCompilerResult(
        request_snapshot=snapshot,
        items=items,
        diagnostics=diagnostics,
        decisions=decisions,
        token_accounting=token_accounting,
        trace=trace,
        hash_evidence=evidence,
    )


_SAFE_MISMATCH_PATH_KEYS = frozenset(
    {
        "messages",
        "tools",
        "params",
        "role",
        "content",
        "type",
        "function",
        "name",
        "description",
        "parameters",
        "temperature",
        "max_tokens",
        "stop",
    }
)


def _redacted_mapping_key(key: str) -> str:
    if key in _SAFE_MISMATCH_PATH_KEYS:
        return key
    return f"key:{canonical_json_hash({'key': key})}"


def _json_pointer(parent: str, token: str | int) -> str:
    escaped = str(token).replace("~", "~0").replace("/", "~1")
    return f"{parent}/{escaped}"


def _mismatch_paths(expected: Any, actual: Any, path: str = "") -> list[str]:
    if isinstance(expected, Mapping) and isinstance(actual, Mapping):
        mismatches: list[str] = []
        for key in sorted(set(expected) | set(actual)):
            child = _json_pointer(path, _redacted_mapping_key(key))
            if key not in expected or key not in actual:
                mismatches.append(child)
            else:
                mismatches.extend(_mismatch_paths(expected[key], actual[key], child))
        return mismatches
    if isinstance(expected, tuple) and isinstance(actual, tuple):
        mismatches = []
        shared = min(len(expected), len(actual))
        for index in range(shared):
            mismatches.extend(
                _mismatch_paths(expected[index], actual[index], _json_pointer(path, index))
            )
        for index in range(shared, max(len(expected), len(actual))):
            mismatches.append(_json_pointer(path, index))
        return mismatches
    if type(expected) is not type(actual) or expected != actual:
        return [path or "/"]
    return []


def request_trace_match(
    snapshot: ProviderRequestSnapshot,
    provider_bound_request: Mapping[str, Any],
) -> RequestTraceMatch:
    """Compare logical request structures and return paths, never raw values.

    Paths are RFC 6901 JSON pointers.  This comparison does not claim byte-level
    provider serialization equality; that requires caller-provided evidence.
    """

    if not isinstance(snapshot, ProviderRequestSnapshot):
        raise TypeError("snapshot must be ProviderRequestSnapshot")
    frozen_actual = freeze_json(provider_bound_request)
    if not isinstance(frozen_actual, FrozenMap):
        raise TypeError("provider_bound_request must be a JSON object")
    paths = tuple(_mismatch_paths(snapshot.payload, frozen_actual))
    return RequestTraceMatch(
        exact=not paths,
        mismatch_paths=paths,
        mismatch_count=len(paths),
    )


__all__ = [
    "HashEvidence",
    "HashEvidenceProvenance",
    "OBSERVE_COMPILER_VERSION",
    "ObserveCompilerResult",
    "ObserveHashEvidence",
    "RequestTraceMatch",
    "observe_legacy_provider_request",
    "request_trace_match",
]
