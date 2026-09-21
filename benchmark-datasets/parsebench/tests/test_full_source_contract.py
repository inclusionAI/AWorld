"""Optional release check against all content-addressed ParseBench materials."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from parsebench_dataset.contracts import PINNED_FULL_SELECTION_MANIFEST_SHA256
from parsebench_dataset.dataset import (
    _selection_manifest_sha256,
    load_parsebench_checkout,
)


def test_complete_source_and_embedded_verifier_match_release_pin() -> None:
    source = os.environ.get("PARSEBENCH_SOURCE")
    if not source:
        pytest.skip("Set PARSEBENCH_SOURCE to verify the complete pinned release")

    dataset = load_parsebench_checkout(Path(source))

    assert (
        _selection_manifest_sha256(dataset, dataset.executions)
        == PINNED_FULL_SELECTION_MANIFEST_SHA256
    )
