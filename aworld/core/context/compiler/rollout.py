"""Side-effect-free rollout selection for Context Compiler adoption modes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .frozen_json import thaw_json
from .models import ProviderRequestSnapshot
from .observe import RequestTraceMatch, request_trace_match


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
    "CandidateRequestForbidden",
    "CandidateRequestRequired",
    "ContextCompilerMode",
    "ContextRolloutSelection",
    "RolloutContractError",
    "select_rollout_request",
]
