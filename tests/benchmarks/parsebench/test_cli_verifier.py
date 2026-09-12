from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "aworld-cli" / "src"))

from aworld.benchmarks.parsebench.adapter import FileXRunRequest, FileXRunResult
from aworld.benchmarks.parsebench.contracts import (
    DATASET_REVISION,
    PINNED_PARSEBENCH_CONTRACT,
    SCORER_REVISION,
    ParseBenchDimension,
)
from aworld.benchmarks.parsebench.execution import (
    ATIF_SCHEMA_VERSION,
    DEFAULT_PARSEBENCH_MODEL_PROFILE,
    ParseBenchExecutionError,
    run_parsebench_task,
)
from aworld.benchmarks.parsebench.scoring import (
    ParseBenchDimensionResult,
    ParseBenchResultStatus,
    ParseBenchVerifierResult,
)
from aworld.benchmarks.parsebench.verifier import (
    ParseBenchVerificationError,
    verify_parsebench_task,
)
from aworld.plugins.discovery import discover_plugins
from aworld_cli.core.top_level_command_system import TopLevelCommandRegistry
from aworld_cli.plugin_capabilities.cli_commands import sync_plugin_cli_commands


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def _source_digest(source: Path) -> str:
    return "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest()


class _FakeFileXRunner:
    def __init__(self) -> None:
        self.requests: list[FileXRunRequest] = []

    def run(self, request: FileXRunRequest) -> FileXRunResult:
        self.requests.append(request)
        output = request.workspace_root / "filex-output"
        output.mkdir(parents=True, exist_ok=True)
        markdown_path = output / "document.md"
        document_ir_path = output / "document.document.json"
        markdown_path.write_text("# Report\n\nAlpha\n", encoding="utf-8")
        document_ir_path.write_text(
            json.dumps(
                {
                    "schema_version": "filex-document-ir-v2",
                    "coordinate_system": "pixel_top_left_xyxy",
                    "pages": [
                        {
                            "page_index": 0,
                            "width": 100,
                            "height": 200,
                            "elements": [
                                {
                                    "id": "title",
                                    "type": "title",
                                    "bbox": [10, 10, 90, 30],
                                    "text": "Report",
                                    "reading_order": 0,
                                },
                                {
                                    "id": "text",
                                    "type": "text",
                                    "bbox": [10, 40, 90, 70],
                                    "text": "Alpha",
                                    "reading_order": 1,
                                },
                            ],
                            "spans": [],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        payload: dict[str, object] = {
            "success": True,
            "file_path": str(markdown_path.relative_to(request.workspace_root)),
            "document_file_path": str(
                document_ir_path.relative_to(request.workspace_root)
            ),
            "metrics": {
                "schema_version": "1.0",
                "provider": request.provider,
                "provider_version": "paddleocr-vl-test",
                "requested_provider": request.provider,
                "requested_provider_version": "paddleocr-vl-test",
                "status": "success",
                "cache": {"status": "bypass"},
                "work": {"failed": 0},
                "error": {"count": 0},
                "model": {
                    "name": "protected-model-name",
                    "timeout_count": 0,
                },
                "timings_ms": {
                    "initialization": 1,
                    "model_wait": 2,
                    "parse": 3,
                    "total": 6,
                },
            },
        }
        return FileXRunResult(
            payload=payload,
            resolved_model_name="protected-model-name",
        )


def _write_public_task(workspace: Path) -> tuple[Path, Path]:
    source = workspace / "input" / "document.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"%PDF-fake")
    task_path = workspace / "parsebench-task.json"
    task_path.write_bytes(
        _canonical_bytes(
            {
                "schema_version": "aworld-parsebench-task/v1",
                "task_id": "pb-cli-verifier-test",
                "dataset_revision": DATASET_REVISION,
                "scorer_revision": SCORER_REVISION,
                "source": {
                    "runtime_path": str(source),
                    "size": source.stat().st_size,
                    "sha256": _source_digest(source),
                    "page": 1,
                },
            }
        )
    )
    return task_path, source


def _run_filex_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path, Path, Path, Path, _FakeFileXRunner]:
    workspace = tmp_path / "workspace"
    artifacts = tmp_path / "logs" / "artifacts"
    trajectory = tmp_path / "logs" / "agent" / "trajectory.json"
    task_path, source = _write_public_task(workspace)
    runner = _FakeFileXRunner()
    monkeypatch.setenv("LLM_BASE_URL", "https://model-gateway.invalid/v1")
    monkeypatch.setenv("LLM_MODEL_NAME", "protected-model-name")
    monkeypatch.setenv("LLM_API_KEY", "sentinel-super-secret-api-key")

    run_parsebench_task(
        task_spec_path=task_path,
        workspace_root=workspace,
        artifacts_root=artifacts,
        trajectory_path=trajectory,
        runner=runner,
    )
    return task_path, source, artifacts, trajectory, workspace, runner


def _rule_for(
    dimension: ParseBenchDimension,
) -> tuple[str, dict[str, object], str | None]:
    if dimension is ParseBenchDimension.TABLE:
        return "expected_markdown", {}, "<table><tr><td>Alpha</td></tr></table>"
    if dimension is ParseBenchDimension.CHART:
        return (
            "chart_data_point",
            {
                "labels": ["series", "label"],
                "max_diffs": 0,
                "normalize_numbers": True,
                "value": "1",
            },
            None,
        )
    if dimension is ParseBenchDimension.TEXT_CONTENT:
        return "missing_sentence_percent", {"bag_of_sentence": {"Alpha": 1}}, None
    if dimension is ParseBenchDimension.TEXT_FORMATTING:
        return "is_title", {"text": "Report", "level": 1}, None
    return (
        "layout",
        {
            "attributes": {"title_level": "title"},
            "bbox": [0.1, 0.05, 0.8, 0.1],
            "canonical_class": "Title",
            "content": {"text": "Report", "type": "text"},
            "ro_index": 0,
            "source_label": "title",
        },
        None,
    )


def _write_ground_truth(
    path: Path,
    *,
    source: Path,
    dimensions: tuple[ParseBenchDimension, ...],
) -> Path:
    rules = []
    for source_line, dimension in enumerate(dimensions, start=1):
        rule_type, rule_payload, expected_markdown = _rule_for(dimension)
        rules.append(
            {
                "id": f"rule-{dimension.value}",
                "dimension": dimension.value,
                "type": rule_type,
                "rule": rule_payload,
                "page": 1,
                "expected_markdown": expected_markdown,
                "tags": ["synthetic"],
                "provenance": {
                    "source_jsonl": PINNED_PARSEBENCH_CONTRACT.source_file_for(
                        dimension
                    ),
                    "source_line": source_line,
                },
            }
        )
    path.write_bytes(
        _canonical_bytes(
            {
                "schema_version": "aworld-parsebench-ground-truth/v1",
                "dataset_revision": DATASET_REVISION,
                "scorer_revision": SCORER_REVISION,
                "task_id": "pb-cli-verifier-test",
                "source": {
                    "path": "docs/mixed/document.pdf",
                    "runtime_path": str(source),
                    "size": source.stat().st_size,
                    "sha256": _source_digest(source),
                    "page": 1,
                },
                "dimensions": [dimension.value for dimension in dimensions],
                "rules": rules,
            }
        )
    )
    path.with_name("parsebench-scope.json").write_bytes(
        _canonical_bytes(
            {
                "schema_version": "aworld-parsebench-scope/v1",
                "kind": "official-full",
                "selection_manifest_sha256": "sha256:" + "1" * 64,
                "publishable": True,
                "non_publishable_reasons": [],
                "selected_execution_count": (
                    PINNED_PARSEBENCH_CONTRACT.unique_execution_count
                ),
                "runtime_image": "aworld-filex@sha256:" + "2" * 64,
                "dataset_revision": DATASET_REVISION,
                "scorer_revision": SCORER_REVISION,
            }
        )
    )
    return path


def test_run_task_emits_artifacts_and_minimal_secret_free_atif(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, artifacts, trajectory_path, _, runner = _run_filex_fixture(
        tmp_path, monkeypatch
    )

    request = runner.requests[0]
    assert request.vlm_model_profile == DEFAULT_PARSEBENCH_MODEL_PROFILE
    assert (artifacts / "document.md").exists()
    assert (artifacts / "layout.json").exists()
    assert (artifacts / "result.json").exists()

    trajectory = json.loads(trajectory_path.read_text(encoding="utf-8"))
    assert trajectory["schema_version"] == ATIF_SCHEMA_VERSION
    assert trajectory["agent"] == {
        "name": "aworld-filex-parsebench",
        "version": "1.0",
        "model_name": DEFAULT_PARSEBENCH_MODEL_PROFILE,
    }
    assert trajectory["steps"] == [
        {
            "step_id": 1,
            "source": "agent",
            "message": "FileX ParseBench artifacts emitted.",
            "llm_call_count": 0,
        }
    ]
    assert trajectory["final_metrics"] == {"total_steps": 1}
    assert trajectory["extra"]["task_id"] == "pb-cli-verifier-test"
    assert trajectory["extra"]["result_sha256"].startswith("sha256:")

    emitted = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (*artifacts.iterdir(), trajectory_path)
    )
    assert "sentinel-super-secret-api-key" not in emitted
    assert "https://model-gateway.invalid/v1" not in emitted
    assert "protected-model-name" not in trajectory_path.read_text(encoding="utf-8")


def test_run_task_requires_protected_model_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    task_path, _ = _write_public_task(workspace)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("LLM_BASE_URL", "https://model-gateway.invalid/v1")
    monkeypatch.setenv("LLM_MODEL_NAME", "model")
    trajectory = tmp_path / "trajectory.json"
    trajectory.write_text('{"stale":true}\n', encoding="utf-8")

    with pytest.raises(ParseBenchExecutionError) as caught:
        run_parsebench_task(
            task_spec_path=task_path,
            workspace_root=workspace,
            artifacts_root=tmp_path / "artifacts",
            trajectory_path=trajectory,
            runner=_FakeFileXRunner(),
        )

    assert caught.value.code == "protected_model_config_missing"
    assert "LLM_API_KEY" in str(caught.value)
    assert not trajectory.exists()


def test_verify_task_builds_official_inputs_for_every_dimension_and_rewards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, source, artifacts, _, _, _ = _run_filex_fixture(tmp_path, monkeypatch)
    dimensions = tuple(ParseBenchDimension)
    ground_truth = _write_ground_truth(
        tmp_path / "ground_truth.json",
        source=source,
        dimensions=dimensions,
    )
    scorer_calls: list[
        tuple[ParseBenchDimension, dict[str, object], dict[str, object]]
    ] = []
    scores = {
        ParseBenchDimension.TABLE: 0.2,
        ParseBenchDimension.CHART: 0.4,
        ParseBenchDimension.TEXT_CONTENT: 0.6,
        ParseBenchDimension.TEXT_FORMATTING: 0.8,
        ParseBenchDimension.LAYOUT: 1.0,
    }
    sentinel_environment = object()
    monkeypatch.setattr(
        "aworld.benchmarks.parsebench.verifier.validate_official_scorer",
        lambda checkout, python_executable: sentinel_environment,
    )

    def fake_official_scorer(
        environment: object,
        *,
        dimension: ParseBenchDimension,
        output_dir: Path,
        test_cases_dir: Path,
        report_dir: Path,
        max_workers: int,
        timeout_seconds: float,
    ) -> ParseBenchDimensionResult:
        assert environment is sentinel_environment
        assert max_workers == 1
        assert timeout_seconds == 33
        assert report_dir.parent.name == "reports"
        jsonl_files = list(test_cases_dir.glob("*.jsonl"))
        assert [path.name for path in jsonl_files] == [f"{dimension.value}.jsonl"]
        row = json.loads(jsonl_files[0].read_text(encoding="utf-8"))
        inference_files = list(output_dir.glob("*.result.json"))
        assert len(inference_files) == 1
        inference = json.loads(inference_files[0].read_text(encoding="utf-8"))
        expected_group = (
            "text"
            if dimension
            in {
                ParseBenchDimension.TEXT_CONTENT,
                ParseBenchDimension.TEXT_FORMATTING,
            }
            else dimension.value
        )
        expected_example_id = f"{expected_group}/document"
        assert row == {
            "pdf": "docs/mixed/document.pdf",
            "category": dimension.value,
            "id": f"rule-{dimension.value}",
            "type": _rule_for(dimension)[0],
            "rule": _rule_for(dimension)[1],
            "page": 1,
            "expected_markdown": _rule_for(dimension)[2],
            "tags": ["synthetic"],
        }
        assert inference["request"] == {
            "example_id": expected_example_id,
            "source_file_path": "docs/mixed/document.pdf",
            "product_type": "parse",
        }
        assert inference["output"]["example_id"] == expected_example_id
        assert inference["output"]["markdown"] == "# Report\n\nAlpha\n"
        assert inference["product_type"] == "parse"
        assert inference["latency_in_ms"] == 0
        assert inference["raw_output"]["result_sha256"].startswith("sha256:")
        scorer_calls.append((dimension, row, inference))
        return ParseBenchDimensionResult.scored(
            dimension=dimension,
            score=scores[dimension],
            total_examples=1,
            successful_examples=1,
            numeric_examples=1,
        )

    monkeypatch.setattr(
        "aworld.benchmarks.parsebench.verifier.run_official_scorer",
        fake_official_scorer,
    )
    verifier_output = tmp_path / "logs" / "verifier"

    outcome = verify_parsebench_task(
        ground_truth_path=ground_truth,
        result_path=artifacts / "result.json",
        markdown_path=artifacts / "document.md",
        layout_path=artifacts / "layout.json",
        verifier_output=verifier_output,
        scorer_checkout=tmp_path / "official-scorer",
        scorer_python=tmp_path / "official-scorer" / ".venv" / "bin" / "python",
        scorer_timeout_seconds=33,
    )

    assert [call[0] for call in scorer_calls] == list(dimensions)
    assert outcome.result.publishable is True
    assert outcome.result.diagnostic_reward == pytest.approx(0.6)
    reward_bytes = (verifier_output / "reward.json").read_bytes()
    reward = json.loads(reward_bytes)
    assert list(reward)[0] == "reward"
    assert reward["reward"] == pytest.approx(0.6)
    assert len(reward) == 31
    assert reward["parsebench_layout_score"] == 1.0
    details = json.loads(
        (verifier_output / "parsebench-result.json").read_text(encoding="utf-8")
    )
    assert ParseBenchVerifierResult.from_dict(details) == outcome.result
    assert details["data_revision"] == DATASET_REVISION
    assert details["scorer_revision"] == SCORER_REVISION
    assert details["benchmark_scope"] == {
        "kind": "official-full",
        "selection_manifest_sha256": "sha256:" + "1" * 64,
        "publishable": True,
    }
    assert outcome.stdout_line.startswith("AWORLD_PARSEBENCH_RESULT=")


def test_verify_task_preserves_official_failure_as_publishable_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, source, artifacts, _, _, _ = _run_filex_fixture(tmp_path, monkeypatch)
    ground_truth = _write_ground_truth(
        tmp_path / "ground_truth.json",
        source=source,
        dimensions=(ParseBenchDimension.LAYOUT,),
    )
    monkeypatch.setattr(
        "aworld.benchmarks.parsebench.verifier.validate_official_scorer",
        lambda checkout, python_executable: object(),
    )
    monkeypatch.setattr(
        "aworld.benchmarks.parsebench.verifier.run_official_scorer",
        lambda *args, **kwargs: ParseBenchDimensionResult.scored(
            dimension=ParseBenchDimension.LAYOUT,
            score=0.0,
            total_examples=1,
            failed_examples=1,
            official_failure_examples=1,
            numeric_examples=0,
            official_failure_details=("provider produced no usable output",),
        ),
    )

    outcome = verify_parsebench_task(
        ground_truth_path=ground_truth,
        result_path=artifacts / "result.json",
        markdown_path=artifacts / "document.md",
        layout_path=artifacts / "layout.json",
        verifier_output=tmp_path / "verifier",
        scorer_checkout=tmp_path / "scorer",
        scorer_python=tmp_path / "python",
    )

    assert outcome.result.publishable is True
    assert (
        outcome.result.case_results[0].status is ParseBenchResultStatus.OFFICIAL_FAILURE
    )
    assert outcome.result.reward_payload()["parsebench_layout_score"] == 0.0
    assert (
        outcome.result.reward_payload()["parsebench_layout_official_failure_count"] == 1
    )


def test_verify_task_propagates_nonpublishable_smoke_scope_but_keeps_reward(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, source, artifacts, _, _, _ = _run_filex_fixture(tmp_path, monkeypatch)
    ground_truth = _write_ground_truth(
        tmp_path / "ground_truth.json",
        source=source,
        dimensions=(ParseBenchDimension.TABLE,),
    )
    scope_path = ground_truth.with_name("parsebench-scope.json")
    scope = json.loads(scope_path.read_text(encoding="utf-8"))
    scope.update(
        {
            "kind": "smoke",
            "publishable": False,
            "non_publishable_reasons": [
                "smoke_selection",
                "mutable_runtime_image",
            ],
            "selected_execution_count": 1,
            "runtime_image": "aworld-filex-parsebench:local",
        }
    )
    scope_path.write_bytes(_canonical_bytes(scope))
    monkeypatch.setattr(
        "aworld.benchmarks.parsebench.verifier.validate_official_scorer",
        lambda checkout, python_executable: object(),
    )
    monkeypatch.setattr(
        "aworld.benchmarks.parsebench.verifier.run_official_scorer",
        lambda *args, **kwargs: ParseBenchDimensionResult.scored(
            dimension=ParseBenchDimension.TABLE,
            score=0.75,
            total_examples=1,
            successful_examples=1,
            numeric_examples=1,
        ),
    )

    outcome = verify_parsebench_task(
        ground_truth_path=ground_truth,
        result_path=artifacts / "result.json",
        markdown_path=artifacts / "document.md",
        layout_path=artifacts / "layout.json",
        verifier_output=tmp_path / "verifier",
        scorer_checkout=tmp_path / "scorer",
        scorer_python=tmp_path / "python",
    )

    assert outcome.result.publishable is True
    assert outcome.result.scope_publishable is False
    assert outcome.result.reward_payload()["reward"] == 0.75
    assert outcome.reward_path is not None
    details = json.loads(outcome.result_path.read_text(encoding="utf-8"))
    assert details["benchmark_scope"]["kind"] == "smoke"
    assert details["benchmark_scope"]["publishable"] is False


def test_verify_task_infra_failure_removes_stale_reward_and_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, source, artifacts, _, _, _ = _run_filex_fixture(tmp_path, monkeypatch)
    ground_truth = _write_ground_truth(
        tmp_path / "ground_truth.json",
        source=source,
        dimensions=(ParseBenchDimension.TABLE,),
    )
    verifier_output = tmp_path / "verifier"
    verifier_output.mkdir()
    (verifier_output / "reward.json").write_text('{"reward":1}\n', encoding="utf-8")
    monkeypatch.setattr(
        "aworld.benchmarks.parsebench.verifier.validate_official_scorer",
        lambda checkout, python_executable: object(),
    )
    monkeypatch.setattr(
        "aworld.benchmarks.parsebench.verifier.run_official_scorer",
        lambda *args, **kwargs: ParseBenchDimensionResult.execution_failed(
            dimension=ParseBenchDimension.TABLE,
            errors=("official scorer timed out",),
        ),
    )

    outcome = verify_parsebench_task(
        ground_truth_path=ground_truth,
        result_path=artifacts / "result.json",
        markdown_path=artifacts / "document.md",
        layout_path=artifacts / "layout.json",
        verifier_output=verifier_output,
        scorer_checkout=tmp_path / "scorer",
        scorer_python=tmp_path / "python",
    )

    assert outcome.result.publishable is False
    assert outcome.result.status is ParseBenchResultStatus.EXECUTION_FAILED
    assert not (verifier_output / "reward.json").exists()
    details = json.loads(
        (verifier_output / "parsebench-result.json").read_text(encoding="utf-8")
    )
    assert details["publishable"] is False
    assert details["counts"]["execution_failure"] == 1
    assert outcome.stdout_line.startswith("AWORLD_PARSEBENCH_RESULT=")


def test_verify_task_rejects_tampered_artifact_before_official_scorer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, source, artifacts, _, _, _ = _run_filex_fixture(tmp_path, monkeypatch)
    ground_truth = _write_ground_truth(
        tmp_path / "ground_truth.json",
        source=source,
        dimensions=(ParseBenchDimension.TABLE,),
    )
    (artifacts / "document.md").write_text("tampered", encoding="utf-8")
    called = False

    def scorer(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("must not run")

    monkeypatch.setattr(
        "aworld.benchmarks.parsebench.verifier.run_official_scorer", scorer
    )

    with pytest.raises(ParseBenchVerificationError) as caught:
        verify_parsebench_task(
            ground_truth_path=ground_truth,
            result_path=artifacts / "result.json",
            markdown_path=artifacts / "document.md",
            layout_path=artifacts / "layout.json",
            verifier_output=tmp_path / "verifier",
            scorer_checkout=tmp_path / "scorer",
            scorer_python=tmp_path / "python",
        )

    assert caught.value.code == "artifact_validation_failed"
    assert called is False


def test_verify_task_rejects_tampered_verifier_source_before_scorer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, source, artifacts, _, _, _ = _run_filex_fixture(tmp_path, monkeypatch)
    ground_truth = _write_ground_truth(
        tmp_path / "ground_truth.json",
        source=source,
        dimensions=(ParseBenchDimension.TABLE,),
    )
    source.write_bytes(b"%PDF-evil")
    called = False

    def scorer(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("must not run")

    monkeypatch.setattr(
        "aworld.benchmarks.parsebench.verifier.run_official_scorer", scorer
    )
    with pytest.raises(ParseBenchVerificationError) as caught:
        verify_parsebench_task(
            ground_truth_path=ground_truth,
            result_path=artifacts / "result.json",
            markdown_path=artifacts / "document.md",
            layout_path=artifacts / "layout.json",
            verifier_output=tmp_path / "verifier",
            scorer_checkout=tmp_path / "scorer",
            scorer_python=tmp_path / "python",
        )

    assert caught.value.code == "source_integrity_mismatch"
    assert called is False


def test_builtin_benchmark_plugin_is_discoverable_and_exposes_help(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FILEX_BENCHMARK_VLM_PROFILE", "runtime-profile")
    plugin_root = (
        Path(__file__).resolve().parents[3]
        / "aworld-cli"
        / "src"
        / "aworld_cli"
        / "builtin_plugins"
        / "benchmark_cli"
    )
    plugins = discover_plugins([plugin_root])
    assert len(plugins) == 1
    registry = TopLevelCommandRegistry()
    sync_plugin_cli_commands(
        registry,
        plugins,
        builtin_plugin_roots=(plugin_root,),
    )
    command = registry.get("benchmark")
    assert command is not None

    parser = argparse.ArgumentParser(prog="aworld-cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    command.register_parser(subparsers)
    with pytest.raises(SystemExit) as caught:
        parser.parse_args(["benchmark", "parsebench", "run-task", "--help"])
    assert caught.value.code == 0
    run_help = capsys.readouterr().out
    assert "--task" in run_help
    assert "--task-spec" in run_help
    assert "--trajectory-output" in run_help
    assert "--model-profile" in run_help

    with pytest.raises(SystemExit) as caught:
        parser.parse_args(["benchmark", "parsebench", "verify-task", "--help"])
    assert caught.value.code == 0
    verify_help = capsys.readouterr().out
    assert "--ground-truth" in verify_help
    assert "--scope" in verify_help
    assert "--scorer-checkout" in verify_help

    runtime_args = parser.parse_args(
        [
            "benchmark",
            "parsebench",
            "run-task",
            "--task-spec",
            "/workspace/parsebench-task.json",
            "--trajectory-output",
            "/logs/agent/trajectory.json",
        ]
    )
    assert runtime_args.task_spec == Path("/workspace/parsebench-task.json")
    assert runtime_args.trajectory_output == Path("/logs/agent/trajectory.json")
    assert runtime_args.model_profile == "runtime-profile"

    verify_args = parser.parse_args(
        ["benchmark", "parsebench", "verify-task", "--scope", "/tests/scope.json"]
    )
    smoke_outcome = argparse.Namespace(
        stdout_line=(
            'AWORLD_PARSEBENCH_RESULT={"benchmark_scope":{"publishable":false}}'
        ),
        result=argparse.Namespace(publishable=False),
    )
    monkeypatch.setattr(
        "aworld_cli.top_level_commands.benchmark_cmd.verify_parsebench_task",
        lambda **_: smoke_outcome,
    )
    assert command.run(verify_args, None) == 0
    assert "publishable\":false" in capsys.readouterr().out
