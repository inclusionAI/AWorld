"""Progressive Skill disclosure and task-sticky Tool catalog contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, ClassVar, Iterable, Mapping

from .frozen_json import FrozenJSON, canonical_json_hash, freeze_json
from .models import CacheBreakReason, ContextItem, ContextKind


def _identifier(name: str, value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value
    ):
        raise ValueError(f"{name} must be a stable identifier")


class DisclosureLevel(str, Enum):
    INDEX = "index"
    DESCRIPTOR = "descriptor"
    CONTENT = "content"


class CatalogChangeAction(str, Enum):
    ACCEPT_CURRENT_EPOCH = "accept_current_epoch"
    CHILD_CONTEXT = "child_context"
    DEFER_NEXT_EPOCH = "defer_next_epoch"


@dataclass(frozen=True, slots=True)
class SkillIndexEntry:
    skill_id: str
    name: str
    description: str
    trigger_codes: tuple[str, ...]
    risk: str
    estimated_tokens: int
    version: str

    def __post_init__(self) -> None:
        _identifier("skill_id", self.skill_id)
        _identifier("version", self.version)
        for name in ("name", "description", "risk"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        object.__setattr__(self, "trigger_codes", tuple(self.trigger_codes))
        if isinstance(self.estimated_tokens, bool) or not isinstance(
            self.estimated_tokens, int
        ) or self.estimated_tokens < 0:
            raise ValueError("estimated_tokens must be non-negative")


@dataclass(frozen=True, slots=True)
class SkillDescriptor:
    index: SkillIndexEntry
    required_tools: tuple[str, ...]
    resource_refs: tuple[str, ...]
    content_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "required_tools", tuple(self.required_tools))
        object.__setattr__(self, "resource_refs", tuple(self.resource_refs))
        for tool in self.required_tools:
            _identifier("required tool", tool)
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.content_hash):
            raise ValueError("content_hash must be a canonical sha256 hash")


@dataclass(frozen=True, slots=True)
class SkillActivation:
    skill_id: str
    level: DisclosureLevel
    activated: bool
    reason_code: str
    loaded_tokens: int
    requested_tools: tuple[str, ...] = ()
    unavailable_tools: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _identifier("skill_id", self.skill_id)
        object.__setattr__(self, "level", DisclosureLevel(self.level))
        _identifier("reason_code", self.reason_code)
        object.__setattr__(self, "requested_tools", tuple(self.requested_tools))
        object.__setattr__(
            self, "unavailable_tools", tuple(self.unavailable_tools)
        )
        if isinstance(self.loaded_tokens, bool) or not isinstance(
            self.loaded_tokens, int
        ) or self.loaded_tokens < 0:
            raise ValueError("loaded_tokens must be non-negative")


@dataclass(frozen=True, slots=True)
class SkillCatalogEntry:
    """One exact Skill content/version bound to its model-visible Tool ids."""

    skill_id: str
    descriptor_content_hash: str
    required_tools: tuple[str, ...]
    content_item: ContextItem

    def __post_init__(self) -> None:
        _identifier("skill_id", self.skill_id)
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.descriptor_content_hash):
            raise ValueError("descriptor_content_hash must be a canonical sha256 hash")
        object.__setattr__(self, "required_tools", tuple(self.required_tools))
        if len(set(self.required_tools)) != len(self.required_tools):
            raise ValueError("required_tools must be unique")
        for tool_id in self.required_tools:
            _identifier("required tool", tool_id)
        if not isinstance(self.content_item, ContextItem):
            raise TypeError("content_item must be a ContextItem")
        if self.content_item.kind is not ContextKind.SKILL:
            raise ValueError("Skill Catalog content must be a Skill ContextItem")
        ref = self.content_item.source.ref
        if ref is None or ref.get("skill_id") != self.skill_id:
            raise ValueError("Skill content item identity does not match skill_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "descriptor_content_hash": self.descriptor_content_hash,
            "required_tools": list(self.required_tools),
            "content_item": self.content_item.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SkillCatalogEntry":
        if not isinstance(value, dict) or set(value) != {
            "skill_id", "descriptor_content_hash", "required_tools", "content_item"
        }:
            raise ValueError("invalid Skill Catalog entry")
        return cls(
            skill_id=value["skill_id"],
            descriptor_content_hash=value["descriptor_content_hash"],
            required_tools=tuple(value["required_tools"]),
            content_item=ContextItem.from_dict(value["content_item"]),
        )


@dataclass(frozen=True, slots=True)
class TaskSkillSnapshot:
    """Task-sticky Skill content and its exact resolved Tool dependency set."""

    SCHEMA_VERSION: ClassVar[str] = "aworld.context.task-skill-snapshot.v1"

    task_epoch: int
    entries: tuple[SkillCatalogEntry, ...]
    snapshot_hash: str

    @classmethod
    def build(
        cls, task_epoch: int, entries: Iterable[SkillCatalogEntry]
    ) -> "TaskSkillSnapshot":
        values = tuple(entries)
        if isinstance(task_epoch, bool) or not isinstance(task_epoch, int) or task_epoch < 0:
            raise ValueError("task_epoch must be a non-negative integer")
        if len({entry.skill_id for entry in values}) != len(values):
            raise ValueError("Skill ids must be unique")
        snapshot_hash = canonical_json_hash(
            [
                {
                    "skill_id": entry.skill_id,
                    "descriptor_content_hash": entry.descriptor_content_hash,
                    "required_tools": list(entry.required_tools),
                    "content_hash": entry.content_item.content_hash,
                    "content_version": entry.content_item.version,
                }
                for entry in values
            ]
        )
        return cls(task_epoch=task_epoch, entries=values, snapshot_hash=snapshot_hash)

    def __post_init__(self) -> None:
        if isinstance(self.task_epoch, bool) or not isinstance(self.task_epoch, int) or self.task_epoch < 0:
            raise ValueError("task_epoch must be a non-negative integer")
        object.__setattr__(self, "entries", tuple(self.entries))
        if not all(isinstance(entry, SkillCatalogEntry) for entry in self.entries):
            raise TypeError("entries must contain SkillCatalogEntry values")
        if len({entry.skill_id for entry in self.entries}) != len(self.entries):
            raise ValueError("Skill ids must be unique")
        if any(entry.content_item.task_epoch != self.task_epoch for entry in self.entries):
            raise ValueError("Skill content item epoch does not match snapshot")
        expected = canonical_json_hash(
            [
                {
                    "skill_id": entry.skill_id,
                    "descriptor_content_hash": entry.descriptor_content_hash,
                    "required_tools": list(entry.required_tools),
                    "content_hash": entry.content_item.content_hash,
                    "content_version": entry.content_item.version,
                }
                for entry in self.entries
            ]
        )
        if expected != self.snapshot_hash:
            raise ValueError("Task Skill snapshot hash mismatch")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "task_epoch": self.task_epoch,
            "entries": [entry.to_dict() for entry in self.entries],
            "snapshot_hash": self.snapshot_hash,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TaskSkillSnapshot":
        if not isinstance(value, dict) or set(value) != {
            "schema_version", "task_epoch", "entries", "snapshot_hash"
        }:
            raise ValueError("invalid Task Skill snapshot")
        if value["schema_version"] != cls.SCHEMA_VERSION:
            raise ValueError("unsupported Task Skill snapshot schema")
        return cls(
            task_epoch=value["task_epoch"],
            entries=tuple(SkillCatalogEntry.from_dict(entry) for entry in value["entries"]),
            snapshot_hash=value["snapshot_hash"],
        )


@dataclass(frozen=True, slots=True)
class SkillCatalogTransition:
    snapshot: TaskSkillSnapshot
    candidate_snapshot: TaskSkillSnapshot
    applied: tuple[str, ...]
    retained_previous: tuple[str, ...]
    deferred: tuple[str, ...]
    deactivated: tuple[str, ...]


def transition_task_skills(
    previous: TaskSkillSnapshot | None,
    candidate: TaskSkillSnapshot,
    *,
    available_tool_ids: Iterable[str],
    sticky: bool,
) -> SkillCatalogTransition:
    """Apply Skill content and resolved Tool dependencies as one snapshot."""
    if previous is not None and previous.task_epoch != candidate.task_epoch:
        previous = None
    available = set(available_tool_ids)
    previous_by_id = {
        entry.skill_id: entry for entry in previous.entries
    } if previous is not None else {}
    applied_entries: list[SkillCatalogEntry] = []
    applied: list[str] = []
    retained: list[str] = []
    deferred: list[str] = []
    deactivated: list[str] = []
    for entry in candidate.entries:
        old = previous_by_id.get(entry.skill_id)
        if previous is not None and sticky and old is None:
            deferred.append(entry.skill_id)
            continue
        missing_dependencies = set(entry.required_tools) - available
        if missing_dependencies:
            added_dependencies = (
                set(entry.required_tools) - set(old.required_tools)
                if old is not None
                else set(entry.required_tools)
            )
            if (
                old is not None
                and sticky
                and missing_dependencies.issubset(added_dependencies)
                and set(old.required_tools).issubset(available)
            ):
                applied_entries.append(old)
                retained.append(entry.skill_id)
                deferred.append(entry.skill_id)
            else:
                deactivated.append(entry.skill_id)
            continue
        applied_entries.append(entry)
        applied.append(entry.skill_id)
    snapshot = TaskSkillSnapshot.build(candidate.task_epoch, applied_entries)
    return SkillCatalogTransition(
        snapshot=snapshot,
        candidate_snapshot=candidate,
        applied=tuple(applied),
        retained_previous=tuple(retained),
        deferred=tuple(deferred),
        deactivated=tuple(deactivated),
    )


def route_skills(
    index: Iterable[SkillIndexEntry],
    descriptors: Iterable[SkillDescriptor],
    *,
    explicit_skill_ids: Iterable[str] = (),
    matched_trigger_codes: Iterable[str] = (),
    model_ranked_skill_ids: Iterable[str] = (),
    allowed_risks: Iterable[str] = (),
    content_available_ids: Iterable[str] = (),
) -> tuple[SkillActivation, ...]:
    """Resolve progressive disclosure; model ranking may select but never grant risk."""
    index_values = tuple(index)
    descriptor_values = tuple(descriptors)
    descriptor_by_id = {
        descriptor.index.skill_id: descriptor for descriptor in descriptor_values
    }
    if len(descriptor_by_id) != len(descriptor_values):
        raise ValueError("Skill descriptors must have unique ids")
    index_ids = {entry.skill_id for entry in index_values}
    if len(index_ids) != len(index_values):
        raise ValueError("Skill index entries must have unique ids")
    if set(descriptor_by_id) - index_ids:
        raise ValueError("Skill descriptor is absent from the index")
    explicit = set(explicit_skill_ids)
    model_ranked = set(model_ranked_skill_ids)
    unknown_requests = (explicit | model_ranked) - index_ids
    if unknown_requests:
        raise ValueError("Skill routing references an unknown skill id")
    trigger_codes = set(matched_trigger_codes)
    allowed_risk_values = set(allowed_risks)
    content_available = set(content_available_ids)
    decisions: list[SkillActivation] = []
    for entry in index_values:
        explicit_match = entry.skill_id in explicit
        rule_match = bool(set(entry.trigger_codes) & trigger_codes)
        model_match = entry.skill_id in model_ranked
        risk_allowed = entry.risk in allowed_risk_values
        activated = risk_allowed and (explicit_match or rule_match or model_match)
        descriptor = descriptor_by_id.get(entry.skill_id)
        if not activated:
            reason = (
                "skill_risk_not_allowed"
                if explicit_match or rule_match or model_match
                else "skill_not_activated"
            )
            level = DisclosureLevel.INDEX
        elif descriptor is None:
            reason = "skill_descriptor_unavailable"
            level = DisclosureLevel.INDEX
            activated = False
        elif entry.skill_id in content_available:
            reason = (
                "skill_explicitly_selected"
                if explicit_match
                else "skill_rule_matched" if rule_match
                else "skill_model_ranked"
            )
            level = DisclosureLevel.CONTENT
        else:
            reason = "skill_content_unavailable"
            level = DisclosureLevel.DESCRIPTOR
        decisions.append(
            SkillActivation(
                skill_id=entry.skill_id,
                level=level,
                activated=activated,
                reason_code=reason,
                loaded_tokens=(entry.estimated_tokens if activated else 0),
                requested_tools=(
                    descriptor.required_tools
                    if activated and descriptor is not None
                    else ()
                ),
            )
        )
    return tuple(decisions)


@dataclass(frozen=True, slots=True)
class ToolCatalogEntry:
    tool_id: str
    schema: FrozenJSON
    schema_version: str
    source: str
    estimated_tokens: int

    def __post_init__(self) -> None:
        _identifier("tool_id", self.tool_id)
        _identifier("schema_version", self.schema_version)
        _identifier("source", self.source)
        object.__setattr__(self, "schema", freeze_json(self.schema))
        if isinstance(self.estimated_tokens, bool) or not isinstance(
            self.estimated_tokens, int
        ) or self.estimated_tokens < 0:
            raise ValueError("estimated_tokens must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        from .frozen_json import thaw_json

        return {
            "tool_id": self.tool_id,
            "schema": thaw_json(self.schema),
            "schema_version": self.schema_version,
            "source": self.source,
            "estimated_tokens": self.estimated_tokens,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ToolCatalogEntry":
        if not isinstance(value, dict) or set(value) != {
            "tool_id", "schema", "schema_version", "source", "estimated_tokens"
        }:
            raise ValueError("invalid Tool Catalog entry")
        return cls(**value)


@dataclass(frozen=True, slots=True)
class TaskCatalogSnapshot:
    SCHEMA_VERSION: ClassVar[str] = "aworld.context.task-tool-catalog.v1"

    task_epoch: int
    entries: tuple[ToolCatalogEntry, ...]
    catalog_hash: str

    @classmethod
    def build(
        cls, task_epoch: int, entries: Iterable[ToolCatalogEntry]
    ) -> "TaskCatalogSnapshot":
        values = tuple(entries)
        if len({entry.tool_id for entry in values}) != len(values):
            raise ValueError("tool ids must be unique")
        return cls(
            task_epoch=task_epoch,
            entries=values,
            catalog_hash=canonical_json_hash(
                [
                    {
                        "tool_id": entry.tool_id,
                        "schema": entry.schema,
                        "schema_version": entry.schema_version,
                    }
                    for entry in values
                ]
            ),
        )

    def __post_init__(self) -> None:
        if isinstance(self.task_epoch, bool) or not isinstance(self.task_epoch, int) or self.task_epoch < 0:
            raise ValueError("task_epoch must be a non-negative integer")
        object.__setattr__(self, "entries", tuple(self.entries))
        if not all(isinstance(entry, ToolCatalogEntry) for entry in self.entries):
            raise TypeError("entries must contain ToolCatalogEntry values")
        if len({entry.tool_id for entry in self.entries}) != len(self.entries):
            raise ValueError("tool ids must be unique")
        expected = canonical_json_hash(
            [
                {
                    "tool_id": entry.tool_id,
                    "schema": entry.schema,
                    "schema_version": entry.schema_version,
                }
                for entry in self.entries
            ]
        )
        if expected != self.catalog_hash:
            raise ValueError("Task Tool Catalog hash mismatch")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "task_epoch": self.task_epoch,
            "entries": [entry.to_dict() for entry in self.entries],
            "catalog_hash": self.catalog_hash,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TaskCatalogSnapshot":
        if not isinstance(value, dict) or set(value) != {
            "schema_version", "task_epoch", "entries", "catalog_hash"
        }:
            raise ValueError("invalid Task Tool Catalog snapshot")
        if value["schema_version"] != cls.SCHEMA_VERSION:
            raise ValueError("unsupported Task Tool Catalog snapshot schema")
        return cls(
            task_epoch=value["task_epoch"],
            entries=tuple(ToolCatalogEntry.from_dict(entry) for entry in value["entries"]),
            catalog_hash=value["catalog_hash"],
        )


@dataclass(frozen=True, slots=True)
class CatalogTransition:
    snapshot: TaskCatalogSnapshot
    added: tuple[str, ...]
    removed: tuple[str, ...]
    action: CatalogChangeAction
    cache_break_reason: CacheBreakReason | None
    candidate_snapshot: TaskCatalogSnapshot | None = None
    applied_added: tuple[str, ...] = ()
    applied_removed: tuple[str, ...] = ()
    deferred_added: tuple[str, ...] = ()


def compile_minimal_tool_catalog(
    entries: Iterable[ToolCatalogEntry],
    *,
    base_tools: Iterable[str],
    skill_requested_tools: Iterable[str],
    agent_allowlist: Iterable[str] | None = None,
    workspace_allowlist: Iterable[str] | None = None,
    user_permissions: Iterable[str] | None = None,
    task_epoch: int,
) -> TaskCatalogSnapshot:
    """Select requests from an already permission-filtered catalog.

    ``entries`` is the runtime permission upper bound.  The optional legacy
    allowlists can only further restrict that bound; they can never recover an
    entry that is absent from it.  Selection preserves the permission owner's
    schema order and never examines task text or Tool descriptions.
    """
    values = tuple(entries)
    requested = set(base_tools) | set(skill_requested_tools)
    allowed = {entry.tool_id for entry in values}
    for restriction in (agent_allowlist, workspace_allowlist, user_permissions):
        if restriction is not None:
            allowed &= set(restriction)
    selected = tuple(
        entry for entry in values
        if entry.tool_id in requested and entry.tool_id in allowed
    )
    return TaskCatalogSnapshot.build(task_epoch, selected)


def preserve_unmanaged_tool_namespaces(
    available_tool_ids: Iterable[str],
    *,
    requested_tools: Iterable[str],
    tool_identity_mapping: Mapping[str, str] | None,
) -> tuple[str, ...]:
    """Keep permission-filtered MCP namespaces that no selector manages yet.

    Explicit base/Skill requests prove that a server namespace is governed by
    the progressive selector. A newly attached namespace with no such request
    must not disappear silently: retaining it is a quality-preserving fallback
    until capability metadata or a Skill can select a narrower subset.
    """
    mapping = dict(tool_identity_mapping or {})

    def namespace(tool_id: str) -> str | None:
        identity = mapping.get(tool_id)
        if not isinstance(identity, str) or "__" not in identity:
            return None
        value, _ = identity.split("__", 1)
        return value or None

    managed_namespaces = {
        value
        for tool_id in requested_tools
        for value in (namespace(tool_id),)
        if value is not None
    }
    return tuple(
        tool_id
        for tool_id in available_tool_ids
        if (value := namespace(tool_id)) is not None
        and value not in managed_namespaces
    )


def transition_task_catalog(
    previous: TaskCatalogSnapshot | None,
    candidate: TaskCatalogSnapshot,
    *,
    action: CatalogChangeAction,
) -> CatalogTransition:
    action = CatalogChangeAction(action)
    if previous is not None and previous.task_epoch != candidate.task_epoch:
        previous = None
    previous_ids = {entry.tool_id for entry in previous.entries} if previous else set()
    candidate_ids = {entry.tool_id for entry in candidate.entries}
    requested_added = candidate_ids - previous_ids
    requested_removed = previous_ids - candidate_ids
    changed = previous is not None and previous.catalog_hash != candidate.catalog_hash
    if action is CatalogChangeAction.CHILD_CONTEXT:
        # A child request is evidence for a separately scoped Context.  It
        # must never mutate the parent task's active catalog.
        snapshot = previous or TaskCatalogSnapshot.build(candidate.task_epoch, ())
    elif changed and action is CatalogChangeAction.DEFER_NEXT_EPOCH:
        # Permission/schema contractions are authoritative immediately.  Only
        # newly requested ids are deferred; keeping the previous snapshot here
        # would accidentally preserve a revoked Tool.
        snapshot = TaskCatalogSnapshot.build(
            candidate.task_epoch,
            tuple(
                entry
                for entry in candidate.entries
                if entry.tool_id in previous_ids
            ),
        )
    else:
        snapshot = candidate
    snapshot_ids = {entry.tool_id for entry in snapshot.entries}
    applied_added = snapshot_ids - previous_ids
    applied_removed = previous_ids - snapshot_ids
    actual_changed = (
        previous is not None and previous.catalog_hash != snapshot.catalog_hash
    )
    return CatalogTransition(
        snapshot=snapshot,
        added=tuple(sorted(requested_added)),
        removed=tuple(sorted(requested_removed)),
        action=action,
        cache_break_reason=(
            CacheBreakReason.TOOL_CATALOG_CHANGE if actual_changed else None
        ),
        candidate_snapshot=candidate,
        applied_added=tuple(sorted(applied_added)),
        applied_removed=tuple(sorted(applied_removed)),
        deferred_added=tuple(sorted(requested_added - applied_added)),
    )


__all__ = [
    "CatalogChangeAction",
    "CatalogTransition",
    "DisclosureLevel",
    "SkillActivation",
    "SkillCatalogEntry",
    "SkillCatalogTransition",
    "SkillDescriptor",
    "SkillIndexEntry",
    "TaskCatalogSnapshot",
    "TaskSkillSnapshot",
    "ToolCatalogEntry",
    "compile_minimal_tool_catalog",
    "preserve_unmanaged_tool_namespaces",
    "transition_task_catalog",
    "transition_task_skills",
    "route_skills",
]
