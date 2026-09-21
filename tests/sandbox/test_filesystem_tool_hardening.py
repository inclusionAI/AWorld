from __future__ import annotations

import asyncio
import base64
import inspect
import importlib
import json
import os
from pathlib import Path
import sys
import time
import tracemalloc
import zipfile

import pytest

from aworld.sandbox.tool_servers.filesystem.src import main as filesystem
from aworld.sandbox.config.templates import get_server_env


parse_module = importlib.import_module(
    "aworld.sandbox.tool_servers.filesystem.src.utils.document_processor.parse_to_path"
)
excel_module = importlib.import_module(
    "aworld.sandbox.tool_servers.filesystem.src.utils.document_processor.parsers.excel_parser"
)
file_ops_module = importlib.import_module(
    "aworld.sandbox.tool_servers.filesystem.src.utils.file_ops"
)


def _json(result) -> dict:
    return json.loads(result.text)


def test_explicit_limit_configuration_is_forwarded_to_stdio_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWORLD_FILESYSTEM_MAX_READ_BYTES", "2097152")
    monkeypatch.setenv("AWORLD_FILESYSTEM_MAX_COPY_BYTES", "67108864")
    monkeypatch.setenv("AWORLD_FILESYSTEM_COPY_TIMEOUT_SECONDS", "30")
    assert get_server_env()["AWORLD_FILESYSTEM_MAX_READ_BYTES"] == "2097152"
    assert get_server_env()["AWORLD_FILESYSTEM_MAX_COPY_BYTES"] == "67108864"
    assert get_server_env()["AWORLD_FILESYSTEM_COPY_TIMEOUT_SECONDS"] == "30"


def test_terminal_policy_configuration_is_forwarded_to_stdio_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWORLD_TERMINAL_CAPTURE_MAX_BYTES", "2097152")
    monkeypatch.setenv("AWORLD_TERMINAL_ARTIFACT_MAX_BYTES", "67108864")
    monkeypatch.setenv("AWORLD_TASK_DEADLINE_EPOCH_SECONDS", "1900000000")
    monkeypatch.setenv("AWORLD_TERMINAL_COMPLETION_RESERVE_SECONDS", "30")
    monkeypatch.setenv("TERMINAL_TIMEOUT", "90")

    env = get_server_env()

    assert env["AWORLD_TERMINAL_CAPTURE_MAX_BYTES"] == "2097152"
    assert env["AWORLD_TERMINAL_ARTIFACT_MAX_BYTES"] == "67108864"
    assert env["AWORLD_TASK_DEADLINE_EPOCH_SECONDS"] == "1900000000"
    assert env["AWORLD_TERMINAL_COMPLETION_RESERVE_SECONDS"] == "30"
    assert env["TERMINAL_TIMEOUT"] == "90"


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
async def test_upload_copy_limit_preserves_existing_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AWORLD_FILESYSTEM_MAX_COPY_BYTES", "4096")
    await filesystem.set_allowed_directories([str(tmp_path)])
    source = tmp_path.parent / f"{tmp_path.name}-large-source.bin"
    source.write_bytes(b"x" * 4097)
    target = tmp_path / "target.bin"
    target.write_bytes(b"original")
    try:
        with pytest.raises(ValueError, match="too large to copy safely"):
            await filesystem.upload_file(None, str(source), str(target))
        assert target.read_bytes() == b"original"
        assert not list(tmp_path.glob(".*.tmp"))
    finally:
        source.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_copy_timeout_kills_real_worker_and_preserves_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_python = tmp_path / "blocking-python"
    fake_python.write_text("#!/bin/sh\nsleep 30\n", encoding="utf-8")
    fake_python.chmod(0o755)
    monkeypatch.setattr(file_ops_module.sys, "executable", str(fake_python))
    monkeypatch.setenv("AWORLD_FILESYSTEM_COPY_TIMEOUT_SECONDS", "0.1")
    created = []
    original_create = asyncio.create_subprocess_exec

    async def recording_create(*args, **kwargs):
        process = await original_create(*args, **kwargs)
        created.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", recording_create)
    source = tmp_path / "source.bin"
    source.write_bytes(b"source")
    target = tmp_path / "target.bin"
    target.write_bytes(b"original")

    with pytest.raises(TimeoutError, match="timed out"):
        await file_ops_module.copy_file_binary(str(source), str(target))

    assert len(created) == 1
    assert created[0].returncode is not None
    assert target.read_bytes() == b"original"
    assert not list(tmp_path.glob(".target.bin.aworld-copy-*.tmp"))


@pytest.mark.asyncio
async def test_cancelled_copy_kills_real_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_python = tmp_path / "blocking-python"
    fake_python.write_text("#!/bin/sh\nsleep 30\n", encoding="utf-8")
    fake_python.chmod(0o755)
    monkeypatch.setattr(file_ops_module.sys, "executable", str(fake_python))
    created = []
    original_create = asyncio.create_subprocess_exec

    async def recording_create(*args, **kwargs):
        process = await original_create(*args, **kwargs)
        created.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", recording_create)
    source = tmp_path / "source.bin"
    source.write_bytes(b"source")
    task = asyncio.create_task(
        file_ops_module.copy_file_binary(str(source), str(tmp_path / "target.bin"))
    )
    for _ in range(100):
        if created:
            break
        await asyncio.sleep(0.01)
    assert created
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)

    assert created[0].returncode is not None
    assert not list(tmp_path.glob(".target.bin.aworld-copy-*.tmp"))


@pytest.mark.asyncio
async def test_copy_rejects_fifo_without_blocking_open(tmp_path: Path) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO is unavailable")
    fifo = tmp_path / "source.pipe"
    os.mkfifo(fifo)

    with pytest.raises(ValueError, match="regular file"):
        await asyncio.wait_for(
            file_ops_module.copy_file_binary(
                str(fifo), str(tmp_path / "target.bin")
            ),
            timeout=1,
        )


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


@pytest.mark.asyncio
async def test_document_archive_ratio_is_rejected_before_parsing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AWORLD_FILESYSTEM_MAX_ARCHIVE_COMPRESSION_RATIO", "10")
    source = tmp_path / "bomb.docx"
    with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", b"0" * (2 * 1024 * 1024))

    with pytest.raises(ValueError, match="compression ratio is unsafe"):
        await parse_module.parse_to_path(source, tmp_path / "output.md", "docx")

    assert not (tmp_path / "output.md").exists()


@pytest.mark.asyncio
async def test_document_archive_metadata_preflight_runs_in_bounded_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AWORLD_FILESYSTEM_MAX_ARCHIVE_MEMBERS", "8")
    source = tmp_path / "many-members.docx"
    with zipfile.ZipFile(source, "w") as archive:
        for index in range(9):
            archive.writestr(f"word/empty-{index}.xml", b"")

    def fail_if_called_in_mcp_process(*_args, **_kwargs):
        raise AssertionError("archive metadata expanded in the MCP process")

    monkeypatch.setattr(
        parse_module, "_validate_ooxml_archive", fail_if_called_in_mcp_process
    )

    with pytest.raises(ValueError, match="too many members"):
        await parse_module.parse_to_path(source, tmp_path / "output.md", "docx")

    assert not (tmp_path / "output.md").exists()


@pytest.mark.asyncio
async def test_markdown_parse_atomically_publishes_without_staging_files(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.md"
    destination = tmp_path / "output.md"
    source.write_text("# bounded\n", encoding="utf-8")

    result = await parse_module.parse_to_path(source, destination, "md")

    assert Path(result) == destination.resolve()
    assert destination.read_text(encoding="utf-8") == "# bounded\n"
    assert not list(tmp_path.glob(".output.md.aworld-parse-*"))


@pytest.mark.asyncio
async def test_extreme_workbook_dimensions_are_rejected_without_iteration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    monkeypatch.setenv("AWORLD_FILESYSTEM_MAX_WORKBOOK_CELLS", "1000")
    source = tmp_path / "extreme.xlsx"
    workbook = openpyxl.Workbook()
    workbook.active["XFD1048576"] = "edge"
    workbook.save(source)
    workbook.close()

    with pytest.raises(RuntimeError, match="Worksheet .* limit exceeded"):
        await parse_module.parse_to_path(source, tmp_path / "output.md", "xlsx")

    assert not (tmp_path / "output.md").exists()


@pytest.mark.asyncio
async def test_workbook_cell_cap_is_applied_inside_parser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeSheet:
        max_row = 100
        max_column = 100

    class FakeWorkbook:
        sheetnames = ["Huge"]
        closed = False

        def __getitem__(self, _name):
            return FakeSheet()

        def close(self):
            self.closed = True

    workbook = FakeWorkbook()
    monkeypatch.setattr(excel_module, "load_workbook", lambda *_a, **_k: workbook)
    monkeypatch.setenv("AWORLD_FILESYSTEM_MAX_WORKBOOK_CELLS", "1000")

    with pytest.raises(ValueError, match="Worksheet cell limit exceeded"):
        await excel_module.ExcelParser()._extract_xlsx_content(tmp_path / "fake.xlsx")

    assert workbook.closed is True


@pytest.mark.asyncio
async def test_document_parse_cancellation_reaps_worker_and_cleans_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.md"
    destination = tmp_path / "output.md"
    source.write_text("hello", encoding="utf-8")
    created = []
    original_create = asyncio.create_subprocess_exec

    async def sleeping_worker(*_args, **_kwargs):
        process = await original_create(
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=(os.name == "posix"),
        )
        created.append(process)
        return process

    monkeypatch.setattr(parse_module.asyncio, "create_subprocess_exec", sleeping_worker)
    task = asyncio.create_task(
        parse_module.parse_to_path(source, destination, "md")
    )
    await asyncio.sleep(0.1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(created) == 1
    assert created[0].returncode is not None
    assert not destination.exists()
    assert not list(tmp_path.glob(".output.md.aworld-parse-*"))
