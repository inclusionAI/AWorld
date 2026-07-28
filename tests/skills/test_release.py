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


def test_normalize_verified_skill_release_rechecks_original_structure() -> None:
    original = (
        "---\nname: demo\n---\n# Demo\n\n"
        "## Usage\n\nPreserve the normal workflow and its result artifact.\n\n"
        "## Debugging\n\n"
        "Inspect the session, save a protocol trace, compare the final response, "
        "and record the bounded recovery action before retrying.\n\n"
        "```console\nagent-browser inspect --session active\n```\n\n"
        "## Safety\n\nNever expose credentials.\n"
    )
    truncated = (
        "---\nname: demo\n---\n# Demo\n\n"
        "## Usage\n\nPreserve the normal workflow and its result artifact.\n\n"
        "## Debugging\n\nShort.\n\n"
        "## Safety\n\nNever expose credentials.\n"
    )

    _, metrics = normalize_verified_skill_release(
        truncated,
        run_id="run-1",
        candidate_id="cand-1",
        original_content=original,
    )

    assert metrics["normalization_equivalence_passed"] is False
    assert metrics["structural_validation_passed"] is False
    assert metrics["structural_failure_code"] == (
        "skill_section_content_truncated"
    )


def test_normalize_verified_skill_release_rejects_unclosed_fence_after_normalization() -> None:
    original = "---\nname: demo\n---\n# Demo\n\nExisting guidance.\n"
    candidate = (
        "---\nname: demo\n---\n# Demo\n\n"
        "```bash\nagent-browser open https://example.test\n"
    )

    _, metrics = normalize_verified_skill_release(
        candidate,
        run_id="run-1",
        candidate_id="cand-1",
        original_content=original,
    )

    assert metrics["normalization_equivalence_passed"] is False
    assert metrics["structural_failure_code"] == "skill_code_fence_unclosed"
