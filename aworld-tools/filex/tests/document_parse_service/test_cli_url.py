from __future__ import annotations

import io

import pytest

import document_parse_service.cli as cli_module


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


def test_download_url_saves_a_sanitized_file_inside_workspace(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cli_module, "FS_WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(
        cli_module,
        "urlopen",
        lambda _request, timeout: _FakeResponse(b"%PDF-1.7\n", url="https://cdn.example/report%202026.pdf"),
    )

    result = cli_module._download_url("https://example.com/download?id=1")

    assert result.is_relative_to(tmp_path)
    assert result.name == "report_2026.pdf"
    assert result.read_bytes() == b"%PDF-1.7\n"


@pytest.mark.parametrize("url", ["file:///tmp/report.pdf", "relative.pdf", "ftp://example.com/a.pdf"])
def test_download_url_rejects_non_http_sources(url: str) -> None:
    with pytest.raises(ValueError, match=r"HTTP\(S\)"):
        cli_module._download_url(url)


def test_download_url_enforces_stream_size_limit(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cli_module, "FS_WORKSPACE_ROOT", tmp_path)
    monkeypatch.setenv("FILEX_MAX_DOWNLOAD_BYTES", "4")
    monkeypatch.setattr(
        cli_module,
        "urlopen",
        lambda _request, timeout: _FakeResponse(b"12345", url="https://example.com/data.bin"),
    )

    with pytest.raises(ValueError, match="FILEX_MAX_DOWNLOAD_BYTES"):
        cli_module._download_url("https://example.com/data.bin")
