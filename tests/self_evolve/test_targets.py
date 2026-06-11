from __future__ import annotations

import pytest

from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.targets import SkillTextTarget, WorkspaceArtifactTarget
from aworld.self_evolve.types import CandidateVariant, SelfEvolveRun


def _write_skill(tmp_path, name: str = "demo", body: str = "Original skill text.\n"):
    skill_path = tmp_path / "aworld-skills" / name / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(f"---\nname: {name}\n---\n\n{body}", encoding="utf-8")
    return skill_path


def test_skill_text_target_loads_identity_content_and_fingerprint(tmp_path) -> None:
    skill_path = _write_skill(tmp_path)
    target = SkillTextTarget(skill_path)

    assert target.identity.target_type == "skill"
    assert target.identity.target_id == "demo"
    assert target.identity.path == str(skill_path)
    assert target.load_current_content().endswith("Original skill text.\n")
    assert target.fingerprint_current_content().startswith("sha256:")


def test_skill_text_target_renders_candidate_diff(tmp_path) -> None:
    skill_path = _write_skill(tmp_path)
    target = SkillTextTarget(skill_path)

    diff = target.render_candidate_diff("---\nname: demo\n---\n\nUpdated skill text.\n")

    assert "--- current/demo/SKILL.md" in diff
    assert "+++ candidate/demo/SKILL.md" in diff
    assert "-Original skill text." in diff
    assert "+Updated skill text." in diff


def test_skill_text_target_preserves_proposal_without_mutating_source(tmp_path) -> None:
    skill_path = _write_skill(tmp_path)
    target = SkillTextTarget(skill_path)
    original_content = skill_path.read_text(encoding="utf-8")
    store = FilesystemSelfEvolveStore(tmp_path)
    store.create_run(SelfEvolveRun(run_id="run-target", target=target.identity))
    candidate = CandidateVariant(
        candidate_id="cand-1",
        target=target.identity,
        content=original_content.replace("Original", "Updated"),
        rationale="clarify wording",
    )

    proposal_path, diff_path = target.preserve_proposal(store, "run-target", candidate)

    assert skill_path.read_text(encoding="utf-8") == original_content
    assert proposal_path.read_text(encoding="utf-8") == candidate.content
    assert "+Updated skill text." in diff_path.read_text(encoding="utf-8")


def test_skill_text_target_apply_requires_allowlist_and_supports_rollback(tmp_path) -> None:
    skill_path = _write_skill(tmp_path)
    target = SkillTextTarget(skill_path)
    original_content = target.load_current_content()

    with pytest.raises(PermissionError, match="not allowlisted"):
        target.apply_candidate(original_content.replace("Original", "Updated"))

    allowlisted = SkillTextTarget(skill_path, allow_auto_apply=True)
    allowlisted.apply_candidate(original_content.replace("Original", "Updated"))
    assert "Updated skill text." in skill_path.read_text(encoding="utf-8")

    allowlisted.rollback()
    assert skill_path.read_text(encoding="utf-8") == original_content


def test_workspace_artifact_target_rejects_protected_product_paths(tmp_path) -> None:
    protected_path = tmp_path / "aworld" / "config" / "conf.py"
    protected_path.parent.mkdir(parents=True)
    protected_path.write_text("PRODUCT = True\n", encoding="utf-8")

    with pytest.raises(ValueError, match="protected product path"):
        WorkspaceArtifactTarget(protected_path, workspace_root=tmp_path)
