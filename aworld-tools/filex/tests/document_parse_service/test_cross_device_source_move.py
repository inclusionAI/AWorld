"""Regression coverage for moving downloaded files across mounted devices."""

from __future__ import annotations

import errno
from pathlib import Path

from document_parse_service.service import DocumentParseService


def test_move_file_falls_back_to_copy_across_devices(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "download" / "source.pdf"
    target = tmp_path / "workspace" / "source.pdf"
    source.parent.mkdir()
    target.parent.mkdir()
    source.write_bytes(b"pdf-content")

    def _raise_cross_device_error(source_path: str, destination_path: str) -> None:
        raise OSError(
            errno.EXDEV, "Invalid cross-device link", source_path, destination_path
        )

    monkeypatch.setattr(
        "document_parse_service.service.shutil.os.rename", _raise_cross_device_error
    )

    result = DocumentParseService._move_file(object(), source, target)

    assert result == target
    assert target.read_bytes() == b"pdf-content"
    assert not source.exists()


def test_remote_source_is_copied_per_task_without_consuming_shared_download(
    tmp_path: Path,
) -> None:
    shared_source = tmp_path / "download-cache" / "source.pdf"
    shared_source.parent.mkdir()
    shared_source.write_bytes(b"shared-pdf-content")

    service = DocumentParseService(workspace_root=tmp_path / "workspace")
    first_path = service._copy_file(
        shared_source,
        service._output_root / "provider-liteparse" / "source" / shared_source.name,
    )
    second_path = service._copy_file(
        shared_source,
        service._output_root / "provider-paddle-ocr" / "source" / shared_source.name,
    )

    assert shared_source.read_bytes() == b"shared-pdf-content"
    assert first_path != second_path
    assert first_path.read_bytes() == b"shared-pdf-content"
    assert second_path.read_bytes() == b"shared-pdf-content"
