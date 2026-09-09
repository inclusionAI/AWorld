from __future__ import annotations

from dataclasses import replace

import pytest

from aworld.self_evolve.candidate_package import (
    candidate_content_semantic_fingerprint,
    candidate_package_fingerprint,
    candidate_package_payload,
    candidate_semantic_package_fingerprint,
    validate_candidate_files,
)
from aworld.self_evolve.patch_intent import apply_skill_patch_intent
from aworld.self_evolve.types import (
    CandidateFileDelta,
    CandidateVariant,
    SelfEvolveTargetRef,
)
from aworld.skills.structure import build_skill_structural_edit_intent


TARGET = SelfEvolveTargetRef(
    target_type="skill",
    target_id="demo-skill",
    path="SKILL.md",
)
SKILL = "---\nname: demo-skill\n---\n# Demo\n"


def _candidate(*, files: tuple[CandidateFileDelta, ...] = ()) -> CandidateVariant:
    return CandidateVariant(
        candidate_id="candidate-1",
        target=TARGET,
        content=SKILL,
        rationale="test",
        files=files,
    )


def test_text_only_candidate_keeps_legacy_shape() -> None:
    assert _candidate().files == ()
    assert "structural_edit_intent" not in candidate_package_payload(
        _candidate()
    )


@pytest.mark.parametrize(
    "path",
    (
        "../escape.py",
        "/tmp/escape.py",
        "scripts/not-a-replay-file.py",
        "replay/../../escape.py",
    ),
)
def test_candidate_package_rejects_paths_outside_replay(path: str) -> None:
    with pytest.raises(ValueError, match="inside replay"):
        validate_candidate_files(
            (CandidateFileDelta(path=path, content="bad"),)
        )


def test_candidate_package_rejects_duplicate_paths() -> None:
    files = (
        CandidateFileDelta(path="replay/compiler.py", content="one"),
        CandidateFileDelta(path="replay/compiler.py", content="two"),
    )

    with pytest.raises(ValueError, match="duplicate"):
        validate_candidate_files(files)


def test_candidate_package_requires_upsert_content() -> None:
    with pytest.raises(ValueError, match="requires text content"):
        validate_candidate_files(
            (CandidateFileDelta(path="replay/compiler.py", content=None),)
        )


def test_candidate_package_fingerprint_includes_replay_files() -> None:
    first = _candidate(
        files=(
            CandidateFileDelta(
                path="replay/capability.json",
                content="{}",
            ),
        )
    )
    second = replace(
        first,
        files=(
            CandidateFileDelta(
                path="replay/capability.json",
                content='{"version": 1}',
            ),
        ),
    )

    assert candidate_package_fingerprint(first) != candidate_package_fingerprint(second)


def test_candidate_package_fingerprint_is_file_order_independent() -> None:
    first = _candidate(
        files=(
            CandidateFileDelta(path="replay/compiler.py", content="compiler"),
            CandidateFileDelta(path="replay/runtime.py", content="runtime"),
        )
    )
    second = replace(first, files=tuple(reversed(first.files)))

    assert candidate_package_fingerprint(first) == candidate_package_fingerprint(second)


def test_candidate_semantic_package_identity_covers_candidate_owned_files() -> None:
    first = _candidate(
        files=(
            CandidateFileDelta(path="replay/compiler.py", content="print('one')"),
            CandidateFileDelta(path="replay/runtime.py", content="print('shared')"),
        )
    )
    second = replace(
        first,
        files=(
            CandidateFileDelta(path="replay/compiler.py", content="print('two')"),
            CandidateFileDelta(path="replay/runtime.py", content="print('shared')"),
        ),
    )

    assert candidate_semantic_package_fingerprint(
        first
    ) != candidate_semantic_package_fingerprint(second)


def test_candidate_semantic_package_identity_covers_structural_authorization() -> None:
    original = (
        "---\nname: demo-skill\n---\n# Demo\n\n"
        "## Usage\n\nKeep the original workflow.\n"
    )
    patch_intent = {
        "operations": [
            {
                "op": "replace_section",
                "heading": "Usage",
                "content": "Use the verified bounded workflow.",
            }
        ]
    }
    content = apply_skill_patch_intent(original, patch_intent)
    intent = build_skill_structural_edit_intent(
        original_content=original,
        candidate_content=content,
        patch_intent=patch_intent,
    )
    untyped = replace(_candidate(), content=content)
    typed = replace(untyped, structural_edit_intent=intent)
    equivalent_typed = replace(
        typed,
        candidate_id="candidate-equivalent",
        rationale="equivalent authorization",
    )

    assert candidate_semantic_package_fingerprint(
        untyped
    ) != candidate_semantic_package_fingerprint(typed)
    assert candidate_semantic_package_fingerprint(
        typed
    ) == candidate_semantic_package_fingerprint(equivalent_typed)


def test_candidate_semantic_package_normalizes_target_content_not_file_code() -> None:
    first = _candidate(
        files=(CandidateFileDelta(path="replay/runtime.py", content="VALUE = 'A'"),)
    )
    equivalent_target = replace(
        first,
        content="---\nname: DEMO-SKILL\n---\n#   DEMO\n",
    )

    assert candidate_content_semantic_fingerprint(
        first.content
    ) == candidate_content_semantic_fingerprint(equivalent_target.content)
    assert candidate_semantic_package_fingerprint(
        first
    ) == candidate_semantic_package_fingerprint(equivalent_target)


def test_candidate_semantic_package_ignores_terminal_file_whitespace_only() -> None:
    first = _candidate(
        files=(
            CandidateFileDelta(
                path="replay/runtime.py",
                content="print('stable')\n",
            ),
        )
    )
    equivalent_file = replace(
        first,
        files=(
            CandidateFileDelta(
                path="replay/runtime.py",
                content="print('stable')\r\n\r\n",
            ),
        ),
    )

    assert candidate_package_fingerprint(first) != candidate_package_fingerprint(
        equivalent_file
    )
    assert candidate_semantic_package_fingerprint(
        first
    ) == candidate_semantic_package_fingerprint(equivalent_file)
