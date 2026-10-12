"""Cross-repository checks for the scorer shipped in the Runtime image."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from parsebench_dataset import scoring


@pytest.fixture
def runtime_scorer() -> Path:
    configured = os.environ.get("PARSEBENCH_RUNTIME_SOURCE")
    runtime = (
        Path(configured).expanduser().resolve()
        if configured
        else Path(__file__).resolve().parents[4] / "runtime"
    )
    scorer = runtime / "third-party/parsebench-scorer"
    if not scorer.is_dir():
        if configured:
            pytest.fail(f"Configured Runtime has no ParseBench scorer: {scorer}")
        pytest.skip("Set PARSEBENCH_RUNTIME_SOURCE to verify the Runtime scorer bundle")
    return scorer


def test_dataset_accepts_the_runtime_scorer_bundle(runtime_scorer: Path) -> None:
    # Exercise the actual verifier check; a Dockerfile-only import check did not
    # catch the former mismatch between these two repositories' manifest pins.
    scoring._validate_vendored_scorer_bundle(runtime_scorer, runtime_scorer / "src")


def test_dataset_rejects_the_obsolete_readme_attestation(
    runtime_scorer: Path, tmp_path: Path
) -> None:
    manifest = json.loads(
        (
            runtime_scorer / scoring.PARSEBENCH_SCORER_BUNDLE_MANIFEST_FILENAME
        ).read_bytes()
    )
    readme = next(entry for entry in manifest["files"] if entry["path"] == "README.md")
    readme["size"] = 16_558
    readme["sha256"] = (
        "091402d0789874d51d4da9ab3d9fb3635734bb6decae1158bbf8decb6a4975b1"
    )
    (tmp_path / scoring.PARSEBENCH_SCORER_BUNDLE_MANIFEST_FILENAME).write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

    with pytest.raises(
        scoring.OfficialScorerValidationError, match="manifest SHA256 mismatch"
    ):
        scoring._validate_vendored_scorer_bundle(tmp_path, tmp_path / "src")


def test_dataset_still_rejects_changed_scorer_source(
    runtime_scorer: Path, tmp_path: Path
) -> None:
    copied = tmp_path / "scorer"
    shutil.copytree(runtime_scorer, copied)
    source = copied / "src/parse_bench/__init__.py"
    content = source.read_bytes()
    source.write_bytes(bytes([content[0] ^ 1]) + content[1:])

    with pytest.raises(
        scoring.OfficialScorerValidationError, match="file SHA256 mismatch"
    ):
        scoring._validate_vendored_scorer_bundle(copied, copied / "src")
