from __future__ import annotations

import json

import pytest

from aworld.self_evolve.provenance import (
    TargetProvenance,
    resolve_target_provenance,
)
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.types import SelfEvolveRun, SelfEvolveTargetRef


def test_target_provenance_is_persisted_as_sidecar_without_mutating_target_file(tmp_path) -> None:
    skill_path = tmp_path / "aworld-skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n\nOriginal skill text.\n", encoding="utf-8")
    original_content = skill_path.read_text(encoding="utf-8")

    target = SelfEvolveTargetRef(target_type="skill", target_id="demo", path=str(skill_path))
    provenance = TargetProvenance(
        target=target,
        source_kind="skill",
        write_origin="repository",
        trust_level="project",
        protected=False,
        reason="skill target loaded from workspace",
    )
    store = FilesystemSelfEvolveStore(workspace_root=tmp_path)
    store.create_run(SelfEvolveRun(run_id="run-prov", target=target))

    provenance_path = store.write_target_provenance("run-prov", provenance)

    assert skill_path.read_text(encoding="utf-8") == original_content
    saved = json.loads(provenance_path.read_text(encoding="utf-8"))
    assert saved["target"]["target_id"] == "demo"
    assert saved["schema_version"] == 1
    assert saved["write_origin"] == "repository"
    assert saved["protected"] is False


def test_protected_provenance_records_reason_without_touching_target_metadata(tmp_path) -> None:
    app_evaluator_path = tmp_path / "aworld-skills" / "app_evaluator" / "SKILL.md"
    app_evaluator_path.parent.mkdir(parents=True)
    app_evaluator_path.write_text("---\nname: app_evaluator\n---\n", encoding="utf-8")

    target = SelfEvolveTargetRef(
        target_type="skill",
        target_id="app_evaluator",
        path=str(app_evaluator_path),
    )
    provenance = TargetProvenance(
        target=target,
        source_kind="skill",
        write_origin="repository",
        trust_level="protected",
        protected=True,
        reason="app_evaluator is read-only for self-evolve",
    )
    store = FilesystemSelfEvolveStore(workspace_root=tmp_path)
    store.create_run(SelfEvolveRun(run_id="run-protected", target=target))

    provenance_path = store.write_target_provenance("run-protected", provenance)

    assert "self_evolve" not in app_evaluator_path.read_text(encoding="utf-8")
    saved = json.loads(provenance_path.read_text(encoding="utf-8"))
    assert saved["protected"] is True
    assert saved["reason"] == "app_evaluator is read-only for self-evolve"


@pytest.mark.parametrize("trajectory_count", [1, 3])
def test_provenance_resolution_is_target_level_not_trajectory_level(
    trajectory_count: int,
) -> None:
    target = SelfEvolveTargetRef(
        target_type="skill",
        target_id="demo",
        path="/workspace/aworld-skills/demo/SKILL.md",
    )
    inventory_provenance = TargetProvenance(
        target=target,
        source_kind="skill",
        write_origin="installed_skill",
        trust_level="local",
        protected=False,
        reason="installed local skill",
    )

    resolutions = tuple(
        resolve_target_provenance(
            target,
            selection_origin="inferred",
            inventory_provenance=inventory_provenance,
            workspace_root="/workspace",
        )
        for _ in range(trajectory_count)
    )

    assert all(resolution.status == "resolved" for resolution in resolutions)
    assert all(
        resolution.provenance is inventory_provenance for resolution in resolutions
    )
    assert len({resolution.provenance for resolution in resolutions}) == 1


def test_provenance_resolution_classifies_explicit_and_inferred_targets() -> None:
    target = SelfEvolveTargetRef(
        target_type="skill",
        target_id="draft",
        path="/workspace/.aworld/self_evolve/drafts/skills/draft/SKILL.md",
    )

    explicit = resolve_target_provenance(
        target,
        selection_origin="operator_explicit",
        workspace_root="/workspace",
    )
    inferred = resolve_target_provenance(
        target,
        selection_origin="inferred",
    )

    assert explicit.status == "resolved"
    assert explicit.provenance is not None
    assert explicit.provenance.write_origin == "operator_selection"
    assert explicit.provenance.trust_level == "local"
    assert inferred.status == "resolved"
    assert inferred.provenance is not None
    assert inferred.provenance.write_origin == "target_inference"
    assert inferred.provenance.trust_level == "generated"


def test_explicit_target_outside_workspace_is_unresolved_without_inventory() -> None:
    resolution = resolve_target_provenance(
        SelfEvolveTargetRef(
            target_type="skill",
            target_id="external-capability",
            path="/external/skills/external-capability/SKILL.md",
        ),
        selection_origin="operator_explicit",
        workspace_root="/workspace",
    )

    assert resolution.status == "unresolved"
    assert resolution.provenance is None
    assert resolution.reason == "explicit target locality could not be established"


def test_provenance_resolution_returns_structured_unresolved_result() -> None:
    resolution = resolve_target_provenance(
        SelfEvolveTargetRef(target_type="", target_id=""),
        selection_origin="inferred",
    )

    assert resolution.status == "unresolved"
    assert resolution.provenance is None
    assert resolution.reason == "target identity is incomplete"


@pytest.mark.parametrize("link_kind", ["directory", "file"])
def test_explicit_target_locality_rejects_symlinked_paths(
    tmp_path,
    link_kind: str,
) -> None:
    real_dir = tmp_path / "real-capability"
    real_dir.mkdir()
    real_file = real_dir / "SKILL.md"
    real_file.write_text("---\nname: capability\n---\n", encoding="utf-8")
    exposed_dir = tmp_path / "skills" / "capability"
    exposed_dir.parent.mkdir()
    if link_kind == "directory":
        exposed_dir.symlink_to(real_dir, target_is_directory=True)
        selected_path = exposed_dir / "SKILL.md"
    else:
        exposed_dir.mkdir()
        selected_path = exposed_dir / "SKILL.md"
        selected_path.symlink_to(real_file)

    resolution = resolve_target_provenance(
        SelfEvolveTargetRef(
            target_type="skill",
            target_id="capability",
            path=str(selected_path),
        ),
        selection_origin="operator_explicit",
        workspace_root=tmp_path,
    )

    assert resolution.status == "unresolved"
    assert resolution.provenance is None
    assert resolution.reason == "explicit target locality could not be established"


def test_inventory_path_match_does_not_trust_equal_raw_symlink_paths(tmp_path) -> None:
    real_dir = tmp_path / "real-capability"
    real_dir.mkdir()
    real_file = real_dir / "SKILL.md"
    real_file.write_text("---\nname: capability\n---\n", encoding="utf-8")
    linked_dir = tmp_path / "skills" / "capability"
    linked_dir.parent.mkdir()
    linked_dir.symlink_to(real_dir, target_is_directory=True)
    linked_path = linked_dir / "SKILL.md"
    target = SelfEvolveTargetRef("skill", "capability", str(linked_path))
    inventory_provenance = TargetProvenance(
        target=target,
        source_kind="skill",
        write_origin="installed_skill",
        trust_level="local",
        protected=False,
        reason="inventory record",
    )

    resolution = resolve_target_provenance(
        target,
        selection_origin="inventory",
        inventory_provenance=inventory_provenance,
        workspace_root=tmp_path,
    )

    assert resolution.status == "unresolved"
    assert resolution.provenance is None
    assert resolution.reason == "inventory provenance path does not match selected target path"
