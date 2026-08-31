"""Side-effect-free rollout selection for Context Compiler adoption modes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any

from .frozen_json import FrozenMap, freeze_json, thaw_json
from .models import ProviderRequestSnapshot
from .observe import RequestTraceMatch, request_trace_match
from .sidecar import ContextObservationSidecar


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


@dataclass(frozen=True, slots=True)
class CandidateCompilation:
    """Pure candidate output returned before rollout selection."""

    request_snapshot: ProviderRequestSnapshot
    compiler_identity: str
    compiler_version: str
    enforce_ready: bool
    diagnostic_codes: tuple[str, ...] = ()

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


@dataclass(frozen=True, slots=True)
class CandidateCompileInput:
    """Immutable, capability-free input exposed to a candidate compiler."""

    legacy_request: ProviderRequestSnapshot
    observations: tuple[ContextObservationSidecar, ...] = ()

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
    "CandidateCompileInput",
    "CandidateCompilePolicy",
    "CandidateCompilation",
    "CandidateRequestForbidden",
    "CandidateRequestNotEnforceable",
    "CandidateRequestRequired",
    "ContextCompilerMode",
    "ContextRolloutSelection",
    "FRAMEWORK_COMPILER_IDENTITY",
    "RolloutContractError",
    "compile_context_candidate",
    "select_rollout_request",
]
