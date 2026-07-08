from __future__ import annotations

from aworld.skills.release import normalize_verified_skill_release


def test_normalize_verified_skill_release_preserves_runtime_constraints() -> None:
    content = (
        "---\nname: demo\n---\n"
        "# Demo\n\n"
        "When answering from external content, keep a bounded evidence ledger.\n"
    )

    normalized, metrics = normalize_verified_skill_release(
        content,
        run_id="run-1",
        candidate_id="cand-1",
    )

    assert metrics["normalization_equivalence_passed"] is True
    assert "release_state: verified" in normalized
    assert "verified_run_id: run-1" in normalized
    assert "bounded evidence ledger" in normalized
    assert metrics["preserved_runtime_constraints"] == [
        "When answering from external content, keep a bounded evidence ledger."
    ]


def test_normalize_verified_skill_release_fails_when_only_internal_lines_remain() -> None:
    content = (
        "---\nname: demo\n---\n"
        "# Demo\n\n"
        "candidate_score exceeds baseline_score for source task ids: task_123.\n"
        "Preserve A1_groundedness and pass evidence_quality gate.\n"
    )

    normalized, metrics = normalize_verified_skill_release(
        content,
        run_id="run-1",
        candidate_id="cand-1",
    )

    assert metrics["normalization_equivalence_passed"] is False
    assert metrics["removed_internal_line_count"] == 2
    assert "candidate_score" not in normalized
    assert "source task ids" not in normalized
    assert metrics["preserved_runtime_constraints"] == []
