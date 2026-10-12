from __future__ import annotations

import json
from pathlib import Path

import pytest
from parsebench_dataset import dataset, verifier
from parsebench_dataset.contracts import PINNED_FULL_SELECTION_MANIFEST_SHA256
from parsebench_dataset.scoring import (
    ParseBenchCaseResult,
    ParseBenchDimension,
    ParseBenchVerifierResult,
)


def _full_scope(path: Path, reasons: tuple[str, ...]) -> Path:
    scope = dataset._PackageScope(
        kind="official-full",
        selection_manifest_sha256=PINNED_FULL_SELECTION_MANIFEST_SHA256,
        publishable=False,
        non_publishable_reasons=reasons,
        selected_execution_count=2_078,
        runtime_image="aworld-filex-parsebench:local",
    )
    path.write_bytes(dataset._public_scope_contract(scope))
    return path


@pytest.mark.parametrize(
    "reasons",
    [
        ("unapproved_verifier_runtime",),
        ("unpinned_selection_manifest",),
        ("unapproved_verifier_runtime", "mutable_runtime_image"),
        (
            "unpinned_selection_manifest",
            "unapproved_verifier_runtime",
            "mutable_runtime_image",
        ),
    ],
)
def test_verifier_accepts_nonpublishable_full_authoring_scope(
    tmp_path: Path, reasons: tuple[str, ...]
) -> None:
    scope_path = _full_scope(tmp_path / "parsebench-scope.json", reasons)

    loaded = verifier._load_scope(scope_path)

    assert loaded.kind == "official-full"
    assert loaded.publishable is False
    result = ParseBenchVerifierResult.from_case_results(
        task_id="full-local-case",
        case_results=[
            ParseBenchCaseResult.scored(
                case_id="full-local-case",
                dimension=ParseBenchDimension.CHART,
                score=1.0,
            )
        ],
        scope_kind=loaded.kind,
        selection_manifest_sha256=loaded.selection_manifest_sha256,
        scope_publishable=loaded.publishable,
    )
    outcome = verifier._commit_verifier_result(output=tmp_path, result=result)
    assert outcome.result_path.is_file()
    details = json.loads(outcome.result_path.read_bytes())
    assert details["benchmark_scope"]["publishable"] is False
    # Harbor may consume the task's diagnostic reward while the benchmark
    # scope remains explicitly ineligible for official publication.
    assert outcome.reward_path is not None
    assert json.loads(outcome.reward_path.read_bytes())["reward"] == 1.0


@pytest.mark.parametrize(
    "reason", ["unapproved_verifier_runtime", "unpinned_selection_manifest"]
)
def test_unapproved_full_scope_cannot_claim_publication(
    tmp_path: Path, reason: str
) -> None:
    scope_path = _full_scope(tmp_path / "parsebench-scope.json", (reason,))
    payload = json.loads(scope_path.read_bytes())
    payload["publishable"] = True
    payload["runtime_image"] = "registry.example/runtime@sha256:" + "4" * 64
    scope_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        verifier.ParseBenchVerificationError, match="not an immutable official release"
    ):
        verifier._load_scope(scope_path)


def test_verifier_rejects_unknown_nonpublishable_reason(tmp_path: Path) -> None:
    scope_path = _full_scope(tmp_path / "parsebench-scope.json", ("unknown_reason",))

    with pytest.raises(
        verifier.ParseBenchVerificationError, match="reasons are invalid"
    ):
        verifier._load_scope(scope_path)
