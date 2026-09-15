from __future__ import annotations

import asyncio
import base64
import inspect
import json
import os
from pathlib import Path
import time
import tracemalloc

import pytest

from aworld.sandbox.tool_servers.filesystem.src import main as filesystem
from aworld.sandbox.config.templates import get_server_env


def _json(result) -> dict:
    return json.loads(result.text)


def test_explicit_limit_configuration_is_forwarded_to_stdio_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWORLD_FILESYSTEM_MAX_READ_BYTES", "2097152")
    assert get_server_env()["AWORLD_FILESYSTEM_MAX_READ_BYTES"] == "2097152"


@pytest.mark.asyncio
async def test_small_file_contract_and_existing_parameters_remain_compatible(tmp_path: Path) -> None:
    await filesystem.set_allowed_directories([str(tmp_path)])
    text_path = tmp_path / "small.txt"
    text_path.write_text("alpha\nbeta\n", encoding="utf-8")

    assert _json(
        await filesystem.read_file(
            None, str(text_path), head=None, tail=None, output="text"
        )
    ) == {"type": "text", "content": "alpha\nbeta\n"}
    assert _json(
        await filesystem.read_file(
            None, str(text_path), head=1, tail=None, output="text"
        )
    ) == {"type": "text", "content": "alpha"}
    assert _json(await filesystem.download_file(None, str(text_path))) == {
        "type": "base64",
        "base64": base64.b64encode(b"alpha\nbeta\n").decode("ascii"),
        "mimeType": "text/plain",
        "fileName": "small.txt",
    }

    signature = inspect.signature(filesystem.read_file)
    assert list(signature.parameters)[:5] == ["ctx", "path", "head", "tail", "output"]


@pytest.mark.asyncio
async def test_large_single_line_and_binary_download_are_bounded_and_observable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AWORLD_FILESYSTEM_MAX_READ_BYTES", "4096")
    monkeypatch.setenv("AWORLD_FILESYSTEM_MAX_LINE_BYTES", "4096")
    monkeypatch.setenv("AWORLD_FILESYSTEM_MAX_SCAN_BYTES", "65536")
    monkeypatch.setenv("AWORLD_FILESYSTEM_MAX_BINARY_BYTES", "4096")
    await filesystem.set_allowed_directories([str(tmp_path)])
    path = tmp_path / "large.bin"
    original = b"a" * (8 * 1024 * 1024)
    path.write_bytes(original)

    tracemalloc.start()
    text_payload = _json(
        await filesystem.read_file(
            None, str(path), head=None, tail=1, output="text"
        )
    )
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert len(text_payload["content"].encode("utf-8")) <= 4096
    assert text_payload["complete"] is False
    assert text_payload["truncationReason"] in {"read_bytes", "scan_bytes", "line_bytes"}
    assert peak < 4 * 1024 * 1024

    chunks: list[bytes] = []
    offset = 0
    while offset < len(original):
        payload = _json(await filesystem.download_file(None, str(path), offset=offset))
        chunks.append(base64.b64decode(payload["base64"]))
        offset = payload.get("nextOffset", len(original))
    assert b"".join(chunks) == original


@pytest.mark.asyncio
async def test_directory_operations_are_capped_and_do_not_follow_symlinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AWORLD_FILESYSTEM_MAX_LIST_ENTRIES", "2")
    await filesystem.set_allowed_directories([str(tmp_path)])
    for name in ("a.txt", "b.txt", "c.txt"):
        (tmp_path / name).write_text(name, encoding="utf-8")
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    try:
        (tmp_path / "loop").symlink_to(tmp_path, target_is_directory=True)
        (tmp_path / "outside").symlink_to(outside, target_is_directory=True)

        listing = (await filesystem.list_directory(None, str(tmp_path))).text
        assert "[TRUNCATED] reason=list_entries; limit=2" in listing

        matches = (
            await filesystem.search_files(None, str(tmp_path), "*.txt", [])
        ).text
        assert "secret.txt" not in matches
        assert matches.count("a.txt") == 1
        assert "/loop/" not in matches
    finally:
        (outside / "secret.txt").unlink()
        outside.rmdir()


@pytest.mark.asyncio
async def test_content_search_timeout_kills_pathological_regex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AWORLD_FILESYSTEM_SEARCH_TIMEOUT_SECONDS", "0.1")
    await filesystem.set_allowed_directories([str(tmp_path)])
    path = tmp_path / "regex.txt"
    path.write_text("a" * 31 + "!\n", encoding="utf-8")

    started = time.monotonic()
    result = await asyncio.wait_for(
        filesystem.search_content(
            None,
            str(path),
            "(a+)+$",
            max_matches=None,
            max_per_file=None,
            before=0,
            after=0,
        ),
        timeout=2,
    )
    assert time.monotonic() - started < 1.5
    assert "[TRUNCATED] reason=timeout; limit=0.1" in result.text


@pytest.mark.asyncio
async def test_read_rejects_special_files_and_upload_preserves_server_import_semantics(
    tmp_path: Path,
) -> None:
    await filesystem.set_allowed_directories([str(tmp_path)])
    outside = tmp_path.parent / f"{tmp_path.name}-source.txt"
    outside.write_text("outside", encoding="utf-8")
    try:
        await filesystem.upload_file(None, str(outside), str(tmp_path / "copy.txt"))
        assert (tmp_path / "copy.txt").read_text(encoding="utf-8") == "outside"

        if hasattr(os, "mkfifo"):
            fifo = tmp_path / "pipe"
            os.mkfifo(fifo)
            with pytest.raises(ValueError, match="regular file"):
                await asyncio.wait_for(
                    filesystem.read_file(
                        None, str(fifo), head=None, tail=None, output="text"
                    ),
                    timeout=1,
                )
    finally:
        outside.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_parse_failure_is_an_mcp_error_not_nested_false_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await filesystem.set_allowed_directories([str(tmp_path)])
    source = tmp_path.parent / f"{tmp_path.name}-parse-source.txt"
    source.write_text("hello", encoding="utf-8")

    async def fail_parse(*args, **kwargs):
        raise NotImplementedError("backend unavailable")

    monkeypatch.setattr(filesystem, "parse_verify_file_type", lambda *args: True)
    monkeypatch.setattr(filesystem, "parse_file_to_path", fail_parse)
    try:
        with pytest.raises(RuntimeError, match="backend unavailable"):
            await filesystem.parse_file(None, str(source), "txt", None)
    finally:
        source.unlink(missing_ok=True)
