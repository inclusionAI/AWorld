"""AWorld-owned, phase-aware reasoning request selection.

This module is intentionally independent from Agent and Runtime orchestration.
It selects a caller- or policy-declared reasoning profile and projects it onto
one reviewed provider transport without mutating shared model configuration.
Runtime may transport these request fields, but it does not need to infer an
execution phase.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, Mapping


AWORLD_REASONING_SELECTION_KWARG = "_aworld_reasoning_selection"
OPENAI_REASONING_TRANSPORT = "openai/v1"
OPENAI_CHAT_TEMPLATE_REASONING_TRANSPORT = "openai+chat_template/v1"
OPENAI_REASONING_TRANSPORTS = frozenset(
    {OPENAI_REASONING_TRANSPORT, OPENAI_CHAT_TEMPLATE_REASONING_TRANSPORT}
)
REASONING_TRANSPORTS = frozenset({"auto", "openai", "openai_chat_template"})
REASONING_WIRE_TRANSPORTS = frozenset({"openai", "openai_chat_template"})
REASONING_EFFORTS = frozenset(
    {
        "none",
        "off",
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    }
)
OPENAI_STANDARD_REASONING_EFFORTS = frozenset(
    {"none", "minimal", "low", "medium", "high", "xhigh"}
)


class ReasoningPolicyError(ValueError):
    """Base class for invalid reasoning-policy declarations."""


class ReasoningPolicyConflictError(ReasoningPolicyError):
    """Raised when explicit reasoning declarations contradict each other."""


class ReasoningPhase(str, Enum):
    """Model-call phases owned by AWorld's execution loop."""

    PLAN = "plan"
    EXECUTE = "execute"
    REVIEW = "review"
    FINALIZE = "finalize"


@dataclass(frozen=True, slots=True)
class ReasoningTransportCapability:
    """One reviewed adapter's model-independent reasoning wire contract.

    A configured transport name is not itself evidence that the active
    provider adapter can lower it.  The adapter must also declare this
    capability.  This keeps the policy generic while preventing, for example,
    an Anthropic adapter from being labelled as an applied OpenAI request.
    """

    provider_name: str
    adapter_identity: str
    supported_transports: frozenset[str]
    auto_transport: str | None = None

    def __post_init__(self) -> None:
        provider_name = str(self.provider_name).strip().lower()
        if not provider_name:
            raise ReasoningPolicyError("reasoning capability provider is required")
        if (
            not isinstance(self.adapter_identity, str)
            or not self.adapter_identity.strip()
            or len(self.adapter_identity) > 256
        ):
            raise ReasoningPolicyError(
                "reasoning capability adapter_identity is invalid"
            )
        transports = frozenset(self.supported_transports)
        if not transports or not transports.issubset(REASONING_WIRE_TRANSPORTS):
            raise ReasoningPolicyError(
                "reasoning capability has unsupported transports"
            )
        if self.auto_transport is not None and self.auto_transport not in transports:
            raise ReasoningPolicyError(
                "reasoning capability auto transport must be supported"
            )
        object.__setattr__(self, "provider_name", provider_name)
        object.__setattr__(self, "adapter_identity", self.adapter_identity.strip())
        object.__setattr__(self, "supported_transports", transports)


OPENAI_REASONING_CAPABILITY = ReasoningTransportCapability(
    provider_name="openai",
    adapter_identity="aworld.provider.openai.chat_completions",
    supported_transports=frozenset({"openai", "openai_chat_template"}),
    auto_transport="openai",
)

AZURE_OPENAI_REASONING_CAPABILITY = ReasoningTransportCapability(
    provider_name="azure_openai",
    adapter_identity="aworld.provider.azure_openai.chat_completions",
    supported_transports=frozenset({"openai"}),
    auto_transport="openai",
)


_CAPABILITY_UNSPECIFIED = object()


@dataclass(frozen=True, slots=True)
class ReasoningProfile:
    """An immutable reasoning selection for one execution phase."""

    reasoning_effort: str | None = None
    thinking: bool | None = None

    def __post_init__(self) -> None:
        effort = _normalize_effort(self.reasoning_effort, field="reasoning_effort")
        if self.thinking is not None and not isinstance(self.thinking, bool):
            raise ReasoningPolicyError("thinking must be a boolean or None")
        if effort is None and self.thinking is None:
            raise ReasoningPolicyError(
                "a reasoning profile must declare reasoning_effort or thinking"
            )
        effort, thinking = _complete_profile(effort, self.thinking)
        _validate_consistency(effort, thinking)
        object.__setattr__(self, "reasoning_effort", effort)
        object.__setattr__(self, "thinking", thinking)


@dataclass(frozen=True, slots=True)
class ReasoningPhasePolicy:
    """Caller-selected per-phase policy; constructing one does not activate it."""

    policy_id: str = "custom/v1"
    plan: ReasoningProfile | None = None
    execute: ReasoningProfile | None = None
    review: ReasoningProfile | None = None
    finalize: ReasoningProfile | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.policy_id, str)
            or self.policy_id != self.policy_id.strip()
            or re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}",
                self.policy_id.strip(),
            )
            is None
        ):
            raise ReasoningPolicyError("policy_id must be a bounded safe identifier")
        object.__setattr__(self, "policy_id", self.policy_id.strip())
        for phase in ReasoningPhase:
            profile = getattr(self, phase.value)
            if profile is not None and not isinstance(profile, ReasoningProfile):
                raise ReasoningPolicyError(
                    f"{phase.value} must be a ReasoningProfile or None"
                )

    def profile_for(self, phase: ReasoningPhase | str) -> ReasoningProfile | None:
        """Return the selected profile for ``phase`` without applying defaults."""

        normalized = _normalize_phase(phase)
        return getattr(self, normalized.value)

    @classmethod
    def balanced(cls) -> "ReasoningPhasePolicy":
        """Return the provider-neutral opt-in throughput canary profile."""

        return cls(
            policy_id="balanced/v1",
            plan=ReasoningProfile(reasoning_effort="max"),
            execute=ReasoningProfile(reasoning_effort="high"),
            review=ReasoningProfile(reasoning_effort="max"),
            finalize=ReasoningProfile(reasoning_effort="high"),
        )

    @classmethod
    def from_value(
        cls, value: "ReasoningPhasePolicy | Mapping[str, Any] | str | None"
    ) -> "ReasoningPhasePolicy | None":
        """Normalize one explicit policy declaration without adding a default."""

        if value is None:
            return None
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"", "off", "none"}:
                return None
            if normalized in {"balanced", "balanced/v1"}:
                return cls.balanced()
            raise ReasoningPolicyError("unsupported reasoning phase policy")
        if not isinstance(value, Mapping):
            raise ReasoningPolicyError(
                "reasoning phase policy must be a policy, mapping, string, or None"
            )
        allowed = {"policy_id", *(phase.value for phase in ReasoningPhase)}
        if set(value) - allowed:
            raise ReasoningPolicyError("reasoning phase policy has unknown fields")

        def profile(name: str) -> ReasoningProfile | None:
            raw = value.get(name)
            if raw is None:
                return None
            if isinstance(raw, ReasoningProfile):
                return raw
            if not isinstance(raw, Mapping):
                raise ReasoningPolicyError(
                    f"reasoning phase {name} must be a profile mapping or None"
                )
            unknown = set(raw) - {"reasoning_effort", "thinking"}
            if unknown:
                raise ReasoningPolicyError(
                    f"reasoning phase {name} profile has unknown fields"
                )
            return ReasoningProfile(
                reasoning_effort=raw.get("reasoning_effort"),
                thinking=raw.get("thinking"),
            )

        return cls(
            policy_id=value.get("policy_id") or "custom/v1",
            plan=profile("plan"),
            execute=profile("execute"),
            review=profile("review"),
            finalize=profile("finalize"),
        )


ReasoningSelectionSource = Literal["caller", "phase_policy", "unchanged"]


@dataclass(frozen=True, slots=True)
class ReasoningSelectionReceipt:
    """Content-free evidence of one reasoning-policy resolution."""

    phase: ReasoningPhase
    source: ReasoningSelectionSource
    reasoning_effort: str | None
    thinking: bool | None
    policy_id: str | None
    transport: str | None
    applied: bool
    reason_code: str

    def to_dict(self) -> dict[str, Any]:
        """Return a stable primitive representation suitable for telemetry."""

        return {
            "phase": self.phase.value,
            "source": self.source,
            "reasoning_effort": self.reasoning_effort,
            "thinking": self.thinking,
            "policy_id": self.policy_id,
            "transport": self.transport,
            "applied": self.applied,
            "reason_code": self.reason_code,
        }


def resolve_reasoning_request(
    *,
    phase: ReasoningPhase | str,
    model_name: str | None,
    provider: str | None,
    request_kwargs: Mapping[str, Any] | None = None,
    policy: ReasoningPhasePolicy | None = None,
    reasoning_transport: str = "auto",
    transport_capability: ReasoningTransportCapability | None | object = (
        _CAPABILITY_UNSPECIFIED
    ),
) -> tuple[dict[str, Any], ReasoningSelectionReceipt]:
    """Resolve and project reasoning settings onto a fresh request mapping.

    Precedence is intentionally small and explicit:

    1. any compatible caller declaration already present in ``request_kwargs``;
    2. the selected profile for ``phase`` from ``policy``;
    3. no change.

    Every OpenAI-compatible request carries ``reasoning_effort`` at the standard
    OpenAI request level.  Vendor chat-template fields are mirrored only when
    explicitly selected or when the caller already supplied that request shape.
    Unsupported providers fail open without modifying the request.
    """

    normalized_phase = _normalize_phase(phase)
    normalized_transport = _normalize_reasoning_transport(reasoning_transport)
    if request_kwargs is None:
        original: dict[str, Any] = {}
    elif not isinstance(request_kwargs, Mapping):
        raise TypeError("request_kwargs must be a mapping or None")
    else:
        if policy is None and not _has_reasoning_declaration(request_kwargs):
            return dict(request_kwargs), ReasoningSelectionReceipt(
                phase=normalized_phase,
                source="unchanged",
                reasoning_effort=None,
                thinking=None,
                policy_id=None,
                transport=None,
                applied=False,
                reason_code="no_reasoning_selection",
            )
        original = _copy_managed_reasoning_containers(request_kwargs)

    explicit = _explicit_caller_profile(original)
    if explicit is not None:
        selected = explicit
        source: ReasoningSelectionSource = "caller"
        policy_id = None
    else:
        if policy is not None and not isinstance(policy, ReasoningPhasePolicy):
            raise TypeError("policy must be a ReasoningPhasePolicy or None")
        selected = policy.profile_for(normalized_phase) if policy is not None else None
        source = "phase_policy" if selected is not None else "unchanged"
        policy_id = policy.policy_id if selected is not None and policy else None

    if selected is None:
        return original, ReasoningSelectionReceipt(
            phase=normalized_phase,
            source="unchanged",
            reasoning_effort=None,
            thinking=None,
            policy_id=None,
            transport=None,
            applied=False,
            reason_code="no_reasoning_selection",
        )

    capability = _resolve_transport_capability(
        provider=provider,
        capability=transport_capability,
    )
    wire_transport = _select_wire_transport(
        configured_transport=normalized_transport,
        capability=capability,
        request_kwargs=original,
    )
    if wire_transport is None:
        return original, ReasoningSelectionReceipt(
            phase=normalized_phase,
            source=source,
            reasoning_effort=selected.reasoning_effort,
            thinking=selected.thinking,
            policy_id=policy_id,
            transport=None,
            applied=False,
            reason_code="unsupported_reasoning_transport",
        )

    wire_profile = _profile_for_wire(
        selected,
        wire_transport=wire_transport,
    )
    use_chat_template = wire_transport == "openai_chat_template"
    projected = _project_openai_profile(
        original,
        wire_profile,
        use_chat_template=use_chat_template,
    )
    if projected is None:
        return original, ReasoningSelectionReceipt(
            phase=normalized_phase,
            source=source,
            reasoning_effort=wire_profile.reasoning_effort,
            thinking=wire_profile.thinking,
            policy_id=policy_id,
            transport=None,
            applied=False,
            reason_code="incompatible_request_shape",
        )

    return projected, ReasoningSelectionReceipt(
        phase=normalized_phase,
        source=source,
        reasoning_effort=wire_profile.reasoning_effort,
        thinking=wire_profile.thinking,
        policy_id=policy_id,
        transport=(
            OPENAI_CHAT_TEMPLATE_REASONING_TRANSPORT
            if use_chat_template
            else OPENAI_REASONING_TRANSPORT
        ),
        applied=True,
        reason_code=(
            "explicit_caller_pin" if source == "caller" else "phase_policy_selected"
        ),
    )


def _normalize_phase(phase: ReasoningPhase | str) -> ReasoningPhase:
    if isinstance(phase, ReasoningPhase):
        return phase
    if not isinstance(phase, str):
        raise ReasoningPolicyError("phase must be a ReasoningPhase or string")
    try:
        return ReasoningPhase(phase.strip().lower())
    except ValueError as exc:
        allowed = ", ".join(item.value for item in ReasoningPhase)
        raise ReasoningPolicyError(
            f"unsupported reasoning phase; expected {allowed}"
        ) from exc


def _normalize_reasoning_transport(value: Any) -> str:
    if not isinstance(value, str):
        raise ReasoningPolicyError("reasoning_transport must be a string")
    normalized = value.strip().lower()
    if normalized not in REASONING_TRANSPORTS:
        allowed = ", ".join(sorted(REASONING_TRANSPORTS))
        raise ReasoningPolicyError(
            f"unsupported reasoning_transport; expected {allowed}"
        )
    return normalized


def _has_reasoning_declaration(request_kwargs: Mapping[str, Any]) -> bool:
    if any(
        request_kwargs.get(key) is not None
        for key in (
            "reasoning_effort",
            "thinking",
            "enable_thinking",
            "chat_template_kwargs",
        )
    ):
        return True
    extra_body = request_kwargs.get("extra_body")
    return isinstance(extra_body, Mapping) and any(
        extra_body.get(key) is not None
        for key in (
            "reasoning_effort",
            "thinking",
            "enable_thinking",
            "chat_template_kwargs",
        )
    )


def _copy_managed_reasoning_containers(
    request_kwargs: Mapping[str, Any],
) -> dict[str, Any]:
    result = dict(request_kwargs)
    top_template = result.get("chat_template_kwargs")
    if isinstance(top_template, Mapping):
        result["chat_template_kwargs"] = dict(top_template)
    extra_body = result.get("extra_body")
    if isinstance(extra_body, Mapping):
        copied_extra = dict(extra_body)
        nested_template = copied_extra.get("chat_template_kwargs")
        if isinstance(nested_template, Mapping):
            copied_extra["chat_template_kwargs"] = dict(nested_template)
        result["extra_body"] = copied_extra
    return result


def _normalize_effort(value: Any, *, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ReasoningPolicyError(f"{field} must be a non-empty string or None")
    normalized = value.strip().lower()
    if normalized not in REASONING_EFFORTS:
        raise ReasoningPolicyError(f"{field} has an unsupported value")
    return normalized


def _complete_profile(effort: str | None, thinking: bool | None) -> tuple[str, bool]:
    if effort is None:
        return ("max" if thinking else "off"), bool(thinking)
    if thinking is None:
        return effort, effort not in {"none", "off"}
    return effort, thinking


def _validate_consistency(effort: str, thinking: bool) -> None:
    disabled = effort in {"none", "off"}
    if disabled == thinking:
        raise ReasoningPolicyConflictError(
            "thinking and reasoning_effort declarations conflict"
        )


def _explicit_caller_profile(
    request_kwargs: Mapping[str, Any],
) -> ReasoningProfile | None:
    effort_values: list[tuple[str, str]] = []
    thinking_values: list[tuple[str, bool]] = []

    _collect_effort(
        effort_values,
        request_kwargs.get("reasoning_effort"),
        "reasoning_effort",
    )
    _collect_thinking(
        thinking_values,
        request_kwargs.get("thinking"),
        "thinking",
    )
    _collect_thinking(
        thinking_values,
        request_kwargs.get("enable_thinking"),
        "enable_thinking",
    )

    extra_body = request_kwargs.get("extra_body")
    if isinstance(extra_body, Mapping):
        _collect_effort(
            effort_values,
            extra_body.get("reasoning_effort"),
            "extra_body.reasoning_effort",
        )
        _collect_thinking(
            thinking_values,
            extra_body.get("thinking"),
            "extra_body.thinking",
        )
        _collect_thinking(
            thinking_values,
            extra_body.get("enable_thinking"),
            "extra_body.enable_thinking",
        )
        template = extra_body.get("chat_template_kwargs")
        if isinstance(template, Mapping):
            _collect_template_values(
                effort_values,
                thinking_values,
                template,
                prefix="extra_body.chat_template_kwargs",
            )

    template = request_kwargs.get("chat_template_kwargs")
    if isinstance(template, Mapping):
        _collect_template_values(
            effort_values,
            thinking_values,
            template,
            prefix="chat_template_kwargs",
        )

    effort = _one_explicit_value(effort_values, field="reasoning_effort")
    thinking = _one_explicit_value(thinking_values, field="thinking")
    if effort is None and thinking is None:
        return None
    return ReasoningProfile(reasoning_effort=effort, thinking=thinking)


def request_reasoning_effort(
    request_kwargs: Mapping[str, Any] | None,
) -> str | None:
    """Return the conflict-checked effective effort from supported aliases."""

    if request_kwargs is None:
        return None
    if not isinstance(request_kwargs, Mapping):
        raise TypeError("request_kwargs must be a mapping or None")
    profile = _explicit_caller_profile(request_kwargs)
    return profile.reasoning_effort if profile is not None else None


def _collect_template_values(
    effort_values: list[tuple[str, str]],
    thinking_values: list[tuple[str, bool]],
    template: Mapping[str, Any],
    *,
    prefix: str,
) -> None:
    _collect_effort(
        effort_values,
        template.get("reasoning_effort"),
        f"{prefix}.reasoning_effort",
    )
    _collect_thinking(
        thinking_values,
        template.get("thinking"),
        f"{prefix}.thinking",
    )
    _collect_thinking(
        thinking_values,
        template.get("enable_thinking"),
        f"{prefix}.enable_thinking",
    )


def _collect_effort(values: list[tuple[str, str]], value: Any, field: str) -> None:
    normalized = _normalize_effort(value, field=field)
    if normalized is not None:
        values.append((field, normalized))


def _collect_thinking(values: list[tuple[str, bool]], value: Any, field: str) -> None:
    if value is None:
        return
    if isinstance(value, bool):
        values.append((field, value))
        return
    if isinstance(value, Mapping):
        mode = value.get("type")
        if mode in {"enabled", "disabled"}:
            values.append((field, mode == "enabled"))
            return
    raise ReasoningPolicyError(
        f"{field} must be a boolean, an enabled/disabled mapping, or None"
    )


def _one_explicit_value(values: list[tuple[str, Any]], *, field: str) -> Any | None:
    if not values:
        return None
    unique = {value for _, value in values}
    if len(unique) != 1:
        locations = ", ".join(location for location, _ in values)
        raise ReasoningPolicyConflictError(
            f"conflicting explicit {field} declarations at {locations}"
        )
    return values[0][1]


def _resolve_transport_capability(
    *,
    provider: str | None,
    capability: ReasoningTransportCapability | None | object,
) -> ReasoningTransportCapability | None:
    normalized_provider = str(provider or "").strip().lower()
    if capability is _CAPABILITY_UNSPECIFIED:
        capability = {
            "openai": OPENAI_REASONING_CAPABILITY,
            "azure_openai": AZURE_OPENAI_REASONING_CAPABILITY,
        }.get(normalized_provider)
    if capability is None:
        return None
    if not isinstance(capability, ReasoningTransportCapability):
        raise TypeError(
            "transport_capability must be a ReasoningTransportCapability or None"
        )
    if capability.provider_name != normalized_provider:
        return None
    return capability


def _select_wire_transport(
    *,
    configured_transport: str,
    capability: ReasoningTransportCapability | None,
    request_kwargs: Mapping[str, Any],
) -> str | None:
    if capability is None:
        return None
    if configured_transport == "auto":
        requested = capability.auto_transport
        if (
            _declares_chat_template_shape(request_kwargs)
            and "openai_chat_template" in capability.supported_transports
        ):
            requested = "openai_chat_template"
    else:
        requested = configured_transport
    if requested not in capability.supported_transports:
        return None
    return requested


def _profile_for_wire(
    profile: ReasoningProfile,
    *,
    wire_transport: str,
) -> ReasoningProfile:
    effort = profile.reasoning_effort
    if wire_transport == "openai":
        effort = {"max": "xhigh", "off": "none"}.get(effort, effort)
        if effort not in OPENAI_STANDARD_REASONING_EFFORTS:
            raise ReasoningPolicyError(
                "reasoning effort cannot be represented on OpenAI transport"
            )
    return ReasoningProfile(reasoning_effort=effort)


def _declares_chat_template_shape(request_kwargs: Mapping[str, Any]) -> bool:
    if request_kwargs.get("chat_template_kwargs") is not None:
        return True
    extra_body = request_kwargs.get("extra_body")
    return isinstance(extra_body, Mapping) and (
        extra_body.get("chat_template_kwargs") is not None
    )


def _project_openai_profile(
    request_kwargs: Mapping[str, Any],
    profile: ReasoningProfile,
    *,
    use_chat_template: bool,
) -> dict[str, Any] | None:
    result = _copy_managed_reasoning_containers(request_kwargs)
    raw_extra_body = result.get("extra_body")
    if raw_extra_body is None:
        extra_body: dict[str, Any] | None = {} if use_chat_template else None
    elif isinstance(raw_extra_body, Mapping):
        extra_body = dict(raw_extra_body)
    elif use_chat_template:
        return None
    else:
        extra_body = None

    template: dict[str, Any] | None = None
    if use_chat_template:
        assert extra_body is not None
        raw_template = extra_body.get("chat_template_kwargs")
        if raw_template is None:
            template = {}
        elif isinstance(raw_template, Mapping):
            template = dict(raw_template)
        else:
            return None

    top_level_template = result.get("chat_template_kwargs")
    if use_chat_template and isinstance(top_level_template, Mapping):
        assert template is not None
        template = {**dict(top_level_template), **template}
    elif use_chat_template and top_level_template is not None:
        return None

    # Consume every accepted alias after conflict validation so unreviewed
    # provider kwargs cannot leak alongside the canonical OpenAI transport.
    for alias in ("thinking", "enable_thinking", "chat_template_kwargs"):
        result.pop(alias, None)
    if extra_body is not None:
        for alias in ("reasoning_effort", "thinking", "enable_thinking"):
            extra_body.pop(alias, None)

    if not use_chat_template:
        preserved_template: dict[str, Any] = {}
        if isinstance(top_level_template, Mapping):
            preserved_template.update(top_level_template)
        if extra_body is not None:
            nested_template = extra_body.get("chat_template_kwargs")
            if isinstance(nested_template, Mapping):
                preserved_template.update(nested_template)
        for alias in ("reasoning_effort", "thinking", "enable_thinking"):
            preserved_template.pop(alias, None)
        if preserved_template:
            if raw_extra_body is not None and not isinstance(raw_extra_body, Mapping):
                return None
            if extra_body is None:
                extra_body = {}
            extra_body["chat_template_kwargs"] = preserved_template
        elif extra_body is not None:
            extra_body.pop("chat_template_kwargs", None)

    result["reasoning_effort"] = profile.reasoning_effort
    if use_chat_template:
        assert extra_body is not None and template is not None
        template.pop("enable_thinking", None)
        template["reasoning_effort"] = profile.reasoning_effort
        template["thinking"] = profile.thinking
        extra_body["chat_template_kwargs"] = template
    if extra_body is not None:
        result["extra_body"] = extra_body
    return result


__all__ = [
    "AWORLD_REASONING_SELECTION_KWARG",
    "AZURE_OPENAI_REASONING_CAPABILITY",
    "OPENAI_CHAT_TEMPLATE_REASONING_TRANSPORT",
    "OPENAI_REASONING_CAPABILITY",
    "OPENAI_REASONING_TRANSPORT",
    "OPENAI_REASONING_TRANSPORTS",
    "OPENAI_STANDARD_REASONING_EFFORTS",
    "REASONING_EFFORTS",
    "REASONING_TRANSPORTS",
    "ReasoningPhase",
    "ReasoningPhasePolicy",
    "ReasoningPolicyConflictError",
    "ReasoningPolicyError",
    "ReasoningProfile",
    "ReasoningSelectionReceipt",
    "ReasoningTransportCapability",
    "request_reasoning_effort",
    "resolve_reasoning_request",
]
