from __future__ import annotations

import shutil

import pytest

from aworld.self_evolve.replay_capability import fingerprint_skill_package
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.targets import (
    DraftSkillTextTarget,
    SkillTextTarget,
    WorkspaceArtifactTarget,
)
from aworld.self_evolve.types import CandidateFileDelta, CandidateVariant, SelfEvolveRun


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
    proposal_content = proposal_path.read_text(encoding="utf-8")
    assert "release_state: candidate" in proposal_content
    assert "candidate_id: cand-1" in proposal_content
    assert "\n---\nUpdated skill text." in proposal_content
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


def test_skill_text_target_applies_and_rolls_back_candidate_package(tmp_path) -> None:
    skill_path = _write_skill(tmp_path)
    replay_root = skill_path.parent / "replay"
    replay_root.mkdir()
    existing = replay_root / "existing.py"
    existing.write_text("old\n", encoding="utf-8")
    target = SkillTextTarget(skill_path, allow_auto_apply=True)
    candidate = CandidateVariant(
        candidate_id="cand-package",
        target=target.identity,
        content=target.load_current_content().replace("Original", "Updated"),
        rationale="add replay capability",
        files=(
            CandidateFileDelta(path="replay/existing.py", content="new\n"),
            CandidateFileDelta(
                path="replay/compiler.py",
                content="print('compile')\n",
                executable=True,
            ),
        ),
    )

    target.apply_candidate_variant(candidate)

    assert "Updated skill text." in skill_path.read_text(encoding="utf-8")
    assert existing.read_text(encoding="utf-8") == "new\n"
    assert (replay_root / "compiler.py").stat().st_mode & 0o111

    target.rollback()

    assert "Original skill text." in skill_path.read_text(encoding="utf-8")
    assert existing.read_text(encoding="utf-8") == "old\n"
    assert not (replay_root / "compiler.py").exists()


def test_skill_package_apply_rejects_symlinked_skill_markdown(tmp_path) -> None:
    external = tmp_path / "external.md"
    external.write_text("# External\n", encoding="utf-8")
    skill_root = tmp_path / "aworld-skills" / "demo"
    skill_root.mkdir(parents=True)
    skill_path = skill_root / "SKILL.md"
    skill_path.symlink_to(external)
    target = SkillTextTarget(
        skill_path,
        target_id="demo",
        allow_auto_apply=True,
    )
    candidate = CandidateVariant(
        candidate_id="cand-symlink",
        target=target.identity,
        content="# Candidate\n",
        rationale="test symlink guard",
    )

    with pytest.raises(ValueError, match="symlink"):
        target.apply_candidate_variant(candidate)

    assert external.read_text(encoding="utf-8") == "# External\n"


def test_skill_package_apply_rejects_changes_after_replay_verification(tmp_path) -> None:
    skill_path = _write_skill(tmp_path)
    helper = skill_path.parent / "helper.py"
    helper.write_text("VERSION = 1\n", encoding="utf-8")
    candidate_content = skill_path.read_text(encoding="utf-8").replace(
        "Original",
        "Updated",
    )
    expected_root = tmp_path / "expected-package"
    shutil.copytree(skill_path.parent, expected_root)
    (expected_root / "SKILL.md").write_text(candidate_content, encoding="utf-8")
    expected_fingerprint = fingerprint_skill_package(expected_root)
    helper.write_text("VERSION = 2\n", encoding="utf-8")
    target = SkillTextTarget(skill_path, allow_auto_apply=True)
    candidate = CandidateVariant(
        candidate_id="cand-stale",
        target=target.identity,
        content=candidate_content,
        rationale="verify package freshness",
    )

    with pytest.raises(ValueError, match="changed after replay verification"):
        target.apply_candidate_variant(
            candidate,
            expected_package_fingerprint=expected_fingerprint,
        )

    assert "Original skill text." in skill_path.read_text(encoding="utf-8")
    assert helper.read_text(encoding="utf-8") == "VERSION = 2\n"


def test_new_skill_package_rollback_atomically_retires_published_root(tmp_path) -> None:
    release_path = tmp_path / "skills" / "new-skill" / "SKILL.md"
    target = DraftSkillTextTarget(
        tmp_path / "drafts" / "new-skill" / "SKILL.md",
        target_id="new-skill",
        release_path=release_path,
        allow_auto_apply=True,
    )
    candidate = CandidateVariant(
        candidate_id="cand-new",
        target=target.identity,
        content="# New skill\n",
        rationale="publish new package",
        files=(CandidateFileDelta(path="replay/compiler.py", content="# compiler\n"),),
    )

    target.apply_candidate_variant(candidate)
    assert release_path.is_file()

    target.rollback()

    assert not release_path.parent.exists()
    assert not tuple((tmp_path / "skills").glob(".new-skill.aworld-trash-*"))


def test_workspace_artifact_target_rejects_protected_product_paths(tmp_path) -> None:
    protected_path = tmp_path / "aworld" / "config" / "conf.py"
    protected_path.parent.mkdir(parents=True)
    protected_path.write_text("PRODUCT = True\n", encoding="utf-8")

    with pytest.raises(ValueError, match="protected product path"):
        WorkspaceArtifactTarget(protected_path, workspace_root=tmp_path)


def test_workspace_artifact_target_supports_isolated_generated_artifact_proposals(tmp_path) -> None:
    artifact_path = tmp_path / "generated" / "report.md"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text("old report\n", encoding="utf-8")
    target = WorkspaceArtifactTarget(artifact_path, workspace_root=tmp_path)
    store = FilesystemSelfEvolveStore(tmp_path)
    store.create_run(SelfEvolveRun(run_id="run-artifact", target=target.identity))
    candidate = CandidateVariant(
        candidate_id="cand-artifact",
        target=target.identity,
        content="new report\n",
        rationale="agent-generated workspace artifact",
    )

    proposal_path, diff_path = target.preserve_proposal(store, "run-artifact", candidate)

    assert target.identity.target_type == "workspace-artifact"
    assert target.load_current_content() == "old report\n"
    assert target.fingerprint_current_content().startswith("sha256:")
    assert artifact_path.read_text(encoding="utf-8") == "old report\n"
    assert proposal_path.read_text(encoding="utf-8") == "new report\n"
    assert "+new report" in diff_path.read_text(encoding="utf-8")
