from __future__ import annotations

from dataclasses import replace

import pytest

from aworld.self_evolve.candidate_package import (
    candidate_package_fingerprint,
    validate_candidate_files,
)
from aworld.self_evolve.types import (
    CandidateFileDelta,
    CandidateVariant,
    SelfEvolveTargetRef,
)


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
