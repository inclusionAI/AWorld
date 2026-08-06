from __future__ import annotations

import json
from pathlib import Path

import pytest

from aworld.self_evolve.datasets import (
    SelfEvolveEvalSourceConfig,
    build_dataset_from_source,
)
from aworld.self_evolve.regression import (
    RegressionEvidence,
    RegressionSuiteResult,
    dataset_case_fingerprints,
    resolve_regression_suites,
    resolve_target_contract_regression_suite,
)
from aworld.self_evolve.types import EvaluationSummary, GateResult


def _write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )


def test_resolve_regression_suites_binds_version_and_disjoint_data(
    tmp_path: Path,
) -> None:
    selection_path = tmp_path / "selection.jsonl"
    regression_path = tmp_path / "regression.jsonl"
    _write_jsonl(selection_path, [{"id": "selection", "input": "build a page"}])
    _write_jsonl(regression_path, [{"id": "regression", "input": "audit a page"}])
    selection = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="jsonl", path=str(selection_path))
    )

    suites = resolve_regression_suites(
        ("jsonl:regression.jsonl",),
        selection_dataset=selection,
        base_dir=tmp_path,
    )

    assert len(suites) == 1
    assert suites[0].spec.source_kind == "jsonl"
    assert suites[0].spec.source_version.startswith("sha256:")
    assert suites[0].spec.dataset_fingerprint.startswith("sha256:")
    assert suites[0].spec.case_fingerprints


def test_resolve_regression_suites_rejects_selection_data_overlap(
    tmp_path: Path,
) -> None:
    selection_path = tmp_path / "selection.jsonl"
    overlap_path = tmp_path / "overlap.jsonl"
    record = {"id": "same-task", "input": "build a page"}
    _write_jsonl(selection_path, [record])
    _write_jsonl(overlap_path, [record])
    selection = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="jsonl", path=str(selection_path))
    )

    with pytest.raises(ValueError, match="overlaps candidate-selection data"):
        resolve_regression_suites(
            (str(overlap_path),),
            selection_dataset=selection,
        )


def test_resolve_regression_suites_rejects_symlinked_source(
    tmp_path: Path,
) -> None:
    selection_path = tmp_path / "selection.jsonl"
    regression_path = tmp_path / "regression.jsonl"
    symlink_path = tmp_path / "regression-link.jsonl"
    _write_jsonl(selection_path, [{"id": "selection", "input": "selection"}])
    _write_jsonl(regression_path, [{"id": "regression", "input": "regression"}])
    symlink_path.symlink_to(regression_path)
    selection = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="jsonl", path=str(selection_path))
    )

    with pytest.raises(ValueError, match="regular file"):
        resolve_regression_suites(
            (str(symlink_path),),
            selection_dataset=selection,
        )


@pytest.mark.parametrize("reserved_kind", ("challenger", "target_contract"))
def test_external_regression_config_cannot_claim_framework_owned_source(
    tmp_path: Path,
    reserved_kind: str,
) -> None:
    selection_path = tmp_path / "selection.jsonl"
    _write_jsonl(selection_path, [{"id": "selection", "input": "selection"}])
    selection = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="jsonl", path=str(selection_path))
    )

    with pytest.raises(ValueError, match="unsupported regression benchmark kind"):
        resolve_regression_suites(
            (f"{reserved_kind}:untrusted.jsonl",),
            selection_dataset=selection,
            base_dir=tmp_path,
        )


def test_regression_evidence_requires_fresh_disjoint_execution(
    tmp_path: Path,
) -> None:
    selection_path = tmp_path / "selection.jsonl"
    regression_path = tmp_path / "regression.jsonl"
    _write_jsonl(selection_path, [{"id": "selection", "input": "selection"}])
    _write_jsonl(regression_path, [{"id": "regression", "input": "regression"}])
    selection = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="jsonl", path=str(selection_path))
    )
    suite = resolve_regression_suites(
        (str(regression_path),),
        selection_dataset=selection,
    )[0]
    result = RegressionSuiteResult(
        spec=suite.spec,
        baseline_summary=EvaluationSummary(
            variant_id="baseline", metrics={"score": 0.8}
        ),
        candidate_summary=EvaluationSummary(
            variant_id="candidate", metrics={"score": 0.8}
        ),
        gate_results=(
            GateResult(
                gate_name="score_improvement",
                passed=True,
                reason="candidate did not regress",
            ),
        ),
        execution_id="regression-execution",
        duration_ms=10,
    )
    evidence = RegressionEvidence(
        candidate_id="candidate",
        selection_dataset_fingerprint="sha256:selection",
        selection_case_fingerprints=("sha256:selection-case",),
        selection_backend_id="selection.Backend",
        regression_backend_id="regression.Backend",
        suite_results=(result,),
        evidence_id="evidence-1",
    )

    assert evidence.data_independent is True
    assert evidence.execution_independent is True
    assert evidence.implementation_independent is True
    assert evidence.passed is True
    assert evidence.to_dict()["fingerprint"] == evidence.fingerprint


def test_target_contract_regression_is_derived_from_immutable_skill_sections(
    tmp_path: Path,
) -> None:
    selection_path = tmp_path / "selection.jsonl"
    _write_jsonl(selection_path, [{"id": "selection", "input": "fix a browser task"}])
    selection = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="jsonl", path=str(selection_path))
    )
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(
        "---\nname: demo\n---\n# Demo\n\n## Quick start\n\nDo A.\n\n"
        "## Recovery\n\nDo B.\n\n## References\n\nIgnored.\n",
        encoding="utf-8",
    )

    suites = resolve_target_contract_regression_suite(
        target_type="skill",
        target_id="demo",
        target_path=skill_path,
        current_content=skill_path.read_text(encoding="utf-8"),
        target_fingerprint="sha256:baseline",
        selection_dataset=selection,
    )

    assert len(suites) == 1
    assert suites[0].spec.source_kind == "target_contract"
    assert len(suites[0].dataset.cases) == 2
    assert all(
        "Without using external resources" in case.input["content"]
        for case in suites[0].dataset.cases
    )
    assert [
        case.expected_output["baseline_contract"]
        for case in suites[0].dataset.cases
    ] == ["Do A.", "Do B."]
    assert all(
        case.expected_output["baseline_contract_fingerprint"].startswith(
            "sha256:"
        )
        for case in suites[0].dataset.cases
    )
    assert set(suites[0].spec.case_fingerprints).isdisjoint(
        set(dataset_case_fingerprints(selection))
    )


def test_target_contract_regression_fails_closed_without_behavior_sections(
    tmp_path: Path,
) -> None:
    selection_path = tmp_path / "selection.jsonl"
    _write_jsonl(selection_path, [{"id": "selection", "input": "task"}])
    selection = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="jsonl", path=str(selection_path))
    )

    assert resolve_target_contract_regression_suite(
        target_type="skill",
        target_id="demo",
        target_path=tmp_path / "SKILL.md",
        current_content="---\nname: demo\n---\n# Demo\n",
        target_fingerprint="sha256:baseline",
        selection_dataset=selection,
    ) == ()
