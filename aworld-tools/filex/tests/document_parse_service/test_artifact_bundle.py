from __future__ import annotations

import hashlib
import json
from pathlib import Path

from document_parse_service.artifact_bundle import (
    export_artifact_bundle,
    prepare_artifact_destination,
)


def _write_inputs(root: Path) -> tuple[Path, Path, Path]:
    source = root / "source.pdf"
    markdown = root / "result.md"
    document_ir = root / "result.document.json"
    source.write_bytes(b"%PDF generic source")
    markdown.write_bytes(b"hello\r\n")
    document_ir.write_text(
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
                                "type": "text",
                                "text": "hello",
                                "bbox": [10, 20, 40, 60],
                                "reading_order": 0,
                            }
                        ],
                        "spans": [],
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return source, markdown, document_ir


def test_parse_output_bundle_is_owned_and_verified_by_filex_cli(tmp_path: Path) -> None:
    source, markdown, document_ir = _write_inputs(tmp_path)
    destination = tmp_path / "artifacts"
    response = {
        "success": True,
        "task_id": "generic-task",
        "file_path": str(markdown),
        "document_file_path": str(document_ir),
        "metrics": {"provider": "paddle_ocr", "provider_version": "v1.6"},
    }

    summary = export_artifact_bundle(
        destination=destination,
        source=source,
        markdown=markdown,
        document_ir=document_ir,
        filex_response=response,
        layout_format="parse-output",
    )

    receipt = json.loads((destination / "result.json").read_bytes())
    assert receipt["schema_version"] == "filex.artifact-bundle/v1"
    assert receipt["layout_format"] == "parse-output"
    assert receipt["filex"] == response
    assert summary["filex_provenance"] == receipt["filex_provenance"]
    assert receipt["filex_provenance"]["exporter"] == "filex-cli"
    assert receipt["filex_provenance"]["provider"] == "paddle_ocr"
    assert set(receipt["artifacts"]) == {"document", "layout", "document_ir"}
    for entry in [receipt["source"], *receipt["artifacts"].values()]:
        content = Path(entry["path"]).read_bytes()
        assert entry["size"] == len(content)
        assert entry["sha256"] == "sha256:" + hashlib.sha256(content).hexdigest()


def test_preparing_attempt_invalidates_only_prior_receipt(tmp_path: Path) -> None:
    destination = tmp_path / "artifacts"
    destination.mkdir()
    (destination / "result.json").write_text('{"status":"succeeded"}\n')
    (destination / "layout.json").write_text('{"diagnostic":true}\n')

    assert prepare_artifact_destination(destination) == destination.resolve()

    assert not (destination / "result.json").exists()
    assert (destination / "layout.json").is_file()


def test_bundle_rejects_unsuccessful_response_and_invalidates_old_receipt(
    tmp_path: Path,
) -> None:
    source, markdown, document_ir = _write_inputs(tmp_path)
    destination = tmp_path / "artifacts"
    destination.mkdir()
    (destination / "result.json").write_text('{"status":"succeeded"}\n')

    try:
        export_artifact_bundle(
            destination=destination,
            source=source,
            markdown=markdown,
            document_ir=document_ir,
            filex_response={"success": False},
            layout_format="parse-output",
        )
    except ValueError as exc:
        assert "successful FileX response" in str(exc)
    else:  # pragma: no cover - explicit assertion for a public safety contract
        raise AssertionError("unsuccessful FileX response was accepted")

    assert not (destination / "result.json").exists()
