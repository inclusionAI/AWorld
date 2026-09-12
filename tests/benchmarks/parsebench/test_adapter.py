from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from aworld.benchmarks.parsebench.adapter import (
    DEFAULT_VLM_MODEL_PROFILE,
    FileXAdapterError,
    FileXExecutionOptions,
    FileXRunRequest,
    FileXRunResult,
    SubprocessFileXRunner,
    _run_bounded_process,
    execute_filex_parsebench,
    load_parsebench_task_spec,
    normalize_filex_document_ir,
    validate_parsebench_artifacts,
)
from aworld.benchmarks.parsebench.contracts import DATASET_REVISION, SCORER_REVISION


def _write_task_spec(
    workspace: Path, source: Path, *, page: int | None = 2, **extra: object
) -> Path:
    payload = {
        "schema_version": "aworld-parsebench-task/v1",
        "task_id": "parsebench-abc123",
        "dataset_revision": DATASET_REVISION,
        "scorer_revision": SCORER_REVISION,
        "source": {
            "runtime_path": str(source),
            "size": source.stat().st_size,
            "sha256": "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest(),
            "page": page,
        },
        **extra,
    }
    path = workspace / "parsebench-task.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _document_ir(*, invalid_bbox: bool = False) -> dict[str, object]:
    return {
        "schema_version": "filex-document-ir-v2",
        "coordinate_system": "pixel_top_left_xyxy",
        "pages": [
            {
                "page_index": 1,
                "width": 1000,
                "height": 2000,
                "elements": [
                    {
                        "id": "late",
                        "type": "text",
                        "bbox": [100, 400, 900, 500],
                        "text": "After table",
                        "reading_order": 2,
                    },
                    {
                        "id": "title",
                        "type": "title",
                        "bbox": [100, 20, 500, 80],
                        "text": "Report",
                        "reading_order": 0,
                    },
                    {
                        "id": "table",
                        "type": "table",
                        "bbox": [50, 100, 40 if invalid_bbox else 1100, 350],
                        "text": "| A | B |",
                        "reading_order": 1,
                    },
                ],
                "spans": [],
            }
        ],
    }


class FakeRunner:
    def __init__(
        self,
        *,
        document_ir: dict[str, object],
        provider: str = "paddle_ocr",
        model_name: str = "gemini-3.1-pro-preview",
    ) -> None:
        self.document_ir = document_ir
        self.markdown = "# Report\n\n| A | B |\n|---|---|\n| 1 | 2 |\n"
        self.provider = provider
        self.model_name = model_name
        self.requests: list[FileXRunRequest] = []

    def run(self, request: FileXRunRequest) -> FileXRunResult:
        self.requests.append(request)
        output = request.workspace_root / "document_parse" / request.task_id
        output.mkdir(parents=True, exist_ok=True)
        markdown_path = output / "document.md"
        document_path = output / "document.document.json"
        markdown_path.write_text(self.markdown, encoding="utf-8")
        document_path.write_text(json.dumps(self.document_ir), encoding="utf-8")
        payload: dict[str, object] = {
            "success": True,
            "file_path": str(markdown_path.relative_to(request.workspace_root)),
            "document_file_path": str(
                document_path.relative_to(request.workspace_root)
            ),
            "metrics": {
                "schema_version": "1.0",
                "provider": self.provider,
                "provider_version": "paddleocr-vl-1.6",
                "requested_provider": request.provider,
                "requested_provider_version": "paddleocr-vl-1.6",
                "status": "success",
                "cache": {"status": "bypass"},
                "work": {"failed": 0},
                "error": {"count": 0},
                "model": {"name": self.model_name, "timeout_count": 0},
                "timings_ms": {
                    "initialization": 1,
                    "model_wait": 2,
                    "parse": 3,
                    "total": 6,
                },
            },
        }
        return FileXRunResult(payload=payload, resolved_model_name=self.model_name)


def _task(tmp_path: Path):
    workspace = tmp_path / "workspace"
    source = workspace / "input" / "document.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"fake-pdf")
    return (
        workspace,
        source,
        load_parsebench_task_spec(_write_task_spec(workspace, source)),
    )


def test_task_spec_loader_is_versioned_strict_and_ground_truth_blind(
    tmp_path: Path,
) -> None:
    workspace, source, spec = _task(tmp_path)
    assert spec.source.runtime_path == source
    for field in ("dimensions", "rules", "rule_count", "tags", "expected_markdown"):
        with pytest.raises(FileXAdapterError, match="unexpected field"):
            load_parsebench_task_spec(
                _write_task_spec(workspace, source, **{field: []})
            )


def test_executor_emits_pixel_xywh_lowercase_labels_and_valid_commit(
    tmp_path: Path,
) -> None:
    workspace, _source, spec = _task(tmp_path)
    artifacts = tmp_path / "logs" / "artifacts"
    runner = FakeRunner(document_ir=_document_ir())
    result = execute_filex_parsebench(
        spec,
        options=FileXExecutionOptions(
            workspace_root=workspace, artifacts_root=artifacts
        ),
        runner=runner,
    )
    layout = json.loads((artifacts / "layout.json").read_text())
    assert [item["reading_order"] for item in layout["layout_pages"][0]["items"]] == [
        0,
        1,
        2,
    ]
    assert layout["layout_pages"][0]["items"][1]["layout_segments"][0] == {
        "x": 50.0,
        "y": 100.0,
        "w": 950.0,
        "h": 250.0,
        "label": "table",
    }
    assert (
        result["provenance"]["filex"]["requested_model_profile"]
        == DEFAULT_VLM_MODEL_PROFILE
    )
    assert result["provenance"]["filex"]["resolved_model_name"] == runner.model_name
    assert result["timing_ms"]["total"] == 6.0
    assert (
        validate_parsebench_artifacts(
            result_path=artifacts / "result.json",
            markdown_path=artifacts / "document.md",
            layout_path=artifacts / "layout.json",
            expected_task_id=spec.task_id,
            expected_source=spec.source,
        )
        == result
    )


def test_layout_is_consumed_by_pinned_official_v3_scorer() -> None:
    scorer_root = Path("/private/tmp/ParseBench-scorer")
    if not scorer_root.is_dir():
        pytest.skip("pinned ParseBench scorer checkout is unavailable")
    sys.path.insert(0, str(scorer_root / "src"))
    try:
        from parse_bench.inference.layout_extraction import (
            extract_all_layouts_from_llamaparse_output,
        )
        from parse_bench.layout_label_mapping import (
            map_llamaparse_raw_label_to_canonical,
        )
    finally:
        sys.path.pop(0)
    output = normalize_filex_document_ir(
        _document_ir(),
        markdown="# Report",
        task_id="probe",
        provider="paddle_ocr",
        provider_version="probe",
        selected_page=2,
        clip_bboxes=True,
    )
    page = output["layout_pages"][0]
    raw = {
        "pages": [
            {
                "page": 2,
                "width": page["width"],
                "height": page["height"],
                "md": "# Report",
                "items": [
                    {
                        "type": item["type"],
                        "value": item["value"],
                        "layoutAwareBbox": item["layout_segments"],
                    }
                    for item in page["items"]
                ],
            }
        ]
    }
    predictions = extract_all_layouts_from_llamaparse_output(
        raw, example_id="probe", pipeline_name="filex/probe", label_version="v3"
    ).predictions
    assert len(predictions) == 3
    assert predictions[1].bbox == [50.0, 100.0, 1000.0, 350.0]
    assert map_llamaparse_raw_label_to_canonical(
        predictions[0].label, label_version="v3"
    )
    assert map_llamaparse_raw_label_to_canonical(
        predictions[1].label, label_version="v3"
    )


def test_executor_fails_closed_for_bbox_fallback_cache_model_and_partial(
    tmp_path: Path,
) -> None:
    workspace, _source, spec = _task(tmp_path)
    options = FileXExecutionOptions(
        workspace_root=workspace, artifacts_root=tmp_path / "artifacts"
    )
    with pytest.raises(FileXAdapterError, match="bbox"):
        execute_filex_parsebench(
            spec,
            options=options,
            runner=FakeRunner(document_ir=_document_ir(invalid_bbox=True)),
        )
    with pytest.raises(FileXAdapterError, match="fallback"):
        execute_filex_parsebench(
            spec,
            options=options,
            runner=FakeRunner(document_ir=_document_ir(), provider="liteparse"),
        )
    for path, value, match in (
        (("cache", "status"), "hit", "cache"),
        (("status",), "partial_success", "partial"),
        (("work", "failed"), 1, "partial"),
        (("error", "count"), 1, "partial"),
        (("model", "timeout_count"), 1, "partial"),
        (("model", "name"), "other", "model"),
        (("requested_provider_version",), "other", "version"),
    ):
        runner = FakeRunner(document_ir=_document_ir())
        original = runner.run

        def changed(request: FileXRunRequest, path=path, value=value) -> FileXRunResult:
            result = original(request)
            target = result.payload["metrics"]
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            return result

        runner.run = changed
        with pytest.raises(FileXAdapterError, match=match):
            execute_filex_parsebench(spec, options=options, runner=runner)


def test_executor_requires_complete_pinned_metrics(tmp_path: Path) -> None:
    workspace, _source, spec = _task(tmp_path)
    options = FileXExecutionOptions(
        workspace_root=workspace, artifacts_root=tmp_path / "artifacts"
    )
    for key in ("schema_version", "status", "work", "error", "model", "timings_ms"):
        runner = FakeRunner(document_ir=_document_ir())
        original = runner.run

        def missing(request: FileXRunRequest, key=key) -> FileXRunResult:
            result = original(request)
            del result.payload["metrics"][key]
            return result

        runner.run = missing
        with pytest.raises(FileXAdapterError):
            execute_filex_parsebench(spec, options=options, runner=runner)


def test_source_failure_invalidates_stale_result_marker(tmp_path: Path) -> None:
    workspace, source, spec = _task(tmp_path)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "result.json").write_text("stale")
    source.write_bytes(b"tampered")
    with pytest.raises(FileXAdapterError, match="integrity"):
        execute_filex_parsebench(
            spec,
            options=FileXExecutionOptions(
                workspace_root=workspace, artifacts_root=artifacts
            ),
            runner=FakeRunner(document_ir=_document_ir()),
        )
    assert not (artifacts / "result.json").exists()


def test_validator_rejects_mixed_artifact_and_symlink(tmp_path: Path) -> None:
    workspace, _source, spec = _task(tmp_path)
    artifacts = tmp_path / "artifacts"
    execute_filex_parsebench(
        spec,
        options=FileXExecutionOptions(
            workspace_root=workspace, artifacts_root=artifacts
        ),
        runner=FakeRunner(document_ir=_document_ir()),
    )
    (artifacts / "document.md").write_text("different generation")
    with pytest.raises(FileXAdapterError, match="checksum"):
        validate_parsebench_artifacts(
            result_path=artifacts / "result.json",
            markdown_path=artifacts / "document.md",
            layout_path=artifacts / "layout.json",
        )
    target = tmp_path / "target"
    target.write_text("{}")
    (artifacts / "result.json").unlink()
    (artifacts / "result.json").symlink_to(target)
    with pytest.raises(FileXAdapterError, match="unreadable"):
        validate_parsebench_artifacts(
            result_path=artifacts / "result.json",
            markdown_path=artifacts / "document.md",
            layout_path=artifacts / "layout.json",
        )


def test_subprocess_runner_binds_protected_gateway_without_secret_in_argv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "workspace" / "input" / "document.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"pdf")
    seen: dict[str, object] = {}

    def fake_bounded(command: list[str], **kwargs: object):
        seen.update(command=command, **kwargs)
        return b'{"success":true}', b"", 0

    monkeypatch.setattr(
        "aworld.benchmarks.parsebench.adapter._run_bounded_process", fake_bounded
    )
    request = FileXRunRequest(
        source,
        "task",
        "pdf",
        "paddle_ocr",
        2,
        True,
        12,
        source.parents[1],
        DEFAULT_VLM_MODEL_PROFILE,
    )
    secret = "very-secret-key"
    result = SubprocessFileXRunner(
        executable="filex",
        environment={
            "LLM_BASE_URL": "https://gateway.example/v1",
            "LLM_MODEL_NAME": "gemini-3.1-pro-preview",
            "LLM_API_KEY": secret,
        },
    ).run(request)
    command = seen["command"]
    assert secret not in " ".join(command)
    inline = json.loads(command[command.index("--env-content-json") + 1])
    assert inline["gateway_vllm"] == {
        "base_url": "https://gateway.example/v1",
        "model_name": "gemini-3.1-pro-preview",
    }
    assert set(inline["gateway_vllm"]) == {"base_url", "model_name"}
    child_env = seen["environment"]
    assert child_env["GATEWAY_VLLM_API_KEY"] == secret
    assert "LLM_API_KEY" not in child_env
    assert result.resolved_model_name == "gemini-3.1-pro-preview"


@pytest.mark.parametrize("missing", ["LLM_BASE_URL", "LLM_MODEL_NAME", "LLM_API_KEY"])
def test_subprocess_runner_fails_closed_for_missing_model_config(
    tmp_path: Path, missing: str
) -> None:
    source = tmp_path / "input.pdf"
    source.write_bytes(b"pdf")
    environment = {
        "LLM_BASE_URL": "https://gateway.example/v1",
        "LLM_MODEL_NAME": "gemini-3.1-pro-preview",
        "LLM_API_KEY": "secret",
    }
    del environment[missing]
    request = FileXRunRequest(
        source,
        "task",
        "pdf",
        "paddle_ocr",
        None,
        True,
        1,
        tmp_path,
        DEFAULT_VLM_MODEL_PROFILE,
    )
    with pytest.raises(FileXAdapterError, match="configuration"):
        SubprocessFileXRunner(executable="filex", environment=environment).run(request)


def test_bounded_process_rejects_excess_output() -> None:
    with pytest.raises(FileXAdapterError, match="stdout"):
        _run_bounded_process(
            [sys.executable, "-c", "import sys;sys.stdout.write('x'*10000)"],
            environment=os.environ,
            timeout=5,
            max_stdout_bytes=128,
            max_stderr_bytes=128,
        )


@pytest.mark.skipif(not hasattr(os, "killpg"), reason="POSIX process groups required")
def test_timeout_terminates_child_process_group(tmp_path: Path) -> None:
    pid_path = tmp_path / "child.pid"
    program = "import pathlib,subprocess,sys,time;p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);pathlib.Path(sys.argv[1]).write_text(str(p.pid));time.sleep(60)"
    with pytest.raises(subprocess.TimeoutExpired):
        _run_bounded_process(
            [sys.executable, "-c", program, str(pid_path)],
            environment=os.environ,
            timeout=0.3,
            max_stdout_bytes=128,
            max_stderr_bytes=128,
        )
    child_pid = int(pid_path.read_text())
    for _ in range(20):
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("child process survived timeout process-group termination")
