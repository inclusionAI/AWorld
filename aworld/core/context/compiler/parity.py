"""Privacy-safe semantic parity evidence across AWorld entry points.

Compiler receipts describe logical parity. A default-on decision must use the
runtime-only verified wrapper, which independently binds that receipt to the
raw provider request retained in the authoritative ``llm_calls`` record.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from enum import Enum
import re
from typing import Any, Iterable, Iterator, Mapping

from .final import FinalCompileResult
from .frozen_json import FrozenMap, canonical_json_hash, freeze_json, thaw_json
from .models import (
    ProviderRequestFidelity,
    ProviderRequestSnapshot,
    RequestCaptureStage,
)
from .rollout import ProviderLoweringReceipt


_SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}")
_SCOPE_SELECTOR_FIELDS = (
    "workspace_id",
    "directory",
    "path_pattern",
    "session_id",
    "task_id",
    "turn_id",
    "agent_id",
    "child_task_id",
)


class ContextEntryPoint(str, Enum):
    UNKNOWN = "unknown"
    DIRECT = "direct"
    AGENT = "agent"
    AMNI = "amni"
    CLI = "cli"
    ACP = "acp"
    RESUME = "resume"
    CHILD = "child"


class ContextCallShape(str, Enum):
    UNKNOWN = "unknown"
    SYNC = "sync"
    ASYNC = "async"
    SYNC_STREAM = "sync_stream"
    ASYNC_STREAM = "async_stream"


class ContextEntrypointLabelSource(str, Enum):
    LEGACY_UNATTESTED = "legacy_unattested"
    DIRECT_MODEL_BOUNDARY = "direct_model_boundary"
    AGENT_BOUNDARY = "agent_boundary"
    AMNI_BOUNDARY = "amni_boundary"
    CLI_EXECUTOR = "cli_executor"
    ACP_EXECUTOR = "acp_executor"
    SESSION_RESTORE = "session_restore"
    CHILD_AGENT_BOUNDARY = "child_agent_boundary"


_VALID_LABEL_SOURCES = {
    ContextEntryPoint.DIRECT: ContextEntrypointLabelSource.DIRECT_MODEL_BOUNDARY,
    ContextEntryPoint.AGENT: ContextEntrypointLabelSource.AGENT_BOUNDARY,
    ContextEntryPoint.AMNI: ContextEntrypointLabelSource.AMNI_BOUNDARY,
    ContextEntryPoint.CLI: ContextEntrypointLabelSource.CLI_EXECUTOR,
    ContextEntryPoint.ACP: ContextEntrypointLabelSource.ACP_EXECUTOR,
    ContextEntryPoint.RESUME: ContextEntrypointLabelSource.SESSION_RESTORE,
    ContextEntryPoint.CHILD: ContextEntrypointLabelSource.CHILD_AGENT_BOUNDARY,
}


@dataclass(frozen=True, slots=True, init=False)
class _ContextEntrypointClaim:
    entry_point: ContextEntryPoint
    source: ContextEntrypointLabelSource

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError("entry-point claims are issued by framework boundaries")


_CURRENT_ENTRYPOINT: ContextVar[_ContextEntrypointClaim | None] = ContextVar(
    "aworld_context_entrypoint_claim", default=None
)


def _issue_context_entrypoint_claim(
    entry_point: ContextEntryPoint | str,
) -> _ContextEntrypointClaim:
    """Framework-internal boundary claim; intentionally not publicly exported."""
    resolved = ContextEntryPoint(entry_point)
    source = _VALID_LABEL_SOURCES.get(resolved)
    if source is None:
        raise ValueError("unknown entry point cannot be attested")
    claim = object.__new__(_ContextEntrypointClaim)
    object.__setattr__(claim, "entry_point", resolved)
    object.__setattr__(claim, "source", source)
    return claim


@contextmanager
def _bind_context_entrypoint_claim(
    claim: _ContextEntrypointClaim,
) -> Iterator[None]:
    if not isinstance(claim, _ContextEntrypointClaim):
        raise TypeError("claim must be framework-issued")
    token = _CURRENT_ENTRYPOINT.set(claim)
    try:
        yield
    finally:
        _CURRENT_ENTRYPOINT.reset(token)


def _current_context_entrypoint_claim() -> _ContextEntrypointClaim:
    claim = _CURRENT_ENTRYPOINT.get()
    return claim or _issue_context_entrypoint_claim(ContextEntryPoint.DIRECT)


def _semantic_projection(result: FinalCompileResult) -> FrozenMap:
    if not isinstance(result, FinalCompileResult):
        raise TypeError("result must be FinalCompileResult")
    decisions_by_id = {decision.item_id: decision for decision in result.decisions}
    budget = result.input_budget

    def scope_shape(scope: Any) -> dict[str, bool]:
        # Runtime ids/paths vary by entry point and are not model semantics.
        # Selector presence and the resulting decision preserve the scope
        # contract without retaining or hashing accidental identity values.
        return {
            name: getattr(scope, name) is not None
            for name in _SCOPE_SELECTOR_FIELDS
        }

    projection = {
        "compiler_identity": result.compiler_identity,
        "compiler_version": result.compiler_version,
        "policy_version": result.policy_version,
        # This immutable payload excludes request/trace/task ids but retains
        # provider parameters that can alter request semantics.
        "request_content_hash": result.request_snapshot.content_hash,
        "provider_name": result.request_snapshot.provider_name,
        "inference_profile": result.inference_profile.to_dict(),
        "input_budget": {
            "context_limit": budget.context_limit,
            "reserved_output_tokens": budget.reserved_output_tokens,
            "provider_protocol_reserve": budget.provider_protocol_reserve,
            "safety_margin_tokens": budget.safety_margin_tokens,
            "max_item_tokens": budget.max_item_tokens,
        },
        "token_accounting": result.token_accounting.to_dict(),
        "stable_prefix_hash": result.stable_partition.stable_prefix_hash,
        "dynamic_context_hash": result.stable_partition.dynamic_context_hash,
        "tool_catalog_hash": result.tool_catalog_hash,
        "skill_set_hash": result.skill_set_hash,
        "enforce_ready": result.enforce_ready,
        "blocker_codes": list(result.blocker_codes),
        "decisions": [
            {
                "ordinal": ordinal,
                "action": decision.action.value,
                "reason": decision.reason.value,
                "tokens_before": decision.tokens_before.to_dict(),
                "tokens_after": decision.tokens_after.to_dict(),
                "authority": decision.authority.value,
                "scope_kinds": [kind.value for kind in decision.scope.kinds],
                "scope_selectors": scope_shape(decision.scope),
                "trust": decision.trust.value,
                "content_hash": decision.content_hash,
                "artifact_present": decision.artifact_ref is not None,
            }
            for ordinal, decision in enumerate(result.decisions)
        ],
        "selected": [
            {
                "ordinal": ordinal,
                "kind": item.kind.value,
                "source_kind": item.source.kind.value,
                "authority": item.authority.value,
                "scope_kinds": [kind.value for kind in item.scope.kinds],
                "scope_selectors": scope_shape(item.scope),
                "trust": item.trust.value,
                "stability": item.stability.value,
                "lifetime": item.lifetime.value,
                "required": item.required,
                "priority": item.priority,
                "token_limit": item.token_limit,
                "reducer_hash": (
                    canonical_json_hash({"reducer": item.reducer})
                    if item.reducer is not None
                    else None
                ),
                "version_hash": (
                    canonical_json_hash({"version": item.version})
                    if item.version is not None
                    else None
                ),
                "content_hash": item.content_hash,
                "decision_content_hash": (
                    decisions_by_id[item.id].content_hash
                    if item.id in decisions_by_id
                    else None
                ),
            }
            for ordinal, item in enumerate(result.selected_items)
        ],
    }
    frozen = freeze_json(projection)
    if not isinstance(frozen, FrozenMap):
        raise TypeError("semantic projection must be an object")
    return frozen


@dataclass(frozen=True, slots=True)
class ContextEntrypointParityReceipt:
    entry_point: ContextEntryPoint
    semantic_projection: FrozenMap
    call_shape: ContextCallShape = ContextCallShape.UNKNOWN
    label_source: ContextEntrypointLabelSource = (
        ContextEntrypointLabelSource.LEGACY_UNATTESTED
    )
    request_id_hash: str | None = None
    provider_binding: FrozenMap | None = None
    semantic_fingerprint: str | None = None

    SCHEMA_VERSION = "aworld.context.entrypoint-parity.v2"
    LEGACY_SCHEMA_VERSION = "aworld.context.entrypoint-parity.v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "entry_point", ContextEntryPoint(self.entry_point))
        object.__setattr__(self, "call_shape", ContextCallShape(self.call_shape))
        object.__setattr__(
            self, "label_source", ContextEntrypointLabelSource(self.label_source)
        )
        projection = freeze_json(self.semantic_projection)
        if not isinstance(projection, FrozenMap):
            raise TypeError("semantic_projection must be an object")
        object.__setattr__(self, "semantic_projection", projection)
        expected = canonical_json_hash(projection)
        if self.semantic_fingerprint is not None and self.semantic_fingerprint != expected:
            raise ValueError("entrypoint parity fingerprint mismatch")
        object.__setattr__(self, "semantic_fingerprint", expected)
        if self.request_id_hash is not None and not _SHA256_RE.fullmatch(
            self.request_id_hash
        ):
            raise ValueError("request_id_hash must be canonical or None")
        if self.provider_binding is not None:
            binding = freeze_json(self.provider_binding)
            if not isinstance(binding, FrozenMap):
                raise TypeError("provider_binding must be an object")
            required = {
                "candidate_content_hash",
                "provider_request_content_hash",
                "provider_name",
                "capture_stage",
                "fidelity",
                "adapter_identity",
                "adapter_version",
                "request_projection",
                "plan_fingerprint",
                "canonical_request_checksum",
                "serialization",
                "serialized_checksum",
            }
            if set(binding) != required:
                raise ValueError("provider binding has an unsupported shape")
            for name in (
                "candidate_content_hash",
                "provider_request_content_hash",
                "plan_fingerprint",
                "canonical_request_checksum",
            ):
                if not _SHA256_RE.fullmatch(str(binding[name])):
                    raise ValueError(f"{name} must be canonical")
            if binding["serialized_checksum"] is not None and not _SHA256_RE.fullmatch(
                str(binding["serialized_checksum"])
            ):
                raise ValueError("serialized_checksum must be canonical or null")
            if self.request_id_hash is None:
                raise ValueError("provider binding requires request identity")
            object.__setattr__(self, "provider_binding", binding)

    @property
    def provider_bound(self) -> bool:
        return self.provider_binding is not None

    @property
    def label_attested(self) -> bool:
        return _VALID_LABEL_SOURCES.get(self.entry_point) is self.label_source

    @classmethod
    def from_final_result(
        cls,
        *,
        entry_point: ContextEntryPoint | str,
        result: FinalCompileResult,
        call_shape: ContextCallShape | str = ContextCallShape.UNKNOWN,
        label_source: ContextEntrypointLabelSource | str = (
            ContextEntrypointLabelSource.LEGACY_UNATTESTED
        ),
    ) -> "ContextEntrypointParityReceipt":
        request_id = result.request_snapshot.request_id
        return cls(
            entry_point=ContextEntryPoint(entry_point),
            call_shape=ContextCallShape(call_shape),
            label_source=ContextEntrypointLabelSource(label_source),
            request_id_hash=(
                canonical_json_hash({"request_id": request_id})
                if request_id is not None
                else None
            ),
            semantic_projection=_semantic_projection(result),
        )

    def bind_provider_lowering(
        self, receipt: ProviderLoweringReceipt
    ) -> "ContextEntrypointParityReceipt":
        if not isinstance(receipt, ProviderLoweringReceipt):
            raise TypeError("receipt must be a ProviderLoweringReceipt")
        attribution = receipt.attribution
        if attribution is None or not attribution.binding_explicit:
            raise ValueError("provider-bound parity requires attribution")
        if receipt.candidate_content_hash != self.semantic_projection[
            "request_content_hash"
        ]:
            raise ValueError("provider lowering is not bound to parity candidate")
        if attribution.plan_request_id_hash != self.request_id_hash:
            raise ValueError("provider attribution request id does not match parity")
        provider_request = receipt.provider_request
        return replace(
            self,
            provider_binding=freeze_json({
                "candidate_content_hash": receipt.candidate_content_hash,
                "provider_request_content_hash": provider_request.content_hash,
                "provider_name": provider_request.provider_name,
                "capture_stage": provider_request.capture_stage.value,
                "fidelity": provider_request.fidelity.value,
                "adapter_identity": receipt.lowering.adapter_identity,
                "adapter_version": receipt.lowering.adapter_version,
                "request_projection": receipt.lowering.request_projection,
                "plan_fingerprint": attribution.plan_fingerprint,
                "canonical_request_checksum": attribution.canonical_request_checksum,
                "serialization": attribution.serialization.value,
                "serialized_checksum": provider_request.serialized_checksum,
            }),
        )

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ContextEntrypointParityReceipt":
        if not isinstance(value, dict):
            raise ValueError("unsupported entrypoint parity receipt")
        schema = value.get("schema_version")
        if schema not in {cls.SCHEMA_VERSION, cls.LEGACY_SCHEMA_VERSION}:
            raise ValueError("unsupported entrypoint parity receipt")
        return cls(
            entry_point=value["entry_point"],
            call_shape=value.get("call_shape", ContextCallShape.UNKNOWN.value),
            label_source=value.get(
                "label_source", ContextEntrypointLabelSource.LEGACY_UNATTESTED.value
            ),
            request_id_hash=value.get("request_id_hash"),
            provider_binding=value.get("provider_binding"),
            semantic_projection=value["semantic_projection"],
            semantic_fingerprint=value.get("semantic_fingerprint"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "entry_point": self.entry_point.value,
            "call_shape": self.call_shape.value,
            "label_source": self.label_source.value,
            "request_id_hash": self.request_id_hash,
            "semantic_projection": thaw_json(self.semantic_projection),
            "semantic_fingerprint": self.semantic_fingerprint,
            "provider_binding": (
                thaw_json(self.provider_binding)
                if self.provider_binding is not None
                else None
            ),
        }


@dataclass(frozen=True, slots=True, init=False)
class VerifiedContextEntrypointParityReceipt:
    """Runtime-only proof reconstructed from an authoritative raw call record."""

    receipt: ContextEntrypointParityReceipt
    provider_name: str
    evidence_fingerprint: str

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError("use from_llm_call_record")

    @classmethod
    def from_llm_call_record(
        cls, record: Mapping[str, Any]
    ) -> "VerifiedContextEntrypointParityReceipt":
        if not isinstance(record, Mapping):
            raise TypeError("record must be a mapping")
        rollout = record.get("context_rollout")
        if not isinstance(rollout, Mapping) or rollout.get("mode") != "enforce":
            raise ValueError("verified parity requires an enforce call")
        parity_payload = rollout.get("entrypoint_parity")
        if not isinstance(parity_payload, dict):
            raise ValueError("entrypoint parity receipt is unavailable")
        receipt = ContextEntrypointParityReceipt.from_dict(parity_payload)
        if (
            not receipt.provider_bound
            or not receipt.label_attested
            or receipt.call_shape is ContextCallShape.UNKNOWN
        ):
            raise ValueError("entrypoint parity is not provider-bound and attested")
        if record.get("provider_invoked") is not True or record.get(
            "provider_attempt_status"
        ) != "attempted":
            raise ValueError("provider attempt is incomplete")
        if record.get("status") != "success" or record.get(
            "request_trace_match"
        ) is not True:
            raise ValueError("successful request trace evidence is required")
        request_id = record.get("request_id")
        if not isinstance(request_id, str) or canonical_json_hash(
            {"request_id": request_id}
        ) != receipt.request_id_hash:
            raise ValueError("raw request id does not match parity receipt")
        candidate_payload = record.get("request")
        if not isinstance(candidate_payload, Mapping) or canonical_json_hash(
            candidate_payload
        ) != receipt.semantic_projection["request_content_hash"]:
            raise ValueError("raw candidate request does not match parity receipt")
        provider_payload = record.get("provider_request")
        if not isinstance(provider_payload, dict):
            raise ValueError("raw provider request is unavailable")
        provider_snapshot = ProviderRequestSnapshot.from_dict(provider_payload)
        binding = receipt.provider_binding
        assert binding is not None
        if (
            provider_snapshot.request_id != request_id
            or provider_snapshot.capture_stage is not RequestCaptureStage.PROVIDER_PREPARED
            or provider_snapshot.fidelity is not ProviderRequestFidelity.PROVIDER_PREPARED
            or binding["capture_stage"] != provider_snapshot.capture_stage.value
            or binding["fidelity"] != provider_snapshot.fidelity.value
            or binding["candidate_content_hash"]
            != receipt.semantic_projection["request_content_hash"]
            or provider_snapshot.content_hash != binding["provider_request_content_hash"]
            or binding["canonical_request_checksum"]
            != provider_snapshot.content_hash
            or provider_snapshot.provider_name != binding["provider_name"]
            or provider_snapshot.serialized_checksum != binding["serialized_checksum"]
        ):
            raise ValueError("raw provider request does not match parity binding")
        lowering = rollout.get("provider_lowering")
        attribution = lowering.get("attribution") if isinstance(lowering, Mapping) else None
        if not isinstance(lowering, Mapping) or not isinstance(attribution, Mapping):
            raise ValueError("provider lowering evidence is unavailable")
        expected_fields = {
            "adapter_identity": lowering.get("adapter_identity"),
            "adapter_version": lowering.get("adapter_version"),
            "request_projection": lowering.get("request_projection"),
            "plan_fingerprint": attribution.get("plan_fingerprint"),
            "canonical_request_checksum": attribution.get(
                "canonical_request_checksum"
            ),
            "serialization": attribution.get("serialization"),
        }
        if any(binding[name] != value for name, value in expected_fields.items()):
            raise ValueError("raw provider lowering does not match parity binding")
        if (
            attribution.get("provider_request_content_hash")
            != provider_snapshot.content_hash
            or attribution.get("candidate_content_hash")
            != receipt.semantic_projection["request_content_hash"]
            or attribution.get("plan_request_id_hash") != receipt.request_id_hash
            or attribution.get("subject") != "candidate_selected"
            or (
                attribution.get("serialization")
                == "http_serialized_canonical_json"
                and provider_snapshot.serialized_checksum
                != provider_snapshot.content_hash
            )
            or (
                attribution.get("serialization")
                == "provider_prepared_canonical_json"
                and provider_snapshot.serialized_checksum is not None
            )
        ):
            raise ValueError("provider attribution does not match raw request")
        value = object.__new__(cls)
        object.__setattr__(value, "receipt", receipt)
        object.__setattr__(value, "provider_name", str(provider_snapshot.provider_name))
        object.__setattr__(value, "evidence_fingerprint", canonical_json_hash({
            "request_id_hash": receipt.request_id_hash,
            "semantic_fingerprint": receipt.semantic_fingerprint,
            "provider_binding": binding,
        }))
        return value


def assess_entrypoint_parity(
    receipts: Iterable[
        ContextEntrypointParityReceipt | VerifiedContextEntrypointParityReceipt
    ],
    *,
    required_entry_points: Iterable[ContextEntryPoint | str],
    require_provider_bound: bool = False,
) -> dict[str, Any]:
    values = tuple(receipts)
    required = tuple(ContextEntryPoint(value) for value in required_entry_points)
    if len(set(required)) != len(required):
        raise ValueError("required entry points must be unique")
    if not isinstance(require_provider_bound, bool):
        raise TypeError("require_provider_bound must be a boolean")
    if require_provider_bound:
        if not all(
            isinstance(value, VerifiedContextEntrypointParityReceipt)
            for value in values
        ):
            return {
                "status": "unavailable",
                "reason_code": "entrypoint_provider_evidence_required",
                "missing": [entry.value for entry in required],
                "duplicates": [],
            }
        normalized = tuple(value.receipt for value in values)
    else:
        if not all(isinstance(value, ContextEntrypointParityReceipt) for value in values):
            raise TypeError("receipts must contain ContextEntrypointParityReceipt values")
        normalized = values
    grouped: dict[ContextEntryPoint, list[ContextEntrypointParityReceipt]] = {}
    for value in normalized:
        grouped.setdefault(value.entry_point, []).append(value)
    missing = [entry.value for entry in required if entry not in grouped]
    duplicates = [entry.value for entry in required if len(grouped.get(entry, ())) > 1]
    if missing or duplicates:
        return {
            "status": "unavailable",
            "reason_code": "entrypoint_evidence_incomplete",
            "missing": missing,
            "duplicates": duplicates,
        }
    def parity_fingerprint(entry: ContextEntryPoint) -> str:
        receipt = grouped[entry][0]
        if not require_provider_bound:
            assert receipt.semantic_fingerprint is not None
            return receipt.semantic_fingerprint
        # The compiler projection deliberately excludes transport/request
        # accidents.  Provider-bound parity must additionally preserve every
        # model-visible provider parameter (reasoning effort, response format,
        # etc.) from independently revalidated raw evidence.
        binding = receipt.provider_binding
        assert binding is not None
        return canonical_json_hash({
            "compiler_semantics": receipt.semantic_fingerprint,
            "provider_request_content_hash": binding[
                "provider_request_content_hash"
            ],
            "call_shape": receipt.call_shape.value,
        })

    fingerprints_by_entry = {
        entry: parity_fingerprint(entry) for entry in required
    }
    fingerprints = set(fingerprints_by_entry.values())
    return {
        "status": "available" if len(fingerprints) == 1 else "mismatch",
        "reason_code": None if len(fingerprints) == 1 else "entrypoint_semantics_mismatch",
        "entry_points": [entry.value for entry in required],
        "semantic_fingerprint": next(iter(fingerprints)) if len(fingerprints) == 1 else None,
        "fingerprints": {
            entry.value: fingerprints_by_entry[entry] for entry in required
        },
        "provider_bound": require_provider_bound,
    }


__all__ = [
    "ContextCallShape",
    "ContextEntryPoint",
    "ContextEntrypointLabelSource",
    "ContextEntrypointParityReceipt",
    "VerifiedContextEntrypointParityReceipt",
    "assess_entrypoint_parity",
]
