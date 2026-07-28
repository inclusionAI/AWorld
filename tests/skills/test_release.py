from __future__ import annotations

from aworld.self_evolve.patch_intent import apply_skill_patch_intent
from aworld.skills.release import normalize_verified_skill_release
from aworld.skills.structure import build_skill_structural_edit_intent


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


def test_normalize_verified_skill_release_preserves_basic_auth_documentation() -> None:
    content = (
        "---\nname: demo\n---\n"
        "# Demo\n\n"
        "Use the credential command only with user-provided values.\n\n"
        "```bash\n"
        "agent-browser set credentials user pass   # HTTP basic auth\n"
        "```\n"
    )

    normalized, metrics = normalize_verified_skill_release(
        content,
        run_id="run-auth-docs",
        candidate_id="candidate-auth-docs",
    )

    assert metrics["normalization_equivalence_passed"] is True
    assert metrics["normalization_content_preservation_passed"] is True
    assert "# HTTP basic auth" in normalized
    assert metrics["removed_internal_line_count"] == 0


def test_normalize_verified_skill_release_preserves_auth_syntax_prose() -> None:
    content = (
        "---\nname: demo\n---\n"
        "# Demo\n\n"
        "Bearer syntax uses a scheme followed by a caller-provided value.\n"
        "Basic example values must never be copied from documentation.\n"
        "Basic format is base64(username:password).\n"
    )

    normalized, metrics = normalize_verified_skill_release(
        content,
        run_id="run-auth-syntax",
        candidate_id="candidate-auth-syntax",
    )

    assert metrics["normalization_equivalence_passed"] is True
    assert metrics["normalization_content_preservation_passed"] is True
    assert "Bearer syntax" in normalized
    assert "Basic example" in normalized
    assert "Basic format" in normalized
    assert metrics["removed_internal_line_count"] == 0


def test_normalize_verified_skill_release_rebinds_exact_patch_intent() -> None:
    original = (
        "---\nname: demo\n---\n"
        "# Demo\n\n"
        "## Debugging\n\n"
        "Inspect the session, preserve the trace, compare the response with "
        "the artifact, classify the failure, and record the bounded recovery "
        "action before retrying.\n"
    )
    patch_intent = {
        "operations": [
            {
                "op": "replace_section",
                "heading": "Debugging",
                "content": (
                    "Persist one bounded diagnostic artifact before retrying."
                ),
            }
        ]
    }
    candidate = apply_skill_patch_intent(original, patch_intent)
    structural_intent = build_skill_structural_edit_intent(
        original_content=original,
        candidate_content=candidate,
        patch_intent=patch_intent,
    )

    normalized, metrics = normalize_verified_skill_release(
        candidate,
        run_id="run-patch",
        candidate_id="candidate-patch",
        original_content=original,
        structural_edit_intent=structural_intent,
        require_exact_deletion_intent=True,
    )

    assert metrics["normalization_equivalence_passed"] is True
    assert metrics["structural_validation_passed"] is True
    assert "release_state: verified" in normalized
    assert "Persist one bounded diagnostic artifact" in normalized


def test_normalize_verified_skill_release_fails_closed_when_intent_rebind_fails() -> None:
    original = (
        "---\nname: demo\n---\n"
        "# Demo\n\n"
        "## Debugging\n\n"
        "Inspect the session, preserve the trace, compare the response with "
        "the artifact, classify the failure, and record the bounded recovery "
        "action before retrying.\n"
    )
    patch_intent = {
        "operations": [
            {
                "op": "replace_section",
                "heading": "Debugging",
                "content": (
                    "Preserve the browser artifact before retrying.\n\n"
                    "Apply the runtime gate only after reviewing "
                    "user-visible evidence."
                ),
            }
        ]
    }
    candidate = apply_skill_patch_intent(original, patch_intent)
    structural_intent = build_skill_structural_edit_intent(
        original_content=original,
        candidate_content=candidate,
        patch_intent=patch_intent,
    )

    normalized, metrics = normalize_verified_skill_release(
        candidate,
        run_id="run-rebind-failure",
        candidate_id="candidate-rebind-failure",
        original_content=original,
        structural_edit_intent=structural_intent,
        require_exact_deletion_intent=True,
    )

    assert "runtime gate" not in normalized
    assert metrics["normalization_content_preservation_passed"] is True
    assert metrics["normalization_structural_intent_rebind_passed"] is False
    assert metrics["normalization_equivalence_passed"] is False
    assert metrics["structural_validation_passed"] is False
    assert metrics["structural_failure_code"] == (
        "skill_structural_edit_intent_rebind_failed"
    )
    assert metrics["failure_class"] == "framework"
    assert metrics["failure_owner"] == "framework"
    assert metrics["repairable"] is False
