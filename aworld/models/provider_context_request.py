"""Shared provider-owned lowering transaction for non-OpenAI chat adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from aworld.core.context.compiler import (
    AWORLD_PROVIDER_CANDIDATE_KWARG,
    AWORLD_PROVIDER_OBSERVED_ATTRIBUTION_KWARG,
    AttributionSerialization,
    AttributionCollectionShape,
    CandidateRequestNotEnforceable,
    ProviderAttributionMismatch,
    ProviderCandidateEnvelope,
    ProviderLoweringReceipt,
    ProviderObservedAttributionEnvelope,
    ProviderObservedAttributionReceipt,
    ProviderRequestFidelity,
    ProviderRequestSnapshot,
    ProviderToolsLowering,
    RequestCaptureStage,
    build_provider_attribution_receipt,
)
from aworld.core.context.compiler.frozen_json import canonical_json_bytes


@dataclass(frozen=True, slots=True)
class ProviderWireProjection:
    """One reviewed deterministic projection from standard request to wire args."""

    payload: dict[str, Any]
    message_occurrences: tuple[Any, ...]
    tool_occurrences: tuple[Any, ...] | None
    tools_lowering: ProviderToolsLowering = ProviderToolsLowering.PRESERVE
    provider_tools_shape_override: AttributionCollectionShape | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class PreparedProviderContextRequest:
    payload: dict[str, Any]
    context: Any
    request_id: str
    attempt_tracking_ready: bool
    metadata: dict[str, Any] | None = None


def _standard_request(
    *,
    messages: list[dict[str, Any]],
    tools: Any,
    temperature: float,
    max_tokens: int | None,
    stop: list[str] | None,
) -> dict[str, Any]:
    return {
        "messages": messages,
        "tools": tools,
        "params": {
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stop": stop,
        },
    }


def _validate_candidate_payload(payload: dict[str, Any]) -> None:
    if set(payload) != {"messages", "tools", "params"}:
        raise ValueError("unsupported model-boundary projection")
    if not isinstance(payload["messages"], list):
        raise TypeError("candidate messages must be a list")
    if payload["tools"] is not None and not isinstance(payload["tools"], list):
        raise TypeError("candidate tools must be a list or null")
    params = payload["params"]
    if not isinstance(params, dict) or set(params) != {
        "temperature",
        "max_tokens",
        "stop",
    }:
        raise ValueError("unsupported candidate parameter projection")


def prepare_provider_context_request(
    *,
    provider: Any,
    messages: list[dict[str, Any]],
    temperature: float,
    max_tokens: int | None,
    stop: list[str] | None,
    kwargs: dict[str, Any],
    stream: bool,
    lower: Callable[[dict[str, Any], dict[str, Any], bool], ProviderWireProjection],
) -> PreparedProviderContextRequest:
    """Select, lower, snapshot and commit one provider request before I/O."""
    request_kwargs = dict(kwargs)
    envelope = request_kwargs.pop(AWORLD_PROVIDER_CANDIDATE_KWARG, None)
    observed_envelope = request_kwargs.pop(
        AWORLD_PROVIDER_OBSERVED_ATTRIBUTION_KWARG, None
    )
    if envelope is not None and observed_envelope is not None:
        raise CandidateRequestNotEnforceable("provider_lowering_contract_invalid")

    current = _standard_request(
        messages=messages,
        tools=request_kwargs.get("tools"),
        temperature=temperature,
        max_tokens=max_tokens,
        stop=stop,
    )
    selected = current
    capability = provider.context_candidate_lowering_capability()
    observed_reason = None
    if envelope is not None:
        if not isinstance(envelope, ProviderCandidateEnvelope) or (
            capability != envelope.expected_lowering
        ):
            raise CandidateRequestNotEnforceable("provider_lowering_contract_invalid")
        if request_kwargs.get("prompt_assembly_plan") is not None:
            raise CandidateRequestNotEnforceable("provider_transform_after_candidate")
        try:
            selected = envelope.candidate_request.thaw()
            _validate_candidate_payload(selected)
        except Exception:
            raise CandidateRequestNotEnforceable(
                "provider_candidate_schema_unsupported"
            ) from None
    elif observed_envelope is not None:
        try:
            if (
                not isinstance(observed_envelope, ProviderObservedAttributionEnvelope)
                or capability != observed_envelope.expected_lowering
            ):
                raise ValueError("observed attribution adapter mismatch")
            if observed_envelope.observed_request.thaw() != current:
                raise ValueError("observed model-boundary request mismatch")
        except Exception:
            observed_reason = "observed_model_boundary_mismatch"

    try:
        projection = lower(selected, request_kwargs, stream)
        if not isinstance(projection, ProviderWireProjection):
            raise TypeError("provider lowerer returned an invalid projection")
        canonical_json_bytes(projection.payload)
        snapshot = ProviderRequestSnapshot(
            request_id=request_kwargs.get("llm_request_id"),
            provider_name=capability.provider_name,
            payload=projection.payload,
            capture_stage=RequestCaptureStage.PROVIDER_PREPARED,
            fidelity=ProviderRequestFidelity.PROVIDER_PREPARED,
        )
    except CandidateRequestNotEnforceable:
        raise
    except Exception:
        if envelope is not None:
            raise CandidateRequestNotEnforceable(
                "provider_request_lowering_failed"
            ) from None
        raise

    receipt = None
    if envelope is not None:
        try:
            attribution = build_provider_attribution_receipt(
                plan=envelope.attribution_plan,
                provider_request=projection.payload,
                serialization=AttributionSerialization.PROVIDER_PREPARED_CANONICAL_JSON,
                tools_lowering=projection.tools_lowering,
                source_request=selected,
                provider_message_occurrences=projection.message_occurrences,
                provider_tool_occurrences=projection.tool_occurrences,
                provider_tools_shape_override=(
                    projection.provider_tools_shape_override
                ),
            )
            receipt = ProviderLoweringReceipt.from_envelope(
                envelope=envelope,
                provider_request=snapshot,
                lowering=capability,
                attribution=attribution,
            )
        except ProviderAttributionMismatch:
            raise CandidateRequestNotEnforceable(
                "provider_attribution_mismatch"
            ) from None
        except Exception:
            raise CandidateRequestNotEnforceable(
                "provider_request_not_snapshotable"
            ) from None

    observed_receipt = None
    if observed_envelope is not None and observed_reason is None:
        try:
            attribution = build_provider_attribution_receipt(
                plan=observed_envelope.attribution_plan,
                provider_request=projection.payload,
                serialization=AttributionSerialization.PROVIDER_PREPARED_CANONICAL_JSON,
                tools_lowering=projection.tools_lowering,
                source_request=selected,
                provider_message_occurrences=projection.message_occurrences,
                provider_tool_occurrences=projection.tool_occurrences,
                provider_tools_shape_override=(
                    projection.provider_tools_shape_override
                ),
            )
            observed_receipt = ProviderObservedAttributionReceipt(
                envelope=observed_envelope,
                provider_request=snapshot,
                lowering=capability,
                attribution=attribution,
            )
        except Exception:
            observed_reason = "provider_attribution_mismatch"

    context = request_kwargs.get("context")
    tracking_ready = False
    try:
        if observed_envelope is not None:
            provider.commit_provider_observed_attribution(
                context=context,
                request_id=snapshot.request_id,
                snapshot=snapshot,
                envelope=observed_envelope,
                receipt=observed_receipt,
                reason_code=(observed_reason if observed_receipt is None else None),
            )
        else:
            provider.commit_provider_prepared_attempt(
                context=context,
                request_id=snapshot.request_id,
                snapshot=snapshot,
                envelope=envelope,
                receipt=receipt,
            )
        tracking_ready = True
    except Exception:
        if envelope is not None:
            raise
        if observed_envelope is not None:
            provider.commit_provider_observation_unavailable(
                context=context,
                request_id=snapshot.request_id,
                envelope=observed_envelope,
                reason_code="provider_attribution_storage_failed",
                snapshot=snapshot,
            )

    return PreparedProviderContextRequest(
        payload=projection.payload,
        context=context,
        request_id=snapshot.request_id,
        attempt_tracking_ready=tracking_ready,
        metadata=projection.metadata,
    )


def mark_prepared_provider_attempt(
    provider: Any, prepared: PreparedProviderContextRequest
) -> None:
    """Commit attempted immediately before the SDK/HTTP boundary."""
    if prepared.attempt_tracking_ready:
        provider.mark_provider_attempted(
            context=prepared.context,
            request_id=prepared.request_id,
        )
    else:
        provider.mark_provider_attempted_fail_open(
            context=prepared.context,
            request_id=prepared.request_id,
        )


__all__ = [
    "PreparedProviderContextRequest",
    "ProviderWireProjection",
    "mark_prepared_provider_attempt",
    "prepare_provider_context_request",
]
