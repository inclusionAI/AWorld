from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from aworld.self_evolve.types import SelfEvolveTargetRef


@dataclass(frozen=True)
class TargetProvenance:
    target: SelfEvolveTargetRef
    source_kind: str
    write_origin: str
    trust_level: str
    protected: bool
    reason: str


TargetSelectionOrigin = Literal["inventory", "operator_explicit", "inferred"]
TargetProvenanceStatus = Literal["resolved", "unresolved"]


@dataclass(frozen=True)
class TargetProvenanceResolution:
    """Total result of classifying authorization metadata for one target."""

    status: TargetProvenanceStatus
    provenance: TargetProvenance | None
    reason: str

    @property
    def resolved(self) -> bool:
        return self.status == "resolved" and self.provenance is not None


def resolve_target_provenance(
    target: SelfEvolveTargetRef,
    *,
    selection_origin: TargetSelectionOrigin,
    inventory_provenance: TargetProvenance | None = None,
    workspace_root: str | Path | None = None,
) -> TargetProvenanceResolution:
    """Resolve target-level provenance without consulting trajectory evidence.

    Inventory metadata always takes precedence. Targets chosen explicitly by an
    operator are local selections, while inferred targets missing from inventory
    are generated and therefore require an explicit trust policy to mutate.
    """

    if not target.target_type or not target.target_id:
        return TargetProvenanceResolution(
            status="unresolved",
            provenance=None,
            reason="target identity is incomplete",
        )

    if inventory_provenance is not None:
        inventory_target = inventory_provenance.target
        if (
            inventory_target.target_type != target.target_type
            or inventory_target.target_id != target.target_id
        ):
            return TargetProvenanceResolution(
                status="unresolved",
                provenance=None,
                reason="inventory provenance does not match selected target identity",
            )
        if not _target_paths_match(
            inventory_target,
            target,
            workspace_root=workspace_root,
        ):
            return TargetProvenanceResolution(
                status="unresolved",
                provenance=None,
                reason="inventory provenance path does not match selected target path",
            )
        return TargetProvenanceResolution(
            status="resolved",
            provenance=inventory_provenance,
            reason="selected target uses inventory provenance",
        )

    if selection_origin == "operator_explicit":
        if not _target_path_is_local(target, workspace_root=workspace_root):
            return TargetProvenanceResolution(
                status="unresolved",
                provenance=None,
                reason="explicit target locality could not be established",
            )
        provenance = TargetProvenance(
            target=target,
            source_kind=target.target_type,
            write_origin="operator_selection",
            trust_level="local",
            protected=False,
            reason="target was explicitly selected by the operator",
        )
        return TargetProvenanceResolution(
            status="resolved",
            provenance=provenance,
            reason=provenance.reason,
        )

    if selection_origin == "inferred":
        provenance = TargetProvenance(
            target=target,
            source_kind=target.target_type,
            write_origin="target_inference",
            trust_level="generated",
            protected=False,
            reason="inferred target is absent from the capability inventory",
        )
        return TargetProvenanceResolution(
            status="resolved",
            provenance=provenance,
            reason=provenance.reason,
        )

    return TargetProvenanceResolution(
        status="unresolved",
        provenance=None,
        reason="inventory selection has no inventory provenance",
    )


def _target_path_is_local(
    target: SelfEvolveTargetRef,
    *,
    workspace_root: str | Path | None,
) -> bool:
    if target.path is None or workspace_root is None:
        return False
    root = Path(workspace_root).resolve()
    path = Path(target.path)
    if not path.is_absolute():
        path = root / path
    try:
        path.resolve().relative_to(root)
    except ValueError:
        return False
    return True


def _target_paths_match(
    inventory_target: SelfEvolveTargetRef,
    selected_target: SelfEvolveTargetRef,
    *,
    workspace_root: str | Path | None,
) -> bool:
    if inventory_target.path == selected_target.path:
        return True
    if (
        inventory_target.path is None
        or selected_target.path is None
        or workspace_root is None
    ):
        return False
    root = Path(workspace_root).resolve()

    def normalize(raw_path: str) -> Path:
        path = Path(raw_path)
        if not path.is_absolute():
            path = root / path
        return path.resolve()

    return normalize(inventory_target.path) == normalize(selected_target.path)
