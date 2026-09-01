"""Immutable, provider-neutral Context Compiler contracts.

This module deliberately imports only the Python standard library and the
adjacent frozen-JSON helper.  Runtime owners adapt their data down into these
contracts; compiler core never imports Agent, Amni, Memory, Skill, Tool, CLI,
or provider implementations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, ClassVar

from .frozen_json import (
    FrozenJSON,
    FrozenMap,
    canonical_json_hash,
    freeze_json,
    redacted_shape_preview,
    thaw_json,
)


class ContextKind(str, Enum):
    UNKNOWN = "unknown"
    SYSTEM = "system"
    USER = "user"
    INSTRUCTION = "instruction"
    SKILL = "skill"
    MEMORY = "memory"
    TOOL_CATALOG = "tool_catalog"
    TOOL_RESULT = "tool_result"
    STEERING = "steering"
    DELEGATION = "delegation"


class SourceKind(str, Enum):
    UNKNOWN = "unknown"
    PLATFORM = "platform"
    AGENT = "agent"
    USER = "user"
    LEGACY_MESSAGE = "legacy_message"
    PROMPT_SECTION = "prompt_section"
    NEURON = "neuron"
    MEMORY = "memory"
    WORKSPACE_FILE = "workspace_file"
    SKILL = "skill"
    TOOL = "tool"
    TOOL_CATALOG = "tool_catalog"
    STEERING = "steering"
    DELEGATION = "delegation"


class Authority(str, Enum):
    UNKNOWN = "unknown"
    PLATFORM_SYSTEM = "platform_system"
    APPLICATION_AGENT = "application_agent"
    WORKSPACE = "workspace"
    DIRECTORY = "directory"
    USER = "user"
    RECALLED_MEMORY = "recalled_memory"
    EXTERNAL_TOOL = "external_tool"


class ScopeKind(str, Enum):
    UNKNOWN = "unknown"
    GLOBAL = "global"
    WORKSPACE = "workspace"
    DIRECTORY = "directory"
    PATH_PATTERN = "path_pattern"
    SESSION = "session"
    TASK = "task"
    TURN = "turn"
    AGENT = "agent"
    CHILD_TASK = "child_task"


class Lifetime(str, Enum):
    UNKNOWN = "unknown"
    INSTALLATION = "installation"
    WORKSPACE = "workspace"
    SESSION = "session"
    TASK = "task"
    TURN = "turn"
    SINGLE_CALL = "single_call"


class Trust(str, Enum):
    UNKNOWN = "unknown"
    TRUSTED = "trusted"
    USER_CONTROLLED = "user_controlled"
    EXTERNAL_UNTRUSTED = "external_untrusted"
    TOOL_UNTRUSTED = "tool_untrusted"


class Stability(str, Enum):
    UNKNOWN = "unknown"
    STABLE = "stable"
    SESSION_STABLE = "session_stable"
    TURN_DYNAMIC = "turn_dynamic"


class ResolutionAction(str, Enum):
    UNKNOWN = "unknown"
    INCLUDED = "included"
    EXCLUDED = "excluded"
    COMPACTED = "compacted"
    OFFLOADED = "offloaded"


class ResolutionReason(str, Enum):
    UNKNOWN = "unknown"
    REQUIRED = "required"
    LEGACY_INCLUDED = "legacy_included"
    SCOPE_MISMATCH = "scope_mismatch"
    LOWER_AUTHORITY_CONFLICT = "lower_authority_conflict"
    NOT_ACTIVATED = "not_activated"
    BUDGET_COMPACTED = "budget_compacted"
    BUDGET_OFFLOADED = "budget_offloaded"
    BUDGET_INCLUDED = "budget_included"
    BUDGET_EXCLUDED = "budget_excluded"
    ATOMIC_GROUP_REQUIRED = "atomic_group_required"
    ITEM_TOKEN_LIMIT_EXCEEDED = "item_token_limit_exceeded"
    ATOMIC_GROUP_ITEM_LIMIT_EXCEEDED = "atomic_group_item_limit_exceeded"
    TOKEN_ESTIMATE_UNKNOWN = "token_estimate_unknown"
    REQUIRED_CONTEXT_BUDGET_EXCEEDED = "required_context_budget_exceeded"
    TOOL_NOT_ALLOWED = "tool_not_allowed"


class RequestCaptureStage(str, Enum):
    UNKNOWN = "unknown"
    MODEL_BOUNDARY = "model_boundary"
    PROVIDER_PREPARED = "provider_prepared"
    HTTP_SERIALIZED = "http_serialized"


class ProviderRequestFidelity(str, Enum):
    UNKNOWN = "unknown"
    MODEL_BOUNDARY = "model_boundary"
    PROVIDER_PREPARED = "provider_prepared"
    HTTP_SERIALIZED = "http_serialized"


class CacheBreakReason(str, Enum):
    UNKNOWN = "unknown"
    PROVIDER_CHANGE = "provider_change"
    MODEL_CHANGE = "model_change"
    EFFORT_CHANGE = "effort_change"
    EXECUTION_MODE_CHANGE = "execution_mode_change"
    RESPONSE_FORMAT_CHANGE = "response_format_change"
    CONTEXT_LIMIT_CHANGE = "context_limit_change"
    TOOL_CATALOG_CHANGE = "tool_catalog_change"
    SKILL_SET_CHANGE = "skill_set_change"
    POLICY_VERSION_CHANGE = "policy_version_change"
    SERIALIZATION_CHANGE = "serialization_change"
    SERIALIZED_PREFIX_CHANGE = "serialized_prefix_change"
    PROVIDER_CACHE_NAMESPACE_CHANGE = "provider_cache_namespace_change"
    HISTORY_COMPACTION = "history_compaction"
    TASK_RESET = "task_reset"
    RESUME_CACHE_EXPIRED = "resume_cache_expired"
    PROVIDER_CACHE_UNKNOWN = "provider_cache_unknown"


def _format_datetime(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_datetime(value: str | datetime | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise TypeError("datetime must be an ISO-8601 string, datetime, or None")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _non_empty(name: str, value: str | None) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


@dataclass(frozen=True, slots=True)
class ContextSource:
    kind: SourceKind
    uri: str | None = None
    version: str | None = None
    ref: FrozenJSON | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", SourceKind(self.kind))
        if self.uri is not None:
            _non_empty("uri", self.uri)
        if self.version is not None:
            _non_empty("version", self.version)
        if self.ref is not None:
            object.__setattr__(self, "ref", freeze_json(self.ref))

    @classmethod
    def unknown(cls) -> "ContextSource":
        return cls(kind=SourceKind.UNKNOWN)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "uri": self.uri,
            "version": self.version,
            "ref": thaw_json(self.ref) if self.ref is not None else None,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ContextSource":
        return cls(**payload)


_SCOPE_SELECTOR_FIELDS: dict[ScopeKind, str] = {
    ScopeKind.WORKSPACE: "workspace_id",
    ScopeKind.DIRECTORY: "directory",
    ScopeKind.PATH_PATTERN: "path_pattern",
    ScopeKind.SESSION: "session_id",
    ScopeKind.TASK: "task_id",
    ScopeKind.TURN: "turn_id",
    ScopeKind.AGENT: "agent_id",
    ScopeKind.CHILD_TASK: "child_task_id",
}


@dataclass(frozen=True, slots=True)
class ContextScope:
    """A scope may combine orthogonal selectors such as session and agent."""

    kinds: tuple[ScopeKind, ...]
    workspace_id: str | None = None
    directory: str | None = None
    path_pattern: str | None = None
    session_id: str | None = None
    task_id: str | None = None
    turn_id: str | None = None
    agent_id: str | None = None
    child_task_id: str | None = None

    def __post_init__(self) -> None:
        kinds = tuple(ScopeKind(kind) for kind in self.kinds)
        if not kinds:
            raise ValueError("scope kinds must not be empty")
        if len(set(kinds)) != len(kinds):
            raise ValueError("scope kinds must not contain duplicates")
        if ScopeKind.UNKNOWN in kinds and len(kinds) != 1:
            raise ValueError("unknown scope cannot be combined with known scopes")
        object.__setattr__(self, "kinds", kinds)
        for kind, field_name in _SCOPE_SELECTOR_FIELDS.items():
            value = getattr(self, field_name)
            if kind in kinds:
                _non_empty(field_name, value)
            elif value is not None:
                raise ValueError(f"{field_name} requires scope kind {kind.value}")

    @classmethod
    def unknown(cls) -> "ContextScope":
        return cls(kinds=(ScopeKind.UNKNOWN,))

    def to_dict(self) -> dict[str, Any]:
        return {
            "kinds": [kind.value for kind in self.kinds],
            "workspace_id": self.workspace_id,
            "directory": self.directory,
            "path_pattern": self.path_pattern,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "turn_id": self.turn_id,
            "agent_id": self.agent_id,
            "child_task_id": self.child_task_id,
        }

    def to_redacted_dict(self) -> dict[str, Any]:
        return {"kinds": [kind.value for kind in self.kinds]}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ContextScope":
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class ContextItemRef:
    item_id: str
    kind: ContextKind
    content_hash: str
    source_kind: SourceKind
    occurrence: int
    preview: str

    def __post_init__(self) -> None:
        _non_empty("item_id", self.item_id)
        object.__setattr__(self, "kind", ContextKind(self.kind))
        object.__setattr__(self, "source_kind", SourceKind(self.source_kind))
        _non_empty("content_hash", self.content_hash)
        if isinstance(self.occurrence, bool) or not isinstance(self.occurrence, int) or self.occurrence < 0:
            raise ValueError("occurrence must be a non-negative integer")
        _non_empty("preview", self.preview)
        if not re.fullmatch(
            r"<(?:object fields|array items|string chars)=\d+>|<(?:null|boolean|integer|number)>",
            self.preview,
        ):
            raise ValueError("preview must be a strictly redacted shape preview")

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "kind": self.kind.value,
            "content_hash": self.content_hash,
            "source_kind": self.source_kind.value,
            "occurrence": self.occurrence,
            "preview": self.preview,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ContextItemRef":
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class ContextItem:
    id: str
    kind: ContextKind
    payload: FrozenJSON
    task_epoch: int | None
    authority: Authority
    scope: ContextScope
    lifetime: Lifetime
    priority: int
    required: bool
    trust: Trust
    stability: Stability
    token_limit: int | None
    reducer: str | None
    source: ContextSource
    content_hash: str | None = None
    version: str | None = None
    activation_reason: str = "unknown"
    created_at: datetime | None = None
    occurrence: int = 0

    def __post_init__(self) -> None:
        _non_empty("id", self.id)
        object.__setattr__(self, "kind", ContextKind(self.kind))
        object.__setattr__(self, "authority", Authority(self.authority))
        object.__setattr__(self, "lifetime", Lifetime(self.lifetime))
        object.__setattr__(self, "trust", Trust(self.trust))
        object.__setattr__(self, "stability", Stability(self.stability))
        if isinstance(self.scope, dict):
            object.__setattr__(self, "scope", ContextScope.from_dict(self.scope))
        if isinstance(self.source, dict):
            object.__setattr__(self, "source", ContextSource.from_dict(self.source))
        if not isinstance(self.scope, ContextScope):
            raise TypeError("scope must be a ContextScope")
        if not isinstance(self.source, ContextSource):
            raise TypeError("source must be a ContextSource")
        frozen_payload = freeze_json(self.payload)
        object.__setattr__(self, "payload", frozen_payload)
        expected_hash = canonical_json_hash(frozen_payload)
        if self.content_hash is not None and self.content_hash != expected_hash:
            raise ValueError("content_hash does not match canonical payload")
        object.__setattr__(self, "content_hash", expected_hash)
        if self.task_epoch is not None and (
            isinstance(self.task_epoch, bool) or not isinstance(self.task_epoch, int) or self.task_epoch < 0
        ):
            raise ValueError("task_epoch must be a non-negative integer or None")
        if isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise ValueError("priority must be an integer")
        if not isinstance(self.required, bool):
            raise TypeError("required must be a boolean")
        if self.token_limit is not None and (
            isinstance(self.token_limit, bool)
            or not isinstance(self.token_limit, int)
            or self.token_limit < 0
        ):
            raise ValueError("token_limit must be a non-negative integer or None")
        if self.reducer is not None:
            _non_empty("reducer", self.reducer)
        if self.version is not None:
            _non_empty("version", self.version)
        _non_empty("activation_reason", self.activation_reason)
        if self.created_at is not None:
            _format_datetime(self.created_at)
        if isinstance(self.occurrence, bool) or not isinstance(self.occurrence, int) or self.occurrence < 0:
            raise ValueError("occurrence must be a non-negative integer")

    def to_ref(self) -> ContextItemRef:
        return ContextItemRef(
            item_id=self.id,
            kind=self.kind,
            content_hash=self.content_hash or "",
            source_kind=self.source.kind,
            occurrence=self.occurrence,
            preview=redacted_shape_preview(self.payload),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "payload": thaw_json(self.payload),
            "task_epoch": self.task_epoch,
            "authority": self.authority.value,
            "scope": self.scope.to_dict(),
            "lifetime": self.lifetime.value,
            "priority": self.priority,
            "required": self.required,
            "trust": self.trust.value,
            "stability": self.stability.value,
            "token_limit": self.token_limit,
            "reducer": self.reducer,
            "source": self.source.to_dict(),
            "content_hash": self.content_hash,
            "version": self.version,
            "activation_reason": self.activation_reason,
            "created_at": _format_datetime(self.created_at) if self.created_at else None,
            "occurrence": self.occurrence,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ContextItem":
        values = dict(payload)
        values["created_at"] = _parse_datetime(values.get("created_at"))
        return cls(**values)


@dataclass(frozen=True, slots=True)
class TokenEstimate:
    value: int | None
    estimator: str | None
    exact: bool = False

    def __post_init__(self) -> None:
        if self.value is None:
            if self.exact or self.estimator is not None:
                raise ValueError("unknown token estimate cannot be exact or name an estimator")
            return
        if isinstance(self.value, bool) or not isinstance(self.value, int) or self.value < 0:
            raise ValueError("token estimate value must be a non-negative integer or None")
        _non_empty("estimator", self.estimator)
        if not isinstance(self.exact, bool):
            raise TypeError("exact must be a boolean")

    @classmethod
    def unknown(cls) -> "TokenEstimate":
        return cls(value=None, estimator=None, exact=False)

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "estimator": self.estimator, "exact": self.exact}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TokenEstimate":
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class TokenAccounting:
    total_before: TokenEstimate
    total_after: TokenEstimate
    reserved_output: TokenEstimate
    context_limit: TokenEstimate
    by_kind: tuple[tuple[ContextKind, TokenEstimate], ...] = ()
    provider_protocol_reserve: TokenEstimate | None = None
    safety_margin: TokenEstimate | None = None
    available_input: TokenEstimate | None = None

    def __post_init__(self) -> None:
        for name in ("total_before", "total_after", "reserved_output", "context_limit"):
            value = getattr(self, name)
            if isinstance(value, dict):
                object.__setattr__(self, name, TokenEstimate.from_dict(value))
            elif not isinstance(value, TokenEstimate):
                raise TypeError(f"{name} must be a TokenEstimate")
        for name in (
            "provider_protocol_reserve",
            "safety_margin",
            "available_input",
        ):
            value = getattr(self, name)
            if isinstance(value, dict):
                object.__setattr__(self, name, TokenEstimate.from_dict(value))
            elif value is not None and not isinstance(value, TokenEstimate):
                raise TypeError(f"{name} must be a TokenEstimate or None")
        normalized: list[tuple[ContextKind, TokenEstimate]] = []
        for entry in self.by_kind:
            if isinstance(entry, dict):
                kind = ContextKind(entry["kind"])
                estimate = TokenEstimate.from_dict(entry["estimate"])
            else:
                kind, estimate = entry
                kind = ContextKind(kind)
                if isinstance(estimate, dict):
                    estimate = TokenEstimate.from_dict(estimate)
            if not isinstance(estimate, TokenEstimate):
                raise TypeError("by_kind estimates must be TokenEstimate values")
            normalized.append((kind, estimate))
        if len({kind for kind, _ in normalized}) != len(normalized):
            raise ValueError("by_kind must not contain duplicate kinds")
        object.__setattr__(self, "by_kind", tuple(normalized))
        reserve_evidence = (
            self.provider_protocol_reserve,
            self.safety_margin,
            self.available_input,
        )
        if any(value is not None for value in reserve_evidence) and not all(
            value is not None for value in reserve_evidence
        ):
            raise ValueError("input budget reserve evidence must be complete or absent")
        estimates = (
            self.context_limit,
            self.reserved_output,
            *reserve_evidence,
        )
        if all(value is not None and value.value is not None for value in estimates):
            assert self.provider_protocol_reserve is not None
            assert self.safety_margin is not None
            assert self.available_input is not None
            expected = (
                self.reserved_output.value
                + self.provider_protocol_reserve.value
                + self.safety_margin.value
                + self.available_input.value
            )
            if expected != self.context_limit.value:
                raise ValueError("input budget reserves must sum to context_limit")
            if (
                self.total_after.value is not None
                and self.total_after.value > self.available_input.value
            ):
                raise ValueError("total_after must not exceed available_input")

    @classmethod
    def unknown(cls) -> "TokenAccounting":
        unknown = TokenEstimate.unknown()
        return cls(unknown, unknown, unknown, unknown)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "total_before": self.total_before.to_dict(),
            "total_after": self.total_after.to_dict(),
            "reserved_output": self.reserved_output.to_dict(),
            "context_limit": self.context_limit.to_dict(),
            "by_kind": [
                {"kind": kind.value, "estimate": estimate.to_dict()}
                for kind, estimate in self.by_kind
            ],
        }
        if self.provider_protocol_reserve is not None:
            payload["provider_protocol_reserve"] = (
                self.provider_protocol_reserve.to_dict()
            )
            payload["safety_margin"] = self.safety_margin.to_dict()
            payload["available_input"] = self.available_input.to_dict()
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TokenAccounting":
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class ResolutionDecision:
    item_id: str
    action: ResolutionAction
    reason: ResolutionReason
    tokens_before: TokenEstimate
    tokens_after: TokenEstimate
    authority: Authority
    scope: ContextScope
    trust: Trust
    content_hash: str
    artifact_ref: str | None = None

    def __post_init__(self) -> None:
        _non_empty("item_id", self.item_id)
        object.__setattr__(self, "action", ResolutionAction(self.action))
        object.__setattr__(self, "reason", ResolutionReason(self.reason))
        object.__setattr__(self, "authority", Authority(self.authority))
        object.__setattr__(self, "trust", Trust(self.trust))
        if isinstance(self.tokens_before, dict):
            object.__setattr__(self, "tokens_before", TokenEstimate.from_dict(self.tokens_before))
        if isinstance(self.tokens_after, dict):
            object.__setattr__(self, "tokens_after", TokenEstimate.from_dict(self.tokens_after))
        if isinstance(self.scope, dict):
            object.__setattr__(self, "scope", ContextScope.from_dict(self.scope))
        if not isinstance(self.tokens_before, TokenEstimate):
            raise TypeError("tokens_before must be a TokenEstimate")
        if not isinstance(self.tokens_after, TokenEstimate):
            raise TypeError("tokens_after must be a TokenEstimate")
        if not isinstance(self.scope, ContextScope):
            raise TypeError("scope must be a ContextScope")
        _non_empty("content_hash", self.content_hash)
        if self.artifact_ref is not None:
            _non_empty("artifact_ref", self.artifact_ref)

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "action": self.action.value,
            "reason": self.reason.value,
            "tokens_before": self.tokens_before.to_dict(),
            "tokens_after": self.tokens_after.to_dict(),
            "authority": self.authority.value,
            "scope": self.scope.to_dict(),
            "trust": self.trust.value,
            "content_hash": self.content_hash,
            "artifact_ref": self.artifact_ref,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ResolutionDecision":
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class InferenceProfile:
    provider: str
    model: str
    reasoning_effort: str | None
    execution_mode: str
    context_limit: int | None
    response_format_hash: str | None = None

    def __post_init__(self) -> None:
        _non_empty("provider", self.provider)
        _non_empty("model", self.model)
        _non_empty("execution_mode", self.execution_mode)
        if self.reasoning_effort is not None:
            _non_empty("reasoning_effort", self.reasoning_effort)
        if self.context_limit is not None and (
            isinstance(self.context_limit, bool)
            or not isinstance(self.context_limit, int)
            or self.context_limit <= 0
        ):
            raise ValueError("context_limit must be a positive integer or None")
        if self.response_format_hash is not None:
            _non_empty("response_format_hash", self.response_format_hash)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "execution_mode": self.execution_mode,
            "context_limit": self.context_limit,
            "response_format_hash": self.response_format_hash,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "InferenceProfile":
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class CacheIdentity:
    inference_profile: InferenceProfile
    serialization_version: str
    policy_version: str
    tool_catalog_hash: str
    skill_set_hash: str
    serialized_prefix_hash: str
    provider_cache_namespace: str | None

    def __post_init__(self) -> None:
        if isinstance(self.inference_profile, dict):
            object.__setattr__(
                self, "inference_profile", InferenceProfile.from_dict(self.inference_profile)
            )
        if not isinstance(self.inference_profile, InferenceProfile):
            raise TypeError("inference_profile must be an InferenceProfile")
        for name in (
            "serialization_version",
            "policy_version",
            "tool_catalog_hash",
            "skill_set_hash",
            "serialized_prefix_hash",
        ):
            _non_empty(name, getattr(self, name))
        if self.provider_cache_namespace is not None:
            _non_empty("provider_cache_namespace", self.provider_cache_namespace)

    def to_dict(self) -> dict[str, Any]:
        return {
            "inference_profile": self.inference_profile.to_dict(),
            "serialization_version": self.serialization_version,
            "policy_version": self.policy_version,
            "tool_catalog_hash": self.tool_catalog_hash,
            "skill_set_hash": self.skill_set_hash,
            "serialized_prefix_hash": self.serialized_prefix_hash,
            "provider_cache_namespace": self.provider_cache_namespace,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CacheIdentity":
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class ProviderRequestSnapshot:
    request_id: str | None
    provider_name: str | None
    payload: FrozenMap
    capture_stage: RequestCaptureStage
    fidelity: ProviderRequestFidelity
    content_hash: str | None = None
    serialized_checksum: str | None = None

    def __post_init__(self) -> None:
        if self.request_id is not None:
            _non_empty("request_id", self.request_id)
        if self.provider_name is not None:
            _non_empty("provider_name", self.provider_name)
        frozen = freeze_json(self.payload)
        if not isinstance(frozen, FrozenMap):
            raise TypeError("provider request payload must be a JSON object")
        object.__setattr__(self, "payload", frozen)
        object.__setattr__(self, "capture_stage", RequestCaptureStage(self.capture_stage))
        object.__setattr__(self, "fidelity", ProviderRequestFidelity(self.fidelity))
        expected_hash = canonical_json_hash(frozen)
        if self.content_hash is not None and self.content_hash != expected_hash:
            raise ValueError("content_hash does not match canonical provider request")
        object.__setattr__(self, "content_hash", expected_hash)
        if self.serialized_checksum is not None:
            _non_empty("serialized_checksum", self.serialized_checksum)

    def thaw(self) -> dict[str, Any]:
        return thaw_json(self.payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "provider_name": self.provider_name,
            "payload": self.thaw(),
            "capture_stage": self.capture_stage.value,
            "fidelity": self.fidelity.value,
            "content_hash": self.content_hash,
            "serialized_checksum": self.serialized_checksum,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ProviderRequestSnapshot":
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class ResolvedContext:
    SCHEMA_VERSION: ClassVar[str] = "aworld.context.resolved.v1"

    messages: tuple[FrozenJSON, ...]
    tools: tuple[FrozenJSON, ...]
    provider_params: FrozenMap
    stable_prefix_hash: str
    serialized_prefix_hash: str
    dynamic_context_hash: str
    cache_identity: CacheIdentity
    cache_break_reason: CacheBreakReason | None
    token_accounting: TokenAccounting
    decisions: tuple[ResolutionDecision, ...]
    request_snapshot: ProviderRequestSnapshot
    compiler_version: str

    def __post_init__(self) -> None:
        messages = freeze_json(self.messages)
        tools = freeze_json(self.tools)
        params = freeze_json(self.provider_params)
        if not isinstance(messages, tuple) or not isinstance(tools, tuple):
            raise TypeError("messages and tools must be JSON arrays")
        if not isinstance(params, FrozenMap):
            raise TypeError("provider_params must be a JSON object")
        object.__setattr__(self, "messages", messages)
        object.__setattr__(self, "tools", tools)
        object.__setattr__(self, "provider_params", params)
        for name in ("stable_prefix_hash", "serialized_prefix_hash", "dynamic_context_hash", "compiler_version"):
            _non_empty(name, getattr(self, name))
        if isinstance(self.cache_identity, dict):
            object.__setattr__(self, "cache_identity", CacheIdentity.from_dict(self.cache_identity))
        if not isinstance(self.cache_identity, CacheIdentity):
            raise TypeError("cache_identity must be a CacheIdentity")
        if self.cache_break_reason is not None:
            object.__setattr__(self, "cache_break_reason", CacheBreakReason(self.cache_break_reason))
        if isinstance(self.token_accounting, dict):
            object.__setattr__(
                self, "token_accounting", TokenAccounting.from_dict(self.token_accounting)
            )
        if not isinstance(self.token_accounting, TokenAccounting):
            raise TypeError("token_accounting must be a TokenAccounting")
        object.__setattr__(
            self,
            "decisions",
            tuple(
                ResolutionDecision.from_dict(item) if isinstance(item, dict) else item
                for item in self.decisions
            ),
        )
        if isinstance(self.request_snapshot, dict):
            object.__setattr__(
                self,
                "request_snapshot",
                ProviderRequestSnapshot.from_dict(self.request_snapshot),
            )
        if not all(isinstance(item, ResolutionDecision) for item in self.decisions):
            raise TypeError("decisions must contain ResolutionDecision values")
        if not isinstance(self.request_snapshot, ProviderRequestSnapshot):
            raise TypeError("request_snapshot must be a ProviderRequestSnapshot")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "messages": thaw_json(self.messages),
            "tools": thaw_json(self.tools),
            "provider_params": thaw_json(self.provider_params),
            "stable_prefix_hash": self.stable_prefix_hash,
            "serialized_prefix_hash": self.serialized_prefix_hash,
            "dynamic_context_hash": self.dynamic_context_hash,
            "cache_identity": self.cache_identity.to_dict(),
            "cache_break_reason": self.cache_break_reason.value if self.cache_break_reason else None,
            "token_accounting": self.token_accounting.to_dict(),
            "decisions": [decision.to_dict() for decision in self.decisions],
            "request_snapshot": self.request_snapshot.to_dict(),
            "compiler_version": self.compiler_version,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ResolvedContext":
        values = dict(payload)
        schema_version = values.pop("schema_version", cls.SCHEMA_VERSION)
        if schema_version != cls.SCHEMA_VERSION:
            raise ValueError(f"unsupported resolved context schema: {schema_version}")
        return cls(**values)


__all__ = [
    "Authority",
    "CacheBreakReason",
    "CacheIdentity",
    "ContextItem",
    "ContextItemRef",
    "ContextKind",
    "ContextScope",
    "ContextSource",
    "InferenceProfile",
    "Lifetime",
    "ProviderRequestFidelity",
    "ProviderRequestSnapshot",
    "RequestCaptureStage",
    "ResolutionAction",
    "ResolutionDecision",
    "ResolutionReason",
    "ResolvedContext",
    "ScopeKind",
    "SourceKind",
    "Stability",
    "TokenAccounting",
    "TokenEstimate",
    "Trust",
]
