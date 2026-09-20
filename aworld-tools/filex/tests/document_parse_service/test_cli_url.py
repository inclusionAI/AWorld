from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path

import document_parse_service.cli as cli_module
import pytest


class _FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, *, url: str) -> None:
        super().__init__(body)
        self.headers = {"Content-Length": str(len(body))}
        self._url = url

    def geturl(self) -> str:
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        self.close()


def test_download_url_saves_a_sanitized_file_inside_workspace(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(cli_module, "FS_WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(
        cli_module,
        "urlopen",
        lambda _request, timeout: _FakeResponse(
            b"%PDF-1.7\n", url="https://cdn.example/report%202026.pdf"
        ),
    )

    result = cli_module._download_url("https://example.com/download?id=1")

    assert result.is_relative_to(tmp_path)
    assert result.name == "report_2026.pdf"
    assert result.read_bytes() == b"%PDF-1.7\n"


@pytest.mark.parametrize(
    "url", ["file:///tmp/report.pdf", "relative.pdf", "ftp://example.com/a.pdf"]
)
def test_download_url_rejects_non_http_sources(url: str) -> None:
    with pytest.raises(ValueError, match=r"HTTP\(S\)"):
        cli_module._download_url(url)


def test_download_url_enforces_stream_size_limit(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cli_module, "FS_WORKSPACE_ROOT", tmp_path)
    monkeypatch.setenv("FILEX_MAX_DOWNLOAD_BYTES", "4")
    monkeypatch.setattr(
        cli_module,
        "urlopen",
        lambda _request, timeout: _FakeResponse(
            b"12345", url="https://example.com/data.bin"
        ),
    )

    with pytest.raises(ValueError, match="FILEX_MAX_DOWNLOAD_BYTES"):
        cli_module._download_url("https://example.com/data.bin")


@pytest.mark.parametrize(
    ("source", "expected_kind"),
    [
        ("https://www.youtube.com/watch?v=abc123", "youtube"),
        ("https://example.com/report.pdf", "http"),
        ("/root/workspace/report.pdf", "local"),
    ],
)
def test_resolve_parse_source_detects_source_kind(
    source: str, expected_kind: str
) -> None:
    parser = cli_module._build_parser()
    args = parser.parse_args(["parse", source])

    kind, value = cli_module._resolve_parse_source(args)

    assert kind == expected_kind
    assert value == source


def test_resolve_parse_source_keeps_legacy_flags() -> None:
    parser = cli_module._build_parser()
    args = parser.parse_args(["parse", "--url", "https://example.com/report.pdf"])

    assert cli_module._resolve_parse_source(args) == (
        "http",
        "https://example.com/report.pdf",
    )


def test_resolve_parse_source_rejects_ambiguous_input() -> None:
    parser = cli_module._build_parser()
    args = parser.parse_args(
        ["parse", "https://example.com/a.pdf", "--url", "https://example.com/b.pdf"]
    )

    with pytest.raises(ValueError, match="exactly one"):
        cli_module._resolve_parse_source(args)


def test_parse_requires_media_download_when_rights_basis_is_supplied() -> None:
    parser = cli_module._build_parser()
    args = parser.parse_args(
        [
            "parse",
            "https://www.youtube.com/watch?v=abc123",
            "--rights-basis",
            "user-owned",
        ]
    )

    with pytest.raises(ValueError, match="requires --allow-media-download"):
        asyncio.run(cli_module._run_parse(args, trace_id="test-trace"))


def test_parse_rejects_media_download_authorization_for_plain_http() -> None:
    parser = cli_module._build_parser()
    args = parser.parse_args(
        [
            "parse",
            "https://example.com/report.pdf",
            "--allow-media-download",
            "--rights-basis",
            "licensed",
        ]
    )

    with pytest.raises(ValueError, match="only supported by the YouTube"):
        asyncio.run(cli_module._run_parse(args, trace_id="test-trace"))


def _fake_parse_result(workspace: Path, *, task_id: str | None = None) -> dict:
    output = workspace / "document_parse" / "result.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("native FileX CLI\n", encoding="utf-8")
    document = output.with_suffix(".document.json")
    document.write_text(
        json.dumps(
            {
                "schema_version": "filex-document-ir-v2",
                "coordinate_system": "pixel_top_left_xyxy",
                "pages": [
                    {
                        "page_index": 0,
                        "width": 100,
                        "height": 100,
                        "elements": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return {
        "success": True,
        **({"task_id": task_id} if task_id else {}),
        "file_path": str(output.relative_to(workspace)),
        "document_file_path": str(document.relative_to(workspace)),
        "metrics": {"provider": "python_docx", "provider_version": "1"},
    }


def test_native_cli_exports_parse_output_bundle_with_stable_task_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "input.docx"
    source.write_bytes(b"docx fixture")
    artifacts = tmp_path / "artifacts"

    class FakeService:
        async def parse(self, **_kwargs):
            return _fake_parse_result(tmp_path)

    monkeypatch.setattr(cli_module, "FS_WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(cli_module, "DocumentParseService", FakeService)
    args = cli_module._build_parser().parse_args(
        [
            "parse",
            "--workspace-path",
            str(source),
            "--artifacts-dir",
            str(artifacts),
            "--layout-format",
            "parse-output",
        ]
    )

    result = asyncio.run(cli_module._run_parse(args, trace_id="stable-trace"))

    receipt = json.loads((artifacts / "result.json").read_bytes())
    assert result["artifact_result"] == str(artifacts / "result.json")
    assert receipt["schema_version"] == "filex.artifact-bundle/v1"
    assert receipt["filex"]["task_id"] == "stable-trace"
    assert receipt["filex_provenance"]["task_id"] == "stable-trace"
    assert receipt["filex_provenance"]["exporter"] == "filex-cli"


def test_failed_native_parse_removes_stale_receipt_but_keeps_diagnostics(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "input.pdf"
    source.write_bytes(b"%PDF fixture")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "result.json").write_text('{"status":"succeeded"}\n')
    (artifacts / "layout.json").write_text('{"old":true}\n')

    class FailingService:
        async def parse(self, **_kwargs):
            raise RuntimeError("provider failed")

    monkeypatch.setattr(cli_module, "FS_WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(cli_module, "DocumentParseService", FailingService)
    args = cli_module._build_parser().parse_args(
        ["parse", str(source), "--artifacts-dir", str(artifacts)]
    )

    with pytest.raises(RuntimeError, match="provider failed"):
        asyncio.run(cli_module._run_parse(args, trace_id="failed-trace"))

    assert not (artifacts / "result.json").exists()
    assert (artifacts / "layout.json").is_file()


def test_native_cli_selects_layout_capable_image_provider_generically(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "input.png"
    source.write_bytes(b"png fixture")
    captured = {}

    class FakeService:
        async def parse(self, **kwargs):
            captured.update(kwargs)
            result = _fake_parse_result(tmp_path, task_id="image-task")
            result["metrics"] = {
                "provider": "paddle_ocr",
                "provider_version": "v1.6",
            }
            return result

    monkeypatch.setattr(cli_module, "FS_WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(cli_module, "DocumentParseService", FakeService)
    args = cli_module._build_parser().parse_args(
        [
            "parse",
            str(source),
            "--artifacts-dir",
            str(tmp_path / "artifacts"),
            "--layout-format",
            "parse-output",
        ]
    )

    asyncio.run(cli_module._run_parse(args, trace_id="image-trace"))

    assert captured["env_content"]["filex_parse_provider"] == "paddle_ocr"
