from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from parsebench_dataset import verifier
from parsebench_dataset.contracts import ParseBenchDimension


def _sha256(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _fixture(tmp_path: Path, *, model_call_count: int = 1):
    workspace = tmp_path / "workspace"
    source_path = workspace / "input/document.pdf"
    source_path.parent.mkdir(parents=True)
    source = b"%PDF visual fixture"
    source_path.write_bytes(source)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    markdown_path = artifacts / "document.md"
    layout_path = artifacts / "layout.json"
    markdown = b"# Visual output\n"
    layout = {
        "schema_version": "filex-document-ir-v2",
        "coordinate_system": "pixel_top_left_xyxy",
        "pages": [
            {
                "page_index": 0,
                "width": 100.0,
                "height": 100.0,
                "elements": [
                    {
                        "type": "text",
                        "bbox": [1.0, 2.0, 10.0, 20.0],
                        "text": "Visual output",
                        "reading_order": 0,
                    }
                ],
                "spans": [],
            }
        ],
    }
    layout_bytes = (
        json.dumps(layout, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    markdown_path.write_bytes(markdown)
    layout_path.write_bytes(layout_bytes)
    result = {
        "schema_version": "filex.skill.parse-result/v1",
        "status": "succeeded",
        "source": {
            "path": str(source_path),
            "size": len(source),
            "sha256": _sha256(source),
        },
        "artifacts": {
            "document": {
                "path": str(markdown_path),
                "size": len(markdown),
                "sha256": _sha256(markdown),
            },
            "layout": {
                "path": str(layout_path),
                "size": len(layout_bytes),
                "sha256": _sha256(layout_bytes),
            },
        },
        "filex": {
            "success": True,
            "metrics": {
                "schema_version": "1.0",
                "provider": "paddle_ocr",
                "provider_version": "3.7.0",
                "requested_provider": "paddle_ocr",
                "requested_provider_version": "paddleocr-vl-1.6",
                "status": "success",
                "cache": {"status": "bypass"},
                "model": {
                    "name": "real-vlm",
                    "call_count": model_call_count,
                    "timeout_count": 0,
                },
                "work": {"failed": 0},
                "error": {"count": 0},
            },
        },
    }
    result_bytes = (
        json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    ground_truth = verifier._GroundTruth(
        task_id="visual-task",
        source=verifier._GroundTruthSource(
            logical_path="docs/chart.pdf",
            runtime_path=Path("/workspace/input/document.pdf"),
            size=len(source),
            sha256=_sha256(source),
            page=None,
        ),
        dimensions=(ParseBenchDimension.CHART,),
        rules=(),
    )
    return workspace, ground_truth, result, result_bytes, markdown_path, layout_path


def test_generic_skill_accepts_explicit_local_workspace_mapping(tmp_path: Path) -> None:
    workspace, ground_truth, result, result_bytes, markdown_path, layout_path = (
        _fixture(tmp_path)
    )

    snapshot = verifier._snapshot_filex_skill_artifacts(
        ground_truth=ground_truth,
        result=result,
        result_bytes=result_bytes,
        markdown_path=markdown_path,
        layout_path=layout_path,
        workspace_root=workspace,
    )

    assert snapshot.result == result
    assert snapshot.layout["layout_pages"][0]["items"][0]["bbox"]["confidence"] == 1.0


def test_generic_skill_rejects_zero_vlm_calls(tmp_path: Path) -> None:
    workspace, ground_truth, result, result_bytes, markdown_path, layout_path = (
        _fixture(tmp_path, model_call_count=0)
    )

    with pytest.raises(
        verifier.ParseBenchVerificationError, match="could not be normalized"
    ):
        verifier._snapshot_filex_skill_artifacts(
            ground_truth=ground_truth,
            result=result,
            result_bytes=result_bytes,
            markdown_path=markdown_path,
            layout_path=layout_path,
            workspace_root=workspace,
        )
