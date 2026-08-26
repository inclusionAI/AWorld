from __future__ import annotations

import asyncio
import io

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
