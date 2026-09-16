"""Public artifact acceptance and real, embedded official-scorer integration."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from parsebench_dataset import verifier
from parsebench_dataset.contracts import (
    DATASET_REVISION,
    GROUND_TRUTH_SCHEMA_VERSION,
    SCORER_REVISION,
    ParseBenchDimension,
)
from parsebench_dataset.dataset import _verifier_runtime_modules


def _digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _fixture(tmp_path: Path, *, page: int | None = None, layout_task: bool = False):
    source = tmp_path / "workspace/input/document.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"%PDF public benchmark source\n")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    markdown_path = artifacts / "document.md"
    markdown_path.write_bytes(b"# public\r\n\r\nraw text  \n")
    layout_path = artifacts / "layout.json"
    payload = {
        "layout_pages": [
            {
                "page_number": page or 1,
                "width": 100,
                "height": 100,
                "md": "# public",
                "items": [
                    {
                        "type": "text",
                        "md": "public",
                        "value": "public",
                        "bbox": {"x": 10, "y": 20, "w": 20, "h": 20, "label": "Text"},
                    }
                ],
            }
        ],
    }
    layout_path.write_text(json.dumps(payload))
    dimension = (
        ParseBenchDimension.LAYOUT if layout_task else ParseBenchDimension.TEXT_CONTENT
    )
    ground_truth = verifier._GroundTruth(
        task_id="public-task",
        source=verifier._GroundTruthSource(
            logical_path="docs/document.pdf",
            runtime_path=Path("/workspace/input/document.pdf"),
            size=source.stat().st_size,
            sha256=_digest(source.read_bytes()),
            page=page,
        ),
        dimensions=(dimension,),
        rules=(
            verifier._GroundTruthRule(
                rule_id="public-rule",
                dimension=dimension,
                rule_type="layout" if layout_task else "missing_word_percent",
                rule={
                    "bbox": [0.1, 0.2, 0.2, 0.2],
                    "canonical_class": "Text",
                    "content": {"type": "text", "text": "public"},
                }
                if layout_task
                else {"bag_of_word": {"public": 1}},
                page=page,
                expected_markdown=None,
                tags=(),
                source_jsonl=f"{dimension.value}.jsonl",
                source_line=1,
            ),
        ),
    )
    arguments = {
        "ground_truth": ground_truth,
        "result_path": artifacts / "result.json",
        "markdown_path": markdown_path,
        "layout_path": layout_path,
        "workspace_root": tmp_path / "workspace",
    }
    return arguments, payload


def test_public_artifacts_need_no_result_producer_model_or_vlm_evidence(
    tmp_path: Path,
) -> None:
    arguments, _ = _fixture(tmp_path)
    snapshot = verifier._snapshot_artifacts(**arguments)
    assert snapshot.layout["markdown"] == "# public\r\n\r\nraw text  \n"
    assert snapshot.layout["pipeline_name"] == "parsebench-submission"
    assert snapshot.result_sha256 is None
    assert snapshot.layout["layout_pages"][0]["items"][0]["layout_segments"][0] == {
        "x": 10.0,
        "y": 20.0,
        "w": 20.0,
        "h": 20.0,
        "label": "text",
        "confidence": 1.0,
    }
    # An unrelated producer's metadata cannot impose a provider or model gate.
    arguments["result_path"].write_text(
        '{"filex":{"metrics":{"model":{"call_count":0}}}}'
    )
    assert verifier._snapshot_artifacts(**arguments) == snapshot


def test_public_pipeline_metadata_cannot_change_scorer_routing(tmp_path: Path) -> None:
    arguments, payload = _fixture(tmp_path)
    payload.update(
        pipeline_name="arbitrary-provider",
        example_id="other-task",
        markdown="different",
    )
    arguments["layout_path"].write_text(json.dumps(payload))
    snapshot = verifier._snapshot_artifacts(**arguments)
    assert snapshot.layout["pipeline_name"] == "parsebench-submission"
    assert snapshot.layout["example_id"] == "public-task"
    assert (
        snapshot.layout["markdown"] == arguments["markdown_path"].read_bytes().decode()
    )


def test_empty_predictions_are_scored_not_producer_execution_failures(
    tmp_path: Path,
) -> None:
    arguments, _ = _fixture(tmp_path)
    arguments["markdown_path"].write_bytes(b"")
    arguments["layout_path"].write_text('{"layout_pages":[]}')
    assert verifier._snapshot_artifacts(**arguments).layout["markdown"] == ""


def test_selected_page_identity_is_preserved_for_official_scorer(
    tmp_path: Path,
) -> None:
    arguments, _ = _fixture(tmp_path, page=3, layout_task=True)
    snapshot = verifier._snapshot_artifacts(**arguments)
    assert snapshot.layout["pages"] == [
        {"page_index": 2, "markdown": "# public\r\n\r\nraw text  \n"}
    ]
    assert [page["page_number"] for page in snapshot.layout["layout_pages"]] == [
        1,
        2,
        3,
    ]
    assert snapshot.layout["layout_pages"][0]["items"] == []
    assert len(snapshot.layout["layout_pages"][2]["items"]) == 1
    outputs, cases, _ = verifier._write_official_inputs(
        root=tmp_path / "official",
        ground_truth=arguments["ground_truth"],
        dimension=ParseBenchDimension.LAYOUT,
        snapshot=snapshot,
    )
    inference = json.loads(next(outputs.glob("*.result.json")).read_bytes())
    assert inference["request"]["source_file_path"] == "docs/document.pdf"
    assert "result_sha256" not in inference["raw_output"]
    assert inference["output"]["markdown"] == snapshot.layout["markdown"]
    official_rule = json.loads((cases / "layout.jsonl").read_bytes())["rule"]
    assert official_rule["page_index"] == 2
    assert "page_index" not in arguments["ground_truth"].rules[0].rule


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.update(schema_version="filex-document-ir-v2"),
        lambda payload: payload.update(layout_pages="wrong"),
        lambda payload: payload["layout_pages"].append(
            copy.deepcopy(payload["layout_pages"][0])
        ),
        lambda payload: payload["layout_pages"][0].update(page_number=True),
        lambda payload: payload["layout_pages"][0].update(width=0),
        lambda payload: payload["layout_pages"][0].update(items={}),
        lambda payload: payload["layout_pages"][0]["items"][0].update(md=1),
        lambda payload: payload["layout_pages"][0]["items"][0]["bbox"].update(x=-1),
        lambda payload: payload["layout_pages"][0]["items"][0]["bbox"].update(w=0),
        lambda payload: payload["layout_pages"][0]["items"][0]["bbox"].update(w=100),
        lambda payload: payload["layout_pages"][0]["items"][0]["bbox"].update(
            label="unknown"
        ),
        lambda payload: payload["layout_pages"][0]["items"][0]["bbox"].update(
            confidence=1.1
        ),
        lambda payload: payload.update(pages=[{"page_index": -1, "markdown": "x"}]),
    ],
)
def test_malformed_public_layout_fails_closed(tmp_path: Path, mutation) -> None:
    arguments, payload = _fixture(tmp_path)
    mutation(payload)
    arguments["layout_path"].write_text(json.dumps(payload))
    with pytest.raises(verifier.ParseBenchVerificationError) as error:
        verifier._snapshot_artifacts(**arguments)
    assert error.value.code == "artifact_validation_failed"


@pytest.mark.parametrize(
    "content", [b'{"layout_pages":[],"layout_pages":[]}', b'{"layout_pages":NaN}']
)
def test_non_strict_layout_json_is_rejected(tmp_path: Path, content: bytes) -> None:
    arguments, _ = _fixture(tmp_path)
    arguments["layout_path"].write_bytes(content)
    with pytest.raises(verifier.ParseBenchVerificationError, match="strict JSON"):
        verifier._snapshot_artifacts(**arguments)


def test_source_integrity_remains_independent_of_producer(tmp_path: Path) -> None:
    arguments, _ = _fixture(tmp_path)
    verifier._validate_source_integrity(
        arguments["ground_truth"].source, workspace_root=arguments["workspace_root"]
    )
    (arguments["workspace_root"] / "input/document.pdf").write_bytes(b"changed source")
    with pytest.raises(verifier.ParseBenchVerificationError) as error:
        verifier._validate_source_integrity(
            arguments["ground_truth"].source, workspace_root=arguments["workspace_root"]
        )
    assert error.value.code == "source_integrity_mismatch"


def _embedded_case(
    tmp_path: Path,
    *,
    markdown: bytes,
    layout_task: bool = False,
    page: int | None = None,
) -> None:
    arguments, _ = _fixture(tmp_path, layout_task=layout_task, page=page)
    arguments["markdown_path"].write_bytes(markdown)
    if not layout_task:
        arguments["layout_path"].write_text('{"layout_pages":[]}')
    tests = tmp_path / "tests"
    package = tests / "parsebench_verifier"
    package.mkdir(parents=True)
    for name, content in _verifier_runtime_modules().items():
        if name != "artifacts.py":  # Prove the public verifier needs no FileX adapter.
            (package / name).write_bytes(content)
    truth = arguments["ground_truth"]
    rule = truth.rules[0]
    (tests / "ground_truth.json").write_text(
        json.dumps(
            {
                "schema_version": GROUND_TRUTH_SCHEMA_VERSION,
                "dataset_revision": DATASET_REVISION,
                "scorer_revision": SCORER_REVISION,
                "task_id": truth.task_id,
                "source": {
                    "path": truth.source.logical_path,
                    "runtime_path": str(truth.source.runtime_path),
                    "size": truth.source.size,
                    "sha256": truth.source.sha256,
                    "page": page,
                },
                "dimensions": [rule.dimension.value],
                "rules": [
                    {
                        "id": rule.rule_id,
                        "dimension": rule.dimension.value,
                        "type": rule.rule_type,
                        "rule": rule.rule,
                        "page": page,
                        "expected_markdown": None,
                        "tags": [],
                        "provenance": {
                            "source_jsonl": rule.source_jsonl,
                            "source_line": 1,
                        },
                    }
                ],
            }
        )
    )
    (tests / "parsebench-scope.json").write_text(
        json.dumps(
            {
                "schema_version": "aworld-parsebench-scope/v1",
                "kind": "smoke",
                "selection_manifest_sha256": "sha256:" + "0" * 64,
                "publishable": False,
                "non_publishable_reasons": ["smoke_selection"],
                "selected_execution_count": 1,
                "runtime_image": "python:3.12-slim",
                "dataset_revision": DATASET_REVISION,
                "scorer_revision": SCORER_REVISION,
            }
        )
    )
    (tests / "run.py").write_text("""from pathlib import Path
from parsebench_verifier.verifier import verify_parsebench_task
outcome = verify_parsebench_task(
    ground_truth_path=Path("/case/tests/ground_truth.json"),
    markdown_path=Path("/case/artifacts/document.md"),
    layout_path=Path("/case/artifacts/layout.json"),
    verifier_output=Path("/case/verifier"),
    workspace_root=Path("/case/workspace"),
)
print(outcome.stdout_line)
assert outcome.reward_path is not None, outcome.result.diagnostics
""")


def _run_embedded_case(tmp_path: Path, image: str) -> subprocess.CompletedProcess[str]:
    # docker cp works with both local and remote daemons, unlike bind mounts.
    created = subprocess.run(
        [
            "docker",
            "create",
            "--network",
            "none",
            image,
            "python",
            "/case/tests/run.py",
        ],
        check=True,
        text=True,
        capture_output=True,
        timeout=30,
    )
    container = created.stdout.strip()
    try:
        subprocess.run(
            ["docker", "cp", str(tmp_path), f"{container}:/case"],
            check=True,
            text=True,
            capture_output=True,
            timeout=30,
        )
        result = subprocess.run(
            ["docker", "start", "-a", container],
            check=False,
            text=True,
            capture_output=True,
            timeout=120,
        )
        copied = subprocess.run(
            ["docker", "cp", f"{container}:/case/verifier", str(tmp_path / "verifier")],
            check=False,
            text=True,
            capture_output=True,
            timeout=30,
        )
        assert copied.returncode == 0, result.stdout + result.stderr + copied.stderr
        return result
    finally:
        subprocess.run(
            ["docker", "rm", "-f", container],
            check=True,
            capture_output=True,
            timeout=30,
        )


def test_public_scorer_infrastructure_failure_is_not_a_zero_score(
    tmp_path: Path,
) -> None:
    _embedded_case(tmp_path, markdown=b"public")
    outcome = verifier.verify_parsebench_task(
        ground_truth_path=tmp_path / "tests/ground_truth.json",
        markdown_path=tmp_path / "artifacts/document.md",
        layout_path=tmp_path / "artifacts/layout.json",
        workspace_root=tmp_path / "workspace",
        verifier_output=tmp_path / "verifier",
        scorer_checkout=tmp_path / "missing-scorer",
        scorer_python=Path(sys.executable),
    )
    assert outcome.result.status.value == "execution_failed"
    assert outcome.result.diagnostic_reward is None
    assert outcome.reward_path is None
    assert not (tmp_path / "verifier/reward.json").exists()


@pytest.mark.parametrize(
    ("markdown", "expected"), [(b"public", 1.0), (b"wrong", 0.0), (b"", 0.0)]
)
def test_embedded_verifier_with_real_official_scorer_without_filex(
    tmp_path: Path, markdown: bytes, expected: float
) -> None:
    image = os.environ.get("PARSEBENCH_SCORER_TEST_IMAGE")
    if not image:
        pytest.skip(
            "Set PARSEBENCH_SCORER_TEST_IMAGE to exercise the public scorer image"
        )
    _embedded_case(tmp_path, markdown=markdown)
    process = _run_embedded_case(tmp_path, image)
    assert process.returncode == 0, process.stdout + process.stderr
    reward = json.loads((tmp_path / "verifier/reward.json").read_bytes())
    assert reward["parsebench_text_content_score"] == expected
    assert not (tmp_path / "artifacts/result.json").exists()


@pytest.mark.parametrize("page", [1, 3])
def test_embedded_verifier_official_layout_predictions(
    tmp_path: Path, page: int
) -> None:
    image = os.environ.get("PARSEBENCH_SCORER_TEST_IMAGE")
    if not image:
        pytest.skip(
            "Set PARSEBENCH_SCORER_TEST_IMAGE to exercise the public scorer image"
        )
    _embedded_case(tmp_path, markdown=b"public", layout_task=True, page=page)
    process = _run_embedded_case(tmp_path, image)
    assert process.returncode == 0, process.stdout + process.stderr
    reward = json.loads((tmp_path / "verifier/reward.json").read_bytes())
    assert reward["parsebench_layout_score"] == 1.0


@pytest.mark.parametrize(
    ("dimension", "rule_type", "rule", "markdown"),
    [
        ("table", "expected_markdown", {}, "<table><tr><td>public</td></tr></table>"),
        (
            "chart",
            "chart_data_point",
            {"labels": ["Revenue"], "value": "10"},
            "| Metric | Value |\n| --- | --- |\n| Revenue | 10 |\n",
        ),
        ("text_formatting", "is_bold", {"text": "public"}, "**public**"),
    ],
)
def test_embedded_verifier_other_official_dimensions(
    tmp_path: Path, dimension: str, rule_type: str, rule: dict, markdown: str
) -> None:
    image = os.environ.get("PARSEBENCH_SCORER_TEST_IMAGE")
    if not image:
        pytest.skip(
            "Set PARSEBENCH_SCORER_TEST_IMAGE to exercise the public scorer image"
        )
    _embedded_case(tmp_path, markdown=markdown.encode())
    ground_truth_path = tmp_path / "tests/ground_truth.json"
    ground_truth = json.loads(ground_truth_path.read_bytes())
    ground_truth["dimensions"] = [dimension]
    ground_truth["rules"][0].update(
        dimension=dimension,
        type=rule_type,
        rule=rule,
        expected_markdown=markdown if dimension == "table" else None,
        provenance={"source_jsonl": f"{dimension}.jsonl", "source_line": 1},
    )
    ground_truth_path.write_text(json.dumps(ground_truth))
    process = _run_embedded_case(tmp_path, image)
    assert process.returncode == 0, process.stdout + process.stderr
    result = json.loads((tmp_path / "verifier/parsebench-result.json").read_bytes())
    reward = json.loads((tmp_path / "verifier/reward.json").read_bytes())
    assert result["status"] == "scored", result
    assert reward[f"parsebench_{dimension}_score"] == 1.0


@pytest.mark.parametrize("damage", ["invalid_json", "changed_source"])
def test_embedded_verifier_rejects_bad_artifacts_without_reward(
    tmp_path: Path, damage: str
) -> None:
    image = os.environ.get("PARSEBENCH_SCORER_TEST_IMAGE")
    if not image:
        pytest.skip(
            "Set PARSEBENCH_SCORER_TEST_IMAGE to exercise the public scorer image"
        )
    _embedded_case(tmp_path, markdown=b"public")
    if damage == "invalid_json":
        (tmp_path / "artifacts/layout.json").write_text('{"layout_pages":NaN}')
    else:
        (tmp_path / "workspace/input/document.pdf").write_bytes(b"altered source")
    process = _run_embedded_case(tmp_path, image)
    assert process.returncode != 0
    assert "ParseBenchVerificationError" in process.stderr
    assert not (tmp_path / "verifier/reward.json").exists()
