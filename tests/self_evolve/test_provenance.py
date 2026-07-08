from __future__ import annotations

import json

from aworld.self_evolve.provenance import TargetProvenance
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
