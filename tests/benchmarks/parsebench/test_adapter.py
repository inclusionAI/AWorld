from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from aworld.benchmarks.parsebench.adapter import (
    DEFAULT_LLM_MODEL_PROFILE,
    DEFAULT_VLM_MODEL_PROFILE,
    FileXAdapterError,
    FileXExecutionOptions,
    FileXRunRequest,
    SubprocessFileXRunner,
    execute_filex_parsebench,
    load_parsebench_task_spec,
)
from aworld.benchmarks.parsebench.contracts import DATASET_REVISION, SCORER_REVISION


def _write_task_spec(
    workspace: Path,
    source: Path,
    *,
    page: int | None = 2,
    **extra: object,
) -> Path:
    import hashlib

    payload = {
        "schema_version": "aworld-parsebench-task/v1",
        "task_id": "parsebench-abc123",
        "dataset_revision": DATASET_REVISION,
        "scorer_revision": SCORER_REVISION,
        "source": {
            "runtime_path": str(source),
            "size": source.stat().st_size,
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "page": page,
        },
        **extra,
    }
    path = workspace / "parsebench-task.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class FakeRunner:
    def __init__(
        self,
        *,
        document_ir: dict[str, object],
        markdown: str = "# Report\n\n| A | B |\n|---|---|\n| 1 | 2 |\n",
        provider: str = "paddle_ocr",
        provider_version: str = "paddleocr-vl-1.6",
    ) -> None:
        self.document_ir = document_ir
        self.markdown = markdown
        self.provider = provider
        self.provider_version = provider_version
        self.requests: list[FileXRunRequest] = []

    def run(self, request: FileXRunRequest) -> dict[str, object]:
        self.requests.append(request)
        output = request.workspace_root / "document_parse" / request.task_id
        output.mkdir(parents=True, exist_ok=True)
        markdown_path = output / "document.md"
        document_path = output / "document.document.json"
        markdown_path.write_text(self.markdown, encoding="utf-8")
        document_path.write_text(json.dumps(self.document_ir), encoding="utf-8")
        return {
            "success": True,
            "file_path": str(markdown_path.relative_to(request.workspace_root)),
            "document_file_path": str(
                document_path.relative_to(request.workspace_root)
            ),
            "metrics": {
                "provider": self.provider,
                "provider_version": self.provider_version,
                "status": "success",
                "cache": {"status": "bypass"},
                "work": {"failed": 0},
                "error": {"count": 0},
                "model": {"timeout_count": 0},
            },
        }


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
                        "id": "table",
                        "type": "table",
                        "bbox": [
                            50,
                            100,
                            40 if invalid_bbox else 1100,
                            350,
                        ],
                        "text": "| A | B |\n|---|---|\n| 1 | 2 |",
                        "reading_order": 1,
                    },
                ],
                "spans": [],
            }
        ],
    }


def test_task_spec_loader_is_versioned_strict_and_ground_truth_blind(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    source = workspace / "input" / "document.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"fake-pdf")

    spec = load_parsebench_task_spec(_write_task_spec(workspace, source))

    assert spec.task_id == "parsebench-abc123"
    assert spec.source.runtime_path == source
    assert spec.source.page == 2

    for leaked_field in (
        "dimensions",
        "rules",
        "rule_count",
        "tags",
        "expected_markdown",
    ):
        with pytest.raises(FileXAdapterError, match="unexpected field"):
            load_parsebench_task_spec(
                _write_task_spec(workspace, source, **{leaked_field: []})
            )


def test_executor_normalizes_markdown_table_page_bbox_labels_and_order(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    artifacts = tmp_path / "logs" / "artifacts"
    source = workspace / "input" / "document.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"fake-pdf")
    spec = load_parsebench_task_spec(_write_task_spec(workspace, source))
    runner = FakeRunner(document_ir=_document_ir())

    result = execute_filex_parsebench(
        spec,
        options=FileXExecutionOptions(
            workspace_root=workspace,
            artifacts_root=artifacts,
            timeout_seconds=9,
        ),
        runner=runner,
    )

    request = runner.requests[0]
    assert request.provider == "paddle_ocr"
    assert request.page == 2
    assert request.no_cache is True
    assert request.timeout_seconds == 9
    assert request.workspace_root == workspace.resolve()
    assert request.llm_model_profile == DEFAULT_LLM_MODEL_PROFILE
    assert request.vlm_model_profile == DEFAULT_VLM_MODEL_PROFILE

    assert (artifacts / "document.md").read_text() == runner.markdown
    parse_output = json.loads((artifacts / "layout.json").read_text())
    assert parse_output["task_type"] == "parse"
    assert parse_output["example_id"] == spec.task_id
    assert parse_output["pages"] == [{"page_index": 1, "markdown": runner.markdown}]
    layout_page = parse_output["layout_pages"][0]
    assert layout_page["page_number"] == 2
    assert [item["reading_order"] for item in layout_page["items"]] == [1, 2]
    table = layout_page["items"][0]
    assert table["type"] == "table"
    assert table["layout_segments"][0] == {
        "x": 0.05,
        "y": 0.05,
        "w": 0.95,
        "h": 0.125,
        "label": "Table",
    }
    assert result["status"] == "succeeded"
    assert result["provenance"]["filex"]["provider"] == "paddle_ocr"
    assert result["provenance"]["filex"]["fallback_allowed"] is False
    assert result["provenance"]["filex"]["cache"] == "bypass"
    assert result["artifacts"]["document"]["sha256"]
    assert result["artifacts"]["layout"]["sha256"]
    assert json.loads((artifacts / "result.json").read_text()) == result


def test_executor_fails_closed_for_invalid_bbox(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = workspace / "input" / "document.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"fake-pdf")
    spec = load_parsebench_task_spec(_write_task_spec(workspace, source))

    with pytest.raises(FileXAdapterError, match="bbox"):
        execute_filex_parsebench(
            spec,
            options=FileXExecutionOptions(
                workspace_root=workspace,
                artifacts_root=tmp_path / "artifacts",
            ),
            runner=FakeRunner(document_ir=_document_ir(invalid_bbox=True)),
        )


def test_executor_checks_source_integrity_before_calling_filex(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = workspace / "input" / "document.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"fake-pdf")
    spec = load_parsebench_task_spec(_write_task_spec(workspace, source))
    source.write_bytes(b"tampered")
    runner = FakeRunner(document_ir=_document_ir())

    with pytest.raises(FileXAdapterError, match="integrity"):
        execute_filex_parsebench(
            spec,
            options=FileXExecutionOptions(
                workspace_root=workspace,
                artifacts_root=tmp_path / "artifacts",
            ),
            runner=runner,
        )

    assert runner.requests == []


def test_executor_rejects_provider_fallback_and_non_bypass_cache(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    source = workspace / "input" / "document.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"fake-pdf")
    spec = load_parsebench_task_spec(_write_task_spec(workspace, source))

    with pytest.raises(FileXAdapterError, match="fallback"):
        execute_filex_parsebench(
            spec,
            options=FileXExecutionOptions(
                workspace_root=workspace,
                artifacts_root=tmp_path / "artifacts",
            ),
            runner=FakeRunner(document_ir=_document_ir(), provider="liteparse"),
        )

    runner = FakeRunner(document_ir=_document_ir())
    original_run = runner.run

    def cached(request: FileXRunRequest) -> dict[str, object]:
        value = original_run(request)
        value["metrics"]["cache"]["status"] = "hit"  # type: ignore[index]
        return value

    runner.run = cached  # type: ignore[method-assign]
    with pytest.raises(FileXAdapterError, match="cache"):
        execute_filex_parsebench(
            spec,
            options=FileXExecutionOptions(
                workspace_root=workspace,
                artifacts_root=tmp_path / "artifacts",
            ),
            runner=runner,
        )


def test_executor_rejects_partial_provider_output(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = workspace / "input" / "document.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"fake-pdf")
    spec = load_parsebench_task_spec(_write_task_spec(workspace, source))
    runner = FakeRunner(document_ir=_document_ir())
    original_run = runner.run

    def partial(request: FileXRunRequest) -> dict[str, object]:
        value = original_run(request)
        value["metrics"]["status"] = "partial_success"  # type: ignore[index]
        return value

    runner.run = partial  # type: ignore[method-assign]
    with pytest.raises(FileXAdapterError, match="partial"):
        execute_filex_parsebench(
            spec,
            options=FileXExecutionOptions(
                workspace_root=workspace,
                artifacts_root=tmp_path / "artifacts",
            ),
            runner=runner,
        )


@pytest.mark.parametrize(
    ("raised", "message"),
    [
        (RuntimeError("provider failed"), "execution failed"),
        (subprocess.TimeoutExpired("filex", 3), "timed out"),
    ],
)
def test_executor_normalizes_runner_error_and_timeout(
    tmp_path: Path, raised: BaseException, message: str
) -> None:
    workspace = tmp_path / "workspace"
    source = workspace / "input" / "document.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"fake-pdf")
    spec = load_parsebench_task_spec(_write_task_spec(workspace, source))

    class FailingRunner:
        def run(self, _request: FileXRunRequest) -> dict[str, object]:
            raise raised

    with pytest.raises(FileXAdapterError, match=message):
        execute_filex_parsebench(
            spec,
            options=FileXExecutionOptions(
                workspace_root=workspace,
                artifacts_root=tmp_path / "artifacts",
            ),
            runner=FailingRunner(),
        )


def test_subprocess_runner_passes_only_explicit_deterministic_controls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "workspace" / "input" / "document.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"fake-pdf")
    seen: dict[str, object] = {}

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        seen.update(command=command, **kwargs)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "success": True,
                    "file_path": "document_parse/task/document.md",
                    "document_file_path": "document_parse/task/document.document.json",
                    "metrics": {
                        "provider": "paddle_ocr",
                        "provider_version": "paddleocr-vl-1.6",
                        "cache": {"status": "bypass"},
                    },
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    request = FileXRunRequest(
        source_path=source,
        task_id="task",
        file_type="pdf",
        provider="paddle_ocr",
        page=2,
        no_cache=True,
        timeout_seconds=12,
        workspace_root=source.parents[1],
        llm_model_profile=DEFAULT_LLM_MODEL_PROFILE,
        vlm_model_profile=DEFAULT_VLM_MODEL_PROFILE,
    )

    SubprocessFileXRunner(executable="filex").run(request)

    command = seen["command"]
    assert command[:2] == ["filex", "parse"]
    assert "--pages" in command and command[command.index("--pages") + 1] == "2"
    assert "--no-cache" in command
    assert "--force-refresh" not in command
    assert seen["timeout"] == 12
    assert seen["shell"] is False
    env = seen["env"]
    assert env["FILEX_WORKSPACE_ROOT"] == str(source.parents[1].resolve())
    inline = json.loads(command[command.index("--env-content-json") + 1])
    assert inline["filex_parse_provider"] == "paddle_ocr"
    assert inline["filex_cache_enabled"] is False
    assert inline["filex_no_cache"] is True
    assert inline["benchmark_llm_model_profile"] == DEFAULT_LLM_MODEL_PROFILE
    assert inline["benchmark_vlm_model_profile"] == DEFAULT_VLM_MODEL_PROFILE
