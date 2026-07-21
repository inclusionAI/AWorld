from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from aworld.self_evolve.types import SelfEvolveTargetRef


TARGET_PROVENANCE_SCHEMA_VERSION = 1


class TargetProvenanceSourceKind(str, Enum):
    SKILL = "skill"
    PROMPT = "prompt"
    TOOL_SCHEMA = "tool_schema"
    WORKSPACE_ARTIFACT = "workspace_artifact"


class TargetWriteOrigin(str, Enum):
    INSTALLED_SKILL = "installed_skill"
    REPOSITORY = "repository"
    FRAMEWORK_PROMPT = "framework_prompt"
    FRAMEWORK_TOOL_DESCRIPTION = "framework_tool_description"
    AGENT_GENERATED_ARTIFACT = "agent_generated_artifact"
    OPERATOR_SELECTION = "operator_selection"
    TARGET_INFERENCE = "target_inference"
    EXTERNAL = "external"


class TargetTrustLevel(str, Enum):
    LOCAL = "local"
    PROJECT = "project"
    FRAMEWORK = "framework"
    GENERATED = "generated"
    EXTERNAL = "external"
    PROTECTED = "protected"


class TargetSelectionOrigin(str, Enum):
    INVENTORY = "inventory"
    OPERATOR_EXPLICIT = "operator_explicit"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


class TargetProvenanceStatus(str, Enum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"


class TargetProvenancePolicyClass(str, Enum):
    TRUSTED = "trusted"
    GENERATED = "generated"
    EXTERNAL = "external"
    PROTECTED = "protected"


@dataclass(frozen=True)
class TargetProvenance:
    target: SelfEvolveTargetRef
    source_kind: TargetProvenanceSourceKind
    write_origin: TargetWriteOrigin
    trust_level: TargetTrustLevel
    protected: bool
    reason: str
    schema_version: int = TARGET_PROVENANCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.target, SelfEvolveTargetRef):
            raise ValueError(
                "target provenance requires a typed target reference"
            )
        if (
            not isinstance(self.target.target_type, str)
            or not self.target.target_type.strip()
            or not isinstance(self.target.target_id, str)
            or not self.target.target_id.strip()
            or (
                self.target.path is not None
                and (
                    not isinstance(self.target.path, str)
                    or not self.target.path.strip()
                )
            )
        ):
            raise ValueError(
                "target provenance requires valid target identity fields"
            )
        object.__setattr__(
            self,
            "source_kind",
            TargetProvenanceSourceKind(self.source_kind),
        )
        object.__setattr__(self, "write_origin", TargetWriteOrigin(self.write_origin))
        object.__setattr__(self, "trust_level", TargetTrustLevel(self.trust_level))
        if type(self.protected) is not bool:
            raise ValueError("target provenance protected flag must be boolean")
        if (
            type(self.schema_version) is not int
            or self.schema_version != TARGET_PROVENANCE_SCHEMA_VERSION
        ):
            raise ValueError("unsupported target provenance schema version")


@dataclass(frozen=True)
class TargetProvenanceResolution:
    """Total result of classifying authorization metadata for one target."""

    status: TargetProvenanceStatus
    provenance: TargetProvenance | None
    reason: str

    def __post_init__(self) -> None:
        typed_status = TargetProvenanceStatus(self.status)
        object.__setattr__(self, "status", typed_status)
        if self.provenance is not None and not isinstance(
            self.provenance,
            TargetProvenance,
        ):
            raise ValueError(
                "provenance resolution requires typed target provenance"
            )
        if (
            typed_status == TargetProvenanceStatus.RESOLVED
            and self.provenance is None
        ):
            raise ValueError(
                "resolved provenance resolution requires target provenance"
            )
        if (
            typed_status == TargetProvenanceStatus.UNRESOLVED
            and self.provenance is not None
        ):
            raise ValueError(
                "unresolved provenance resolution cannot carry target provenance"
            )

    @property
    def resolved(self) -> bool:
        return self.status == TargetProvenanceStatus.RESOLVED


_TRUSTED_COMBINATIONS = frozenset(
    {
        (
            TargetProvenanceSourceKind.SKILL,
            TargetWriteOrigin.INSTALLED_SKILL,
            TargetTrustLevel.LOCAL,
        ),
        (
            TargetProvenanceSourceKind.SKILL,
            TargetWriteOrigin.REPOSITORY,
            TargetTrustLevel.LOCAL,
        ),
        (
            TargetProvenanceSourceKind.SKILL,
            TargetWriteOrigin.REPOSITORY,
            TargetTrustLevel.PROJECT,
        ),
        (
            TargetProvenanceSourceKind.SKILL,
            TargetWriteOrigin.OPERATOR_SELECTION,
            TargetTrustLevel.LOCAL,
        ),
        (
            TargetProvenanceSourceKind.PROMPT,
            TargetWriteOrigin.FRAMEWORK_PROMPT,
            TargetTrustLevel.FRAMEWORK,
        ),
        (
            TargetProvenanceSourceKind.TOOL_SCHEMA,
            TargetWriteOrigin.FRAMEWORK_TOOL_DESCRIPTION,
            TargetTrustLevel.FRAMEWORK,
        ),
    }
)
_GENERATED_COMBINATIONS = frozenset(
    {
        (
            TargetProvenanceSourceKind.SKILL,
            TargetWriteOrigin.TARGET_INFERENCE,
            TargetTrustLevel.GENERATED,
        ),
        (
            TargetProvenanceSourceKind.WORKSPACE_ARTIFACT,
            TargetWriteOrigin.AGENT_GENERATED_ARTIFACT,
            TargetTrustLevel.GENERATED,
        ),
    }
)
_EXTERNAL_COMBINATIONS = frozenset(
    {
        (
            TargetProvenanceSourceKind.SKILL,
            TargetWriteOrigin.EXTERNAL,
            TargetTrustLevel.EXTERNAL,
        ),
    }
)
_PROTECTED_COMBINATIONS = frozenset(
    {
        (
            TargetProvenanceSourceKind.SKILL,
            TargetWriteOrigin.REPOSITORY,
            TargetTrustLevel.PROTECTED,
        ),
        (
            TargetProvenanceSourceKind.SKILL,
            TargetWriteOrigin.INSTALLED_SKILL,
            TargetTrustLevel.PROTECTED,
        ),
    }
)


def target_provenance_policy_class(
    provenance: TargetProvenance,
) -> TargetProvenancePolicyClass | None:
    """Classify only explicit, internally supported provenance combinations."""

    if _source_kind_for_target_type(provenance.target.target_type) != provenance.source_kind:
        return None
    combination = (
        provenance.source_kind,
        provenance.write_origin,
        provenance.trust_level,
    )
    if combination in _TRUSTED_COMBINATIONS:
        return (
            TargetProvenancePolicyClass.PROTECTED
            if provenance.protected
            else TargetProvenancePolicyClass.TRUSTED
        )
    if combination in _PROTECTED_COMBINATIONS and provenance.protected:
        return TargetProvenancePolicyClass.PROTECTED
    if combination in _GENERATED_COMBINATIONS and not provenance.protected:
        return TargetProvenancePolicyClass.GENERATED
    if combination in _EXTERNAL_COMBINATIONS and not provenance.protected:
        return TargetProvenancePolicyClass.EXTERNAL
    return None


def resolve_target_provenance(
    target: SelfEvolveTargetRef,
    *,
    selection_origin: TargetSelectionOrigin | str,
    inventory_provenance: TargetProvenance | None = None,
    workspace_root: str | Path | None = None,
) -> TargetProvenanceResolution:
    """Resolve target-level provenance without consulting trajectory evidence."""

    try:
        typed_origin = TargetSelectionOrigin(selection_origin)
    except ValueError:
        typed_origin = TargetSelectionOrigin.UNKNOWN

    if not target.target_type or not target.target_id:
        return _unresolved("target identity is incomplete")
    if typed_origin == TargetSelectionOrigin.UNKNOWN:
        return _unresolved("target selection origin is unknown")

    if inventory_provenance is not None:
        inventory_target = inventory_provenance.target
        if (
            inventory_target.target_type != target.target_type
            or inventory_target.target_id != target.target_id
        ):
            return _unresolved(
                "inventory provenance does not match selected target identity"
            )
        if not _target_paths_match(
            inventory_target,
            target,
            workspace_root=workspace_root,
        ):
            return _unresolved(
                "inventory provenance path does not match selected target path"
            )
        if target_provenance_policy_class(inventory_provenance) is None:
            return _unresolved("inventory provenance classification is not trusted")
        return TargetProvenanceResolution(
            status=TargetProvenanceStatus.RESOLVED,
            provenance=inventory_provenance,
            reason="selected target uses inventory provenance",
        )

    source_kind = _source_kind_for_target_type(target.target_type)
    if source_kind is None:
        return _unresolved("target type has no provenance classification")

    if typed_origin == TargetSelectionOrigin.OPERATOR_EXPLICIT:
        if not _target_path_is_local(target, workspace_root=workspace_root):
            return _unresolved("explicit target locality could not be established")
        provenance = TargetProvenance(
            target=target,
            source_kind=source_kind,
            write_origin=TargetWriteOrigin.OPERATOR_SELECTION,
            trust_level=TargetTrustLevel.LOCAL,
            protected=False,
            reason="target was explicitly selected by the operator",
        )
        return TargetProvenanceResolution(
            status=TargetProvenanceStatus.RESOLVED,
            provenance=provenance,
            reason=provenance.reason,
        )

    if typed_origin == TargetSelectionOrigin.INFERRED:
        if source_kind != TargetProvenanceSourceKind.SKILL:
            return _unresolved("inferred target type has no generated provenance policy")
        provenance = TargetProvenance(
            target=target,
            source_kind=source_kind,
            write_origin=TargetWriteOrigin.TARGET_INFERENCE,
            trust_level=TargetTrustLevel.GENERATED,
            protected=False,
            reason="inferred target is absent from the capability inventory",
        )
        return TargetProvenanceResolution(
            status=TargetProvenanceStatus.RESOLVED,
            provenance=provenance,
            reason=provenance.reason,
        )

    return _unresolved("inventory selection has no inventory provenance")


def load_target_provenance_payload(
    payload: Mapping[str, Any],
) -> TargetProvenanceResolution:
    """Strictly parse a persisted provenance sidecar as audit data."""

    if (
        type(payload.get("schema_version")) is not int
        or payload.get("schema_version") != TARGET_PROVENANCE_SCHEMA_VERSION
    ):
        return _unresolved("target provenance sidecar schema is missing or unsupported")
    target_payload = payload.get("target")
    if not isinstance(target_payload, Mapping):
        return _unresolved("target provenance sidecar target is malformed")
    target_type = target_payload.get("target_type")
    target_id = target_payload.get("target_id")
    target_path = target_payload.get("path")
    reason = payload.get("reason")
    if not isinstance(target_type, str) or not target_type:
        return _unresolved("target provenance sidecar target type is malformed")
    if not isinstance(target_id, str) or not target_id:
        return _unresolved("target provenance sidecar target id is malformed")
    if target_path is not None and (
        not isinstance(target_path, str) or not target_path
    ):
        return _unresolved("target provenance sidecar path is malformed")
    if type(payload.get("protected")) is not bool:
        return _unresolved("target provenance sidecar protected flag is malformed")
    if not isinstance(reason, str) or not reason:
        return _unresolved("target provenance sidecar reason is malformed")
    try:
        provenance = TargetProvenance(
            target=SelfEvolveTargetRef(target_type, target_id, target_path),
            source_kind=TargetProvenanceSourceKind(payload.get("source_kind")),
            write_origin=TargetWriteOrigin(payload.get("write_origin")),
            trust_level=TargetTrustLevel(payload.get("trust_level")),
            protected=payload["protected"],
            reason=reason,
            schema_version=payload["schema_version"],
        )
    except (TypeError, ValueError):
        return _unresolved("target provenance sidecar contains an unknown enum value")
    if target_provenance_policy_class(provenance) is None:
        return _unresolved("target provenance sidecar classification is not trusted")
    return TargetProvenanceResolution(
        status=TargetProvenanceStatus.RESOLVED,
        provenance=provenance,
        reason="stored target provenance sidecar is valid audit data",
    )


def canonical_local_target_path(
    raw_path: str | Path,
    *,
    workspace_root: str | Path,
) -> Path | None:
    """Return a canonical workspace-local path only when no component is a symlink."""

    root = Path(workspace_root).resolve()
    raw = Path(raw_path)
    candidate = raw if raw.is_absolute() else root / raw
    lexical = Path(os.path.abspath(candidate))
    try:
        canonical = lexical.resolve(strict=False)
        canonical.relative_to(root)
    except (OSError, ValueError):
        return None

    current = Path(lexical.anchor)
    for part in lexical.parts[1:]:
        current /= part
        try:
            if current.is_symlink():
                return None
        except OSError:
            return None
    return canonical


def _target_path_is_local(
    target: SelfEvolveTargetRef,
    *,
    workspace_root: str | Path | None,
) -> bool:
    if target.path is None or workspace_root is None:
        return False
    return (
        canonical_local_target_path(
            target.path,
            workspace_root=workspace_root,
        )
        is not None
    )


def _target_paths_match(
    inventory_target: SelfEvolveTargetRef,
    selected_target: SelfEvolveTargetRef,
    *,
    workspace_root: str | Path | None,
) -> bool:
    if inventory_target.path is None or selected_target.path is None:
        return inventory_target.path is None and selected_target.path is None
    if workspace_root is None:
        return False
    inventory_path = canonical_local_target_path(
        inventory_target.path,
        workspace_root=workspace_root,
    )
    selected_path = canonical_local_target_path(
        selected_target.path,
        workspace_root=workspace_root,
    )
    return (
        inventory_path is not None
        and selected_path is not None
        and inventory_path == selected_path
    )


def _source_kind_for_target_type(
    target_type: str,
) -> TargetProvenanceSourceKind | None:
    return {
        "skill": TargetProvenanceSourceKind.SKILL,
        "prompt-section": TargetProvenanceSourceKind.PROMPT,
        "tool-description": TargetProvenanceSourceKind.TOOL_SCHEMA,
        "workspace-artifact": TargetProvenanceSourceKind.WORKSPACE_ARTIFACT,
    }.get(target_type)


def _unresolved(reason: str) -> TargetProvenanceResolution:
    return TargetProvenanceResolution(
        status=TargetProvenanceStatus.UNRESOLVED,
        provenance=None,
        reason=reason,
    )
