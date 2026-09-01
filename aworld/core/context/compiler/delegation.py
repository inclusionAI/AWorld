"""Least-authority delegation, Context Pack, and child merge contracts."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
import re
from typing import Iterable

from .frozen_json import (
    FrozenJSON,
    FrozenMap,
    canonical_json_hash,
    freeze_json,
    thaw_json,
)
from .models import (
    Authority,
    ContextItem,
    ContextItemRef,
    ContextScope,
    ContextSource,
    InferenceProfile,
    Lifetime,
    ScopeKind,
    Trust,
)


class MergePolicy(str, Enum):
    ANSWER_ONLY = "answer_only"
    ANSWER_EVIDENCE = "answer_evidence"
    EXPLICIT_CONTEXT_DELTA = "explicit_context_delta"


class ChildStatus(str, Enum):
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    DEPTH_EXCEEDED = "depth_exceeded"
    BUDGET_EXCEEDED = "budget_exceeded"


@dataclass(frozen=True, slots=True)
class StopCondition:
    code: str
    threshold: int | None = None

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,127}", self.code):
            raise ValueError("stop condition code must be stable")
        if self.threshold is not None and (
            isinstance(self.threshold, bool)
            or not isinstance(self.threshold, int)
            or self.threshold < 0
        ):
            raise ValueError("threshold must be non-negative or None")


@dataclass(frozen=True, slots=True)
class DelegationSpec:
    objective: str
    context_item_ids: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    token_budget: int
    max_output_tokens: int
    max_turns: int
    max_depth: int
    deadline: datetime | None
    expected_output_schema: FrozenMap
    inference_profile: InferenceProfile | None
    stop_conditions: tuple[StopCondition, ...]
    merge_policy: MergePolicy
    allowed_context_delta_kinds: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.objective, str) or not self.objective.strip():
            raise ValueError("objective must be a non-empty string")
        object.__setattr__(self, "context_item_ids", tuple(self.context_item_ids))
        object.__setattr__(self, "allowed_tools", tuple(self.allowed_tools))
        object.__setattr__(self, "stop_conditions", tuple(self.stop_conditions))
        object.__setattr__(
            self, "allowed_context_delta_kinds", tuple(self.allowed_context_delta_kinds)
        )
        if len(set(self.context_item_ids)) != len(self.context_item_ids):
            raise ValueError("context item ids must be unique")
        if len(set(self.allowed_tools)) != len(self.allowed_tools):
            raise ValueError("allowed tools must be unique")
        for name in ("token_budget", "max_output_tokens", "max_turns", "max_depth"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.max_output_tokens > self.token_budget:
            raise ValueError("max_output_tokens cannot exceed token_budget")
        if self.deadline is not None and (
            self.deadline.tzinfo is None or self.deadline.utcoffset() is None
        ):
            raise ValueError("deadline must be timezone-aware")
        schema = freeze_json(self.expected_output_schema)
        if not isinstance(schema, FrozenMap):
            raise TypeError("expected_output_schema must be a JSON object")
        object.__setattr__(self, "expected_output_schema", schema)
        if self.inference_profile is not None and not isinstance(
            self.inference_profile, InferenceProfile
        ):
            raise TypeError("inference_profile must be an InferenceProfile or None")
        object.__setattr__(self, "merge_policy", MergePolicy(self.merge_policy))


@dataclass(frozen=True, slots=True)
class ContextPack:
    objective_hash: str
    items: tuple[ContextItem, ...]
    item_refs: tuple[ContextItemRef, ...]
    allowed_tools: tuple[str, ...]
    token_budget: int
    parent_task_epoch: int
    child_depth: int
    child_task_id: str
    child_task_epoch: int
    pack_hash: str

    @classmethod
    def build(
        cls,
        *,
        spec: DelegationSpec,
        available_items: Iterable[ContextItem],
        parent_allowed_tools: Iterable[str],
        child_declared_tools: Iterable[str],
        parent_task_epoch: int,
        child_depth: int,
        child_task_id: str,
        child_task_epoch: int,
    ) -> "ContextPack":
        if child_depth > spec.max_depth:
            raise ValueError("delegation depth exceeds max_depth")
        requested_ids = set(spec.context_item_ids)
        available_values = tuple(available_items)
        available_ids = [item.id for item in available_values]
        if len(set(available_ids)) != len(available_ids):
            raise ValueError("available Context items must have unique ids")
        mandatory_ids = {
            item.id
            for item in available_values
            if item.required
            and item.authority in {
                Authority.PLATFORM_SYSTEM,
                Authority.APPLICATION_AGENT,
            }
        }
        selected_ids = requested_ids | mandatory_ids
        selected_values = tuple(
            item for item in available_values if item.id in selected_ids
        )
        missing = requested_ids - {item.id for item in selected_values}
        if missing:
            raise ValueError("delegation references unavailable Context items")
        if any(
            item.trust in {Trust.EXTERNAL_UNTRUSTED, Trust.TOOL_UNTRUSTED}
            and item.required
            for item in selected_values
        ):
            raise ValueError("untrusted content cannot become required by delegation")
        if not isinstance(child_task_id, str) or not child_task_id.strip():
            raise ValueError("child_task_id must be non-empty")
        if isinstance(child_task_epoch, bool) or not isinstance(
            child_task_epoch, int
        ) or child_task_epoch < 0:
            raise ValueError("child_task_epoch must be non-negative")
        values: list[ContextItem] = []
        for item in selected_values:
            source_ref = (
                thaw_json(item.source.ref) if item.source.ref is not None else {}
            )
            if not isinstance(source_ref, dict):
                source_ref = {"owner_ref": source_ref}
            source_ref.update(
                {
                    "delegation_context_pack": True,
                    "parent_item_id": item.id,
                    "parent_content_hash": item.content_hash,
                }
            )
            values.append(
                replace(
                    item,
                    id=f"delegated:{child_task_id}:{item.id}",
                    task_epoch=child_task_epoch,
                    scope=ContextScope(
                        kinds=(ScopeKind.CHILD_TASK,),
                        child_task_id=child_task_id,
                    ),
                    lifetime=Lifetime.TASK,
                    source=ContextSource(
                        kind=item.source.kind,
                        uri=item.source.uri,
                        version=item.source.version,
                        ref=source_ref,
                    ),
                    content_hash=None,
                )
            )
        value_tuple = tuple(values)
        tools = tuple(
            sorted(
                set(spec.allowed_tools)
                & set(parent_allowed_tools)
                & set(child_declared_tools)
            )
        )
        refs = tuple(item.to_ref() for item in value_tuple)
        objective_hash = canonical_json_hash({"objective": spec.objective})
        pack_hash = canonical_json_hash(
            {
                "objective_hash": objective_hash,
                "items": [ref.to_dict() for ref in refs],
                "allowed_tools": tools,
                "token_budget": spec.token_budget,
                "parent_task_epoch": parent_task_epoch,
                "child_depth": child_depth,
                "child_task_id": child_task_id,
                "child_task_epoch": child_task_epoch,
            }
        )
        return cls(
            objective_hash=objective_hash,
            items=value_tuple,
            item_refs=refs,
            allowed_tools=tools,
            token_budget=spec.token_budget,
            parent_task_epoch=parent_task_epoch,
            child_depth=child_depth,
            child_task_id=child_task_id,
            child_task_epoch=child_task_epoch,
            pack_hash=pack_hash,
        )


@dataclass(frozen=True, slots=True)
class ChildUsage:
    input_tokens: int
    output_tokens: int
    turns: int
    cost_microunits: int | None = None

    def __post_init__(self) -> None:
        for name in ("input_tokens", "output_tokens", "turns"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.cost_microunits is not None and self.cost_microunits < 0:
            raise ValueError("cost_microunits must be non-negative or None")


@dataclass(frozen=True, slots=True)
class ChildResult:
    status: ChildStatus
    answer: FrozenJSON | None
    evidence: tuple[FrozenJSON, ...]
    artifacts: tuple[str, ...]
    context_delta: tuple[ContextItem, ...]
    usage: ChildUsage
    reason_code: str | None = None
    schema_validated: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", ChildStatus(self.status))
        if self.answer is not None:
            object.__setattr__(self, "answer", freeze_json(self.answer))
        object.__setattr__(self, "evidence", tuple(freeze_json(v) for v in self.evidence))
        object.__setattr__(self, "artifacts", tuple(self.artifacts))
        object.__setattr__(self, "context_delta", tuple(self.context_delta))
        if not isinstance(self.usage, ChildUsage):
            raise TypeError("usage must be ChildUsage")
        if not isinstance(self.schema_validated, bool):
            raise TypeError("schema_validated must be a boolean")


@dataclass(frozen=True, slots=True)
class DelegationMergeResult:
    answer: FrozenJSON | None
    evidence: tuple[FrozenJSON, ...]
    artifacts: tuple[str, ...]
    context_delta: tuple[ContextItem, ...]
    rejected_delta_ids: tuple[str, ...]
    schema_validated: bool


_SUPPORTED_SCHEMA_KEYS = frozenset(
    {
        "type",
        "required",
        "properties",
        "items",
        "additionalProperties",
        "enum",
        "const",
        "minItems",
        "maxItems",
        "minLength",
        "maxLength",
    }
)


def _validate_schema_value(value: FrozenJSON | None, schema: FrozenMap) -> bool:
    """Validate a deterministic JSON-Schema core and reject unknown keywords."""
    if any(key not in _SUPPORTED_SCHEMA_KEYS for key in schema):
        return False
    if "const" in schema and value != schema["const"]:
        return False
    enum_values = schema.get("enum")
    if enum_values is not None:
        if not isinstance(enum_values, tuple) or value not in enum_values:
            return False
    expected_type = schema.get("type")
    if expected_type is None:
        return len(schema) == 0 or set(schema).issubset({"enum", "const"})
    if not isinstance(expected_type, str):
        return False
    type_matches = {
        "object": isinstance(value, FrozenMap),
        "array": isinstance(value, tuple),
        "string": isinstance(value, str),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }
    if not type_matches.get(expected_type, False):
        return False

    if isinstance(value, FrozenMap):
        properties = schema.get("properties", FrozenMap(()))
        if not isinstance(properties, FrozenMap):
            return False
        if any(not isinstance(child, FrozenMap) for child in properties.values()):
            return False
        additional = schema.get("additionalProperties", True)
        if not isinstance(additional, bool):
            return False
        if not additional and any(key not in properties for key in value):
            return False
        for key, child_schema in properties.items():
            if key in value and not _validate_schema_value(value[key], child_schema):
                return False
    required = schema.get("required")
    if required is not None:
        if not isinstance(required, tuple) or not isinstance(value, FrozenMap):
            return False
        if not all(isinstance(key, str) and key in value for key in required):
            return False

    if isinstance(value, tuple):
        item_schema = schema.get("items")
        if item_schema is not None:
            if not isinstance(item_schema, FrozenMap) or not all(
                _validate_schema_value(item, item_schema) for item in value
            ):
                return False
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if minimum is not None and (
            isinstance(minimum, bool)
            or not isinstance(minimum, int)
            or len(value) < minimum
        ):
            return False
        if maximum is not None and (
            isinstance(maximum, bool)
            or not isinstance(maximum, int)
            or len(value) > maximum
        ):
            return False

    if isinstance(value, str):
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        if minimum is not None and (
            isinstance(minimum, bool)
            or not isinstance(minimum, int)
            or len(value) < minimum
        ):
            return False
        if maximum is not None and (
            isinstance(maximum, bool)
            or not isinstance(maximum, int)
            or len(value) > maximum
        ):
            return False
    return True


def validate_delegation_output(answer: FrozenJSON | None, schema: FrozenMap) -> bool:
    """Validate the frozen child answer without trusting a child-supplied flag."""
    if not isinstance(schema, FrozenMap):
        return False
    return _validate_schema_value(answer, schema)


def merge_child_result(
    spec: DelegationSpec, result: ChildResult
) -> DelegationMergeResult:
    """Merge bounded outputs only; never replay a child transcript."""
    # The parent boundary independently validates the frozen answer.  The
    # child's schema_validated bit is audit evidence only, never authority.
    schema_validated = validate_delegation_output(
        result.answer, spec.expected_output_schema
    )
    evidence = (
        result.evidence
        if spec.merge_policy in {
            MergePolicy.ANSWER_EVIDENCE,
            MergePolicy.EXPLICIT_CONTEXT_DELTA,
        }
        else ()
    )
    allowed_kinds = set(spec.allowed_context_delta_kinds)
    accepted_delta = (
        tuple(
            item for item in result.context_delta if item.kind.value in allowed_kinds
        )
        if spec.merge_policy is MergePolicy.EXPLICIT_CONTEXT_DELTA
        else ()
    )
    accepted_ids = {item.id for item in accepted_delta}
    return DelegationMergeResult(
        answer=result.answer if schema_validated else None,
        evidence=evidence if schema_validated else (),
        artifacts=result.artifacts if schema_validated else (),
        context_delta=accepted_delta if schema_validated else (),
        rejected_delta_ids=tuple(
            item.id for item in result.context_delta if item.id not in accepted_ids
        ),
        schema_validated=schema_validated,
    )


__all__ = [
    "ChildResult",
    "ChildStatus",
    "ChildUsage",
    "ContextPack",
    "DelegationMergeResult",
    "DelegationSpec",
    "MergePolicy",
    "StopCondition",
    "merge_child_result",
    "validate_delegation_output",
]
