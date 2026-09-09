"""Side-effect-free rollout selection for Context Compiler adoption modes."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
import re
from typing import Any

from .frozen_json import FrozenMap, canonical_json_hash, freeze_json, thaw_json
from .models import (
    ProviderRequestFidelity,
    ProviderRequestSnapshot,
    RequestCaptureStage,
)
from .observe import RequestTraceMatch, request_trace_match
from .sidecar import ContextObservationSidecar
from .final import (
    FINAL_COMPILER_IDENTITY,
    FinalCompilePolicy,
    FinalCompileResult,
    ReducerReplacement,
)
from .models import InferenceProfile
from .cache import ProviderVerifiedCacheIdentity, SerializedPrefixEvidence
from .scope import ContextResolutionTarget
from .attribution import (
    ProviderAttributionSubject,
    ProviderRequestAttributionPlan,
    ProviderRequestAttributionReceipt,
)


class ContextCompilerMode(str, Enum):
    OFF = "off"
    OBSERVE = "observe"
    SHADOW = "shadow"
    ENFORCE = "enforce"


class RolloutContractError(ValueError):
    code = "context_rollout_contract_error"


class CandidateRequestRequired(RolloutContractError):
    code = "candidate_request_required"


class CandidateRequestForbidden(RolloutContractError):
    code = "candidate_request_forbidden"


class CandidateRequestNotEnforceable(RolloutContractError):
    code = "candidate_request_not_enforceable"

    def __init__(self, reason_code: str) -> None:
        if not isinstance(reason_code, str) or not re.fullmatch(
            r"[a-z][a-z0-9_]{0,127}", reason_code
        ):
            raise ValueError("reason_code must be a bounded stable code")
        self.reason_code = reason_code
        super().__init__(f"{self.code}: {reason_code}")


FRAMEWORK_COMPILER_IDENTITY = "aworld.context.compiler.framework"
AWORLD_PROVIDER_CANDIDATE_KWARG = "_aworld_provider_candidate_envelope"
AWORLD_PROVIDER_OBSERVED_ATTRIBUTION_KWARG = (
    "_aworld_provider_observed_attribution_envelope"
)


def _stable_identifier(name: str, value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value
    ):
        raise ValueError(
            f"{name} must be a bounded stable identifier"
        )


@dataclass(frozen=True, slots=True)
class CandidateCompilePolicy:
    """Capability-free inputs for the framework-owned pure compiler.

    ``candidate_payload`` is a declarative test/transition seam, not a callable.
    Runtime policies may select reducers and budgets later, but external actions
    remain executor decisions outside candidate compilation.
    """

    compiler_version: str = "v1"
    candidate_payload: FrozenMap | None = None
    enforce_ready: bool = False
    diagnostic_codes: tuple[str, ...] = ()
    final_policy: FinalCompilePolicy | None = None

    def __post_init__(self) -> None:
        _stable_identifier("compiler_version", self.compiler_version)
        if self.candidate_payload is not None:
            frozen_payload = freeze_json(self.candidate_payload)
            if not isinstance(frozen_payload, FrozenMap):
                raise TypeError("candidate_payload must be a JSON object")
            object.__setattr__(self, "candidate_payload", frozen_payload)
        if not isinstance(self.enforce_ready, bool):
            raise TypeError("enforce_ready must be a boolean")
        if isinstance(self.diagnostic_codes, str):
            raise TypeError("diagnostic_codes must be an iterable of strings")
        object.__setattr__(self, "diagnostic_codes", tuple(self.diagnostic_codes))
        if any(
            not isinstance(code, str) or not code.strip()
            for code in self.diagnostic_codes
        ):
            raise ValueError("diagnostic_codes must contain non-empty strings")
        if self.final_policy is not None and type(self.final_policy) is not FinalCompilePolicy:
            raise TypeError("final_policy must be the sealed FinalCompilePolicy type")
        if self.candidate_payload is not None and self.final_policy is not None:
            raise ValueError("candidate_payload and final_policy are mutually exclusive")


@dataclass(frozen=True, slots=True)
class CandidateCompilation:
    """Pure candidate output returned before rollout selection."""

    request_snapshot: ProviderRequestSnapshot
    compiler_identity: str
    compiler_version: str
    enforce_ready: bool
    diagnostic_codes: tuple[str, ...] = ()
    final_result: FinalCompileResult | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.request_snapshot, ProviderRequestSnapshot):
            raise TypeError("request_snapshot must be a ProviderRequestSnapshot")
        for name in ("compiler_identity", "compiler_version"):
            _stable_identifier(name, getattr(self, name))
        if not isinstance(self.enforce_ready, bool):
            raise TypeError("enforce_ready must be a boolean")
        if isinstance(self.diagnostic_codes, str):
            raise TypeError("diagnostic_codes must be an iterable of strings")
        object.__setattr__(self, "diagnostic_codes", tuple(self.diagnostic_codes))
        if any(
            not isinstance(code, str) or not code.strip()
            for code in self.diagnostic_codes
        ):
            raise ValueError("diagnostic_codes must contain non-empty strings")
        if self.final_result is not None and not isinstance(
            self.final_result, FinalCompileResult
        ):
            raise TypeError("final_result must be a FinalCompileResult or None")
        if self.final_result is not None:
            if self.request_snapshot != self.final_result.request_snapshot:
                raise ValueError("final result snapshot must match candidate snapshot")
            if self.enforce_ready != self.final_result.enforce_ready:
                raise ValueError("final result enforce readiness must match candidate")


@dataclass(frozen=True, slots=True)
class CandidateCompileInput:
    """Immutable, capability-free input exposed to a candidate compiler."""

    legacy_request: ProviderRequestSnapshot
    observations: tuple[ContextObservationSidecar, ...] = ()
    inference_profile: InferenceProfile | None = None
    created_at: datetime | None = None
    task_id: str | None = None
    session_id: str | None = None
    trace_id: str | None = None
    task_epoch: int | None = None
    resolution_target: ContextResolutionTarget | None = None
    reducer_replacements: tuple[ReducerReplacement, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.legacy_request, ProviderRequestSnapshot):
            raise TypeError("legacy_request must be a ProviderRequestSnapshot")
        object.__setattr__(self, "observations", tuple(self.observations))
        if any(
            not isinstance(observation, ContextObservationSidecar)
            for observation in self.observations
        ):
            raise TypeError(
                "observations must contain ContextObservationSidecar values"
            )
        if self.inference_profile is not None and not isinstance(
            self.inference_profile, InferenceProfile
        ):
            raise TypeError("inference_profile must be an InferenceProfile or None")
        if self.created_at is not None and (
            self.created_at.tzinfo is None or self.created_at.utcoffset() is None
        ):
            raise ValueError("created_at must be timezone-aware")
        if self.task_epoch is not None and (
            isinstance(self.task_epoch, bool)
            or not isinstance(self.task_epoch, int)
            or self.task_epoch < 0
        ):
            raise ValueError("task_epoch must be a non-negative integer or None")
        if self.resolution_target is not None and not isinstance(
            self.resolution_target, ContextResolutionTarget
        ):
            raise TypeError("resolution_target must be ContextResolutionTarget or None")
        object.__setattr__(
            self, "reducer_replacements", tuple(self.reducer_replacements)
        )
        if any(
            not isinstance(value, ReducerReplacement)
            for value in self.reducer_replacements
        ):
            raise TypeError(
                "reducer_replacements must contain ReducerReplacement values"
            )


@dataclass(frozen=True, slots=True)
class ProviderLoweringCapability:
    """Versioned provider-owned immutable lowering declaration."""

    provider_name: str
    adapter_identity: str
    adapter_version: str
    request_projection: str

    def __post_init__(self) -> None:
        for name in (
            "provider_name",
            "adapter_identity",
            "adapter_version",
            "request_projection",
        ):
            _stable_identifier(name, getattr(self, name))


@dataclass(frozen=True, slots=True)
class ProviderCacheMaterial:
    """Logical material a provider may bind to exact wire-prefix evidence."""

    inference_profile: InferenceProfile
    policy_version: str
    tool_catalog_hash: str
    skill_set_hash: str
    logical_stable_prefix_hash: str
    stable_message_count: int
    provider_cache_namespace: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.inference_profile, InferenceProfile):
            raise TypeError("inference_profile must be an InferenceProfile")
        for name in (
            "policy_version", "tool_catalog_hash", "skill_set_hash",
            "logical_stable_prefix_hash",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if isinstance(self.stable_message_count, bool) or not isinstance(
            self.stable_message_count, int
        ) or self.stable_message_count < 0:
            raise ValueError("stable_message_count must be non-negative")


@dataclass(frozen=True, slots=True)
class ProviderCandidateEnvelope:
    """Candidate handed to one declared provider lowering adapter.

    The envelope is immutable and contains no Context, callback, Tool, client,
    artifact repository, or other action capability.
    """

    candidate_request: ProviderRequestSnapshot
    compiler_identity: str
    compiler_version: str
    expected_lowering: ProviderLoweringCapability
    attribution_plan: ProviderRequestAttributionPlan | None = None
    cache_material: ProviderCacheMaterial | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_request, ProviderRequestSnapshot):
            raise TypeError("candidate_request must be a ProviderRequestSnapshot")
        if not isinstance(self.expected_lowering, ProviderLoweringCapability):
            raise TypeError("expected_lowering must be a ProviderLoweringCapability")
        if self.attribution_plan is not None:
            if not isinstance(self.attribution_plan, ProviderRequestAttributionPlan):
                raise TypeError("attribution_plan must be a ProviderRequestAttributionPlan or None")
            if not self.attribution_plan.shape_explicit:
                raise ValueError("candidate attribution requires explicit collection shape")
            if self.attribution_plan.subject is not ProviderAttributionSubject.CANDIDATE_SELECTED:
                raise ValueError("candidate attribution subject is not bound to the plan")
            if self.attribution_plan.candidate_content_hash != self.candidate_request.content_hash:
                raise ValueError("attribution plan does not match candidate")
            if self.attribution_plan.request_id_hash != canonical_json_hash(
                {"request_id": self.candidate_request.request_id}
            ):
                raise ValueError("attribution plan request id does not match candidate")
        for name in ("compiler_identity", "compiler_version"):
            _stable_identifier(name, getattr(self, name))
        if self.compiler_identity not in {
            FRAMEWORK_COMPILER_IDENTITY,
            FINAL_COMPILER_IDENTITY,
        }:
            raise ValueError("candidate envelope requires a framework-owned compiler")
        if self.candidate_request.provider_name != self.expected_lowering.provider_name:
            raise ValueError("candidate provider does not match lowering capability")
        if self.cache_material is not None:
            if not isinstance(self.cache_material, ProviderCacheMaterial):
                raise TypeError("cache_material must be ProviderCacheMaterial or None")
            if (
                self.cache_material.inference_profile.provider
                != self.candidate_request.provider_name
            ):
                raise ValueError("cache material provider does not match candidate")
        if (
            self.candidate_request.capture_stage
            is not RequestCaptureStage.MODEL_BOUNDARY
            or self.candidate_request.fidelity
            is not ProviderRequestFidelity.MODEL_BOUNDARY
        ):
            raise ValueError("candidate must be captured at the model boundary")


@dataclass(frozen=True, slots=True)
class ProviderObservedAttributionEnvelope:
    """Read-only evidence for attributing a legacy request in observe mode.

    Unlike :class:`ProviderCandidateEnvelope`, this contract never authorizes a
    provider adapter to replace any request value.  It may only verify the
    already-selected legacy request and attach redacted attribution evidence.
    """

    observed_request: ProviderRequestSnapshot
    attribution_plan: ProviderRequestAttributionPlan
    expected_lowering: ProviderLoweringCapability
    subject: ProviderAttributionSubject = ProviderAttributionSubject.LEGACY_OBSERVED

    def __post_init__(self) -> None:
        object.__setattr__(self, "subject", ProviderAttributionSubject(self.subject))
        if self.subject is not ProviderAttributionSubject.LEGACY_OBSERVED:
            raise ValueError("observed attribution envelope requires legacy_observed")
        if self.attribution_plan.subject is not self.subject:
            raise ValueError("observed attribution subject is not bound to the plan")
        if not isinstance(self.observed_request, ProviderRequestSnapshot):
            raise TypeError("observed_request must be a ProviderRequestSnapshot")
        if not isinstance(self.attribution_plan, ProviderRequestAttributionPlan):
            raise TypeError("attribution_plan must be a ProviderRequestAttributionPlan")
        if not isinstance(self.expected_lowering, ProviderLoweringCapability):
            raise TypeError("expected_lowering must be a ProviderLoweringCapability")
        if not self.attribution_plan.shape_explicit:
            raise ValueError("observed attribution requires explicit collection shape")
        if self.attribution_plan.candidate_content_hash != self.observed_request.content_hash:
            raise ValueError("observed attribution plan does not match request")
        if self.attribution_plan.request_id_hash != canonical_json_hash(
            {"request_id": self.observed_request.request_id}
        ):
            raise ValueError("observed attribution request id mismatch")
        if self.observed_request.provider_name != self.expected_lowering.provider_name:
            raise ValueError("observed request provider does not match adapter")
        if (
            self.observed_request.capture_stage is not RequestCaptureStage.MODEL_BOUNDARY
            or self.observed_request.fidelity is not ProviderRequestFidelity.MODEL_BOUNDARY
        ):
            raise ValueError("observed request must be captured at the model boundary")


@dataclass(frozen=True, slots=True)
class ProviderObservedAttributionReceipt:
    """Provider-owned proof for one read-only observed request projection."""

    envelope: ProviderObservedAttributionEnvelope
    provider_request: ProviderRequestSnapshot
    lowering: ProviderLoweringCapability
    attribution: ProviderRequestAttributionReceipt

    def __post_init__(self) -> None:
        if not isinstance(self.envelope, ProviderObservedAttributionEnvelope):
            raise TypeError("envelope must be a ProviderObservedAttributionEnvelope")
        if not isinstance(self.provider_request, ProviderRequestSnapshot):
            raise TypeError("provider_request must be a ProviderRequestSnapshot")
        if not isinstance(self.lowering, ProviderLoweringCapability):
            raise TypeError("lowering must be a ProviderLoweringCapability")
        if not isinstance(self.attribution, ProviderRequestAttributionReceipt):
            raise TypeError("attribution must be a ProviderRequestAttributionReceipt")
        if self.lowering != self.envelope.expected_lowering:
            raise ValueError("observed attribution adapter changed")
        if self.provider_request.request_id != self.envelope.observed_request.request_id:
            raise ValueError("provider request id does not match observed request")
        if self.provider_request.provider_name != self.lowering.provider_name:
            raise ValueError("provider request does not match observed adapter")
        if not self.attribution.binds_plan(self.envelope.attribution_plan):
            raise ValueError("provider attribution is not bound to observed plan")
        if self.attribution.provider_request_content_hash != self.provider_request.content_hash:
            raise ValueError("provider attribution does not match provider request")

    def to_redacted_dict(self) -> dict[str, Any]:
        return {
            "subject": self.envelope.subject.value,
            "subject_content_hash": self.envelope.observed_request.content_hash,
            "plan_fingerprint": self.envelope.attribution_plan.fingerprint,
            "adapter_identity": self.lowering.adapter_identity,
            "adapter_version": self.lowering.adapter_version,
            "request_projection": self.lowering.request_projection,
            "provider_request": {
                "content_hash": self.provider_request.content_hash,
                "capture_stage": self.provider_request.capture_stage.value,
                "fidelity": self.provider_request.fidelity.value,
            },
            "attribution": self.attribution.to_redacted_dict(),
        }


@dataclass(frozen=True, slots=True)
class ProviderLoweringReceipt:
    """Redactable proof that one candidate produced one final provider request."""

    candidate_content_hash: str
    provider_request: ProviderRequestSnapshot
    lowering: ProviderLoweringCapability
    attribution: ProviderRequestAttributionReceipt | None = None
    serialized_prefix_evidence: SerializedPrefixEvidence | None = None
    cache_identity: ProviderVerifiedCacheIdentity | None = None
    logical_stable_prefix_hash: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_content_hash, str) or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", self.candidate_content_hash
        ):
            raise ValueError("candidate_content_hash must be a canonical sha256 hash")
        if not isinstance(self.provider_request, ProviderRequestSnapshot):
            raise TypeError("provider_request must be a ProviderRequestSnapshot")
        if not isinstance(self.lowering, ProviderLoweringCapability):
            raise TypeError("lowering must be a ProviderLoweringCapability")
        if self.attribution is not None:
            if not isinstance(self.attribution, ProviderRequestAttributionReceipt):
                raise TypeError("attribution must be a ProviderRequestAttributionReceipt or None")
            if self.attribution.provider_request_content_hash != self.provider_request.content_hash:
                raise ValueError("attribution receipt does not match provider request")
            if self.attribution.canonical_request_checksum != self.provider_request.content_hash:
                raise ValueError("attribution canonical checksum does not match provider request")
        if self.provider_request.provider_name != self.lowering.provider_name:
            raise ValueError("provider request does not match lowering capability")
        if (
            self.provider_request.capture_stage
            is not RequestCaptureStage.PROVIDER_PREPARED
            or self.provider_request.fidelity
            is not ProviderRequestFidelity.PROVIDER_PREPARED
        ):
            raise ValueError("lowering receipt requires a provider-prepared snapshot")
        if self.serialized_prefix_evidence is not None and not isinstance(
            self.serialized_prefix_evidence, SerializedPrefixEvidence
        ):
            raise TypeError("serialized_prefix_evidence has an invalid type")
        if self.cache_identity is not None and not isinstance(
            self.cache_identity, ProviderVerifiedCacheIdentity
        ):
            raise TypeError("cache_identity has an invalid type")
        if (self.serialized_prefix_evidence is None) != (self.cache_identity is None):
            raise ValueError("serialized evidence and verified cache identity are atomic")
        if self.serialized_prefix_evidence is not None:
            evidence = self.serialized_prefix_evidence
            if (
                self.provider_request.serialized_checksum is None
                or evidence.request_serialized_checksum
                != self.provider_request.serialized_checksum
            ):
                raise ValueError(
                    "serialized request checksum must match provider snapshot"
                )
            if evidence.request_id_hash != canonical_json_hash(
                {"request_id": self.provider_request.request_id}
            ):
                raise ValueError("serialized evidence request id does not match")
        if self.logical_stable_prefix_hash is not None and not re.fullmatch(
            r"sha256:[0-9a-f]{64}", self.logical_stable_prefix_hash
        ):
            raise ValueError("logical_stable_prefix_hash must be canonical or None")
        if self.cache_identity is not None and self.logical_stable_prefix_hash is None:
            raise ValueError("verified cache identity requires the logical prefix hash")

    @classmethod
    def from_envelope(
        cls,
        *,
        envelope: ProviderCandidateEnvelope,
        provider_request: ProviderRequestSnapshot,
        lowering: ProviderLoweringCapability,
        attribution: ProviderRequestAttributionReceipt,
        serialized_prefix_evidence: SerializedPrefixEvidence | None = None,
        cache_identity: ProviderVerifiedCacheIdentity | None = None,
    ) -> "ProviderLoweringReceipt":
        if not isinstance(envelope, ProviderCandidateEnvelope):
            raise TypeError("envelope must be a ProviderCandidateEnvelope")
        if lowering != envelope.expected_lowering:
            raise ValueError("provider lowering capability changed after authorization")
        if provider_request.request_id != envelope.candidate_request.request_id:
            raise ValueError("provider request id does not match candidate")
        if envelope.attribution_plan is None:
            raise ValueError("enforce lowering requires candidate attribution")
        if not attribution.binds_plan(envelope.attribution_plan):
            raise ValueError("provider attribution is not bound to the candidate plan")
        return cls(
            candidate_content_hash=envelope.candidate_request.content_hash,
            provider_request=provider_request,
            lowering=lowering,
            attribution=attribution,
            serialized_prefix_evidence=serialized_prefix_evidence,
            cache_identity=cache_identity,
            logical_stable_prefix_hash=(
                envelope.cache_material.logical_stable_prefix_hash
                if envelope.cache_material is not None
                else None
            ),
        )

    def to_redacted_dict(self) -> dict[str, Any]:
        payload = {
            "adapter_identity": self.lowering.adapter_identity,
            "adapter_version": self.lowering.adapter_version,
            "request_projection": self.lowering.request_projection,
            "candidate_content_hash": self.candidate_content_hash,
            "provider_request": {
                "content_hash": self.provider_request.content_hash,
                "capture_stage": self.provider_request.capture_stage.value,
                "fidelity": self.provider_request.fidelity.value,
            },
        }
        if self.attribution is not None:
            payload["attribution"] = self.attribution.to_redacted_dict()
        if self.cache_identity is not None:
            payload["cache_identity"] = self.cache_identity.to_redacted_dict()
            payload["logical_stable_prefix_hash"] = self.logical_stable_prefix_hash
        return payload


def compile_context_candidate(
    *,
    compiler_input: CandidateCompileInput,
    policy: CandidateCompilePolicy,
) -> CandidateCompilation:
    """Compile one candidate without callbacks, I/O, Tools, or repositories."""
    if not isinstance(compiler_input, CandidateCompileInput):
        raise TypeError("compiler_input must be a CandidateCompileInput")
    if type(policy) is not CandidateCompilePolicy:
        raise TypeError("policy must be the sealed CandidateCompilePolicy type")
    legacy = compiler_input.legacy_request
    if policy.final_policy is not None:
        if compiler_input.inference_profile is None:
            raise ValueError("universal final compilation requires inference_profile")
        from .runtime import compile_model_boundary_context

        replacement_ids = {
            replacement.item_id for replacement in policy.final_policy.replacements
        }
        if any(
            replacement.item_id in replacement_ids
            for replacement in compiler_input.reducer_replacements
        ):
            raise ValueError("runtime reducer replacement conflicts with policy")
        effective_final_policy = replace(
            policy.final_policy,
            replacements=(
                *policy.final_policy.replacements,
                *compiler_input.reducer_replacements,
            ),
        )
        final_result = compile_model_boundary_context(
            legacy_request=legacy,
            observations=compiler_input.observations,
            inference_profile=compiler_input.inference_profile,
            policy=effective_final_policy,
            created_at=compiler_input.created_at or datetime.now(timezone.utc),
            task_id=compiler_input.task_id,
            session_id=compiler_input.session_id,
            trace_id=compiler_input.trace_id,
            task_epoch=compiler_input.task_epoch,
            resolution_target=compiler_input.resolution_target,
        )
        return CandidateCompilation(
            request_snapshot=final_result.request_snapshot,
            compiler_identity=FINAL_COMPILER_IDENTITY,
            compiler_version=final_result.compiler_version,
            enforce_ready=final_result.enforce_ready,
            diagnostic_codes=final_result.blocker_codes,
            final_result=final_result,
        )
    payload: Any = (
        policy.candidate_payload
        if policy.candidate_payload is not None
        else legacy.payload
    )
    return CandidateCompilation(
        request_snapshot=ProviderRequestSnapshot(
            request_id=legacy.request_id,
            provider_name=legacy.provider_name,
            payload=payload,
            capture_stage=legacy.capture_stage,
            fidelity=legacy.fidelity,
        ),
        compiler_identity=FRAMEWORK_COMPILER_IDENTITY,
        compiler_version=policy.compiler_version,
        enforce_ready=policy.enforce_ready,
        diagnostic_codes=policy.diagnostic_codes,
    )


@dataclass(frozen=True, slots=True)
class ContextRolloutSelection:
    """The one request selected for the existing provider invocation."""

    mode: ContextCompilerMode
    provider_request: ProviderRequestSnapshot
    legacy_request: ProviderRequestSnapshot
    candidate_request: ProviderRequestSnapshot | None
    comparison: RequestTraceMatch | None
    candidate_applied: bool
    additional_external_actions: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", ContextCompilerMode(self.mode))
        for name in ("provider_request", "legacy_request"):
            if not isinstance(getattr(self, name), ProviderRequestSnapshot):
                raise TypeError(f"{name} must be a ProviderRequestSnapshot")
        if self.candidate_request is not None and not isinstance(
            self.candidate_request, ProviderRequestSnapshot
        ):
            raise TypeError("candidate_request must be a ProviderRequestSnapshot or None")
        if self.comparison is not None and not isinstance(
            self.comparison, RequestTraceMatch
        ):
            raise TypeError("comparison must be a RequestTraceMatch or None")
        if not isinstance(self.candidate_applied, bool):
            raise TypeError("candidate_applied must be a boolean")
        if self.additional_external_actions != 0:
            raise ValueError("rollout selection cannot authorize additional external actions")
        if self.mode is ContextCompilerMode.ENFORCE:
            if self.candidate_request is None or not self.candidate_applied:
                raise ValueError("enforce must apply a candidate request")
            if self.provider_request is not self.candidate_request:
                raise ValueError("enforce provider request must be the candidate object")
        else:
            if self.candidate_applied:
                raise ValueError("only enforce may apply a candidate request")
            if self.provider_request is not self.legacy_request:
                raise ValueError("non-enforce provider request must be the legacy object")


def select_rollout_request(
    *,
    mode: ContextCompilerMode,
    legacy_request: ProviderRequestSnapshot,
    candidate_request: ProviderRequestSnapshot | None = None,
) -> ContextRolloutSelection:
    """Select one existing snapshot; never invoke a provider or external Tool."""
    resolved_mode = ContextCompilerMode(mode)
    if not isinstance(legacy_request, ProviderRequestSnapshot):
        raise TypeError("legacy_request must be a ProviderRequestSnapshot")
    if candidate_request is not None and not isinstance(
        candidate_request, ProviderRequestSnapshot
    ):
        raise TypeError("candidate_request must be a ProviderRequestSnapshot or None")

    if resolved_mode in {ContextCompilerMode.OFF, ContextCompilerMode.OBSERVE}:
        if candidate_request is not None:
            raise CandidateRequestForbidden(
                f"{CandidateRequestForbidden.code}: mode={resolved_mode.value}"
            )
        return ContextRolloutSelection(
            mode=resolved_mode,
            provider_request=legacy_request,
            legacy_request=legacy_request,
            candidate_request=None,
            comparison=None,
            candidate_applied=False,
        )

    if candidate_request is None:
        raise CandidateRequestRequired(
            f"{CandidateRequestRequired.code}: mode={resolved_mode.value}"
        )
    comparison = request_trace_match(
        candidate_request,
        thaw_json(legacy_request.payload),
    )
    candidate_applied = resolved_mode is ContextCompilerMode.ENFORCE
    return ContextRolloutSelection(
        mode=resolved_mode,
        provider_request=candidate_request if candidate_applied else legacy_request,
        legacy_request=legacy_request,
        candidate_request=candidate_request,
        comparison=comparison,
        candidate_applied=candidate_applied,
    )


__all__ = [
    "AWORLD_PROVIDER_CANDIDATE_KWARG",
    "AWORLD_PROVIDER_OBSERVED_ATTRIBUTION_KWARG",
    "CandidateCompileInput",
    "CandidateCompilePolicy",
    "CandidateCompilation",
    "CandidateRequestForbidden",
    "CandidateRequestNotEnforceable",
    "CandidateRequestRequired",
    "ContextCompilerMode",
    "ContextRolloutSelection",
    "FRAMEWORK_COMPILER_IDENTITY",
    "ProviderCandidateEnvelope",
    "ProviderAttributionSubject",
    "ProviderObservedAttributionEnvelope",
    "ProviderObservedAttributionReceipt",
    "ProviderLoweringCapability",
    "ProviderCacheMaterial",
    "ProviderLoweringReceipt",
    "RolloutContractError",
    "compile_context_candidate",
    "select_rollout_request",
]
