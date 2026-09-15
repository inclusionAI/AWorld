import asyncio
import os
import time
from pathlib import Path

import pytest

from aworld.experimental.cast import ACast
from aworld.experimental.cast.searchers.engine import SearchParams
from aworld.experimental.cast.searchers.searchers import (
    GlobSearcher,
    GrepSearcher,
    ReadSearcher,
)
from aworld.experimental.cast.searchers.utils import (
    PygrepSearcher,
    RipgrepSearcher,
    SearchTimeoutError,
)


@pytest.mark.asyncio
async def test_read_is_bounded_and_reports_single_long_line_truncation(tmp_path: Path):
    large = tmp_path / "large.txt"
    large.write_bytes(b"x" * (9 * 1024 * 1024))

    result = await ReadSearcher(tmp_path).search(
        SearchParams(path="large.txt", limit=1)
    )

    assert result.truncated is True
    assert result.metadata["total_lines_exact"] is False
    assert result.metadata["truncation_reason"] == "scan_byte_budget"
    assert result.matches[0]["bytes_scanned"] == 8 * 1024 * 1024
    assert len(result.output.encode("utf-8")) < 60 * 1024


@pytest.mark.asyncio
async def test_absolute_and_symlink_escape_are_rejected(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (workspace / "escape.txt").symlink_to(outside)
    searcher = ReadSearcher(workspace)

    with pytest.raises(PermissionError, match="outside the configured search root"):
        await searcher.search(SearchParams(path=str(outside)))
    with pytest.raises(PermissionError, match="outside the configured search root"):
        await searcher.search(SearchParams(path="escape.txt"))
    with pytest.raises(PermissionError, match="outside the configured search root"):
        await GrepSearcher(workspace).search(
            SearchParams(pattern="secret", path=str(outside))
        )
    with pytest.raises(PermissionError, match="outside the configured search root"):
        await GlobSearcher(workspace).search(
            SearchParams(pattern="*.txt", path=str(tmp_path))
        )


@pytest.mark.asyncio
async def test_glob_follow_symlinks_deduplicates_directory_cycles(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "only.py").write_text("pass\n", encoding="utf-8")
    (tmp_path / "src" / "loop").symlink_to(tmp_path, target_is_directory=True)

    result = await GlobSearcher(tmp_path).search(
        SearchParams(
            pattern="*.py",
            path=".",
            follow_symlinks=True,
            max_results=100,
        )
    )

    assert [Path(match["path"]).name for match in result.matches] == ["only.py"]


@pytest.mark.asyncio
async def test_pygrep_does_not_retain_an_unbounded_matching_line(tmp_path: Path):
    target = tmp_path / "large.txt"
    target.write_bytes(b"needle" + b"x" * (4 * 1024 * 1024))

    results = await PygrepSearcher().search(
        "needle",
        str(tmp_path),
        max_count=10,
        max_scan_bytes=1024 * 1024,
        max_line_length=100,
    )

    assert len(results) == 1
    assert results.truncated is True
    assert len(results[0].line_text) <= 103


@pytest.mark.asyncio
async def test_pygrep_pathological_regex_is_preempted(tmp_path: Path):
    target = tmp_path / "pathological.txt"
    target.write_text("a" * 50 + "X\n", encoding="utf-8")
    started = time.monotonic()

    with pytest.raises(SearchTimeoutError, match="timed out"):
        await PygrepSearcher().search(
            "(a|aa)+$",
            str(target),
            timeout_seconds=0.2,
        )

    assert time.monotonic() - started < 2


@pytest.mark.asyncio
async def test_pygrep_cancellation_reaps_pathological_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    target = tmp_path / "cancelled-regex.txt"
    target.write_text("a" * 50 + "X\n", encoding="utf-8")
    created = []
    original_create = asyncio.create_subprocess_exec

    async def recording_create(*args, **kwargs):
        process = await original_create(*args, **kwargs)
        created.append(process)
        return process

    monkeypatch.setattr("asyncio.create_subprocess_exec", recording_create)
    task = asyncio.create_task(
        PygrepSearcher().search(
            "(a|aa)+$",
            str(target),
            timeout_seconds=10,
        )
    )
    await asyncio.sleep(0.1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(created) == 1
    assert created[0].returncode is not None


@pytest.mark.asyncio
async def test_acast_uses_one_canonical_root_for_all_searchers(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    acast = ACast()

    acast.set_search_root_path(workspace)

    assert acast.search_engine.root_path == workspace.resolve()
    assert {
        searcher.root_path for searcher in acast.search_engine.searchers.values()
    } == {workspace.resolve()}


@pytest.mark.asyncio
async def test_ripgrep_timeout_terminates_the_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_rg = fake_bin / "rg"
    fake_rg.write_text(
        "#!/usr/bin/env python3\nimport time\ntime.sleep(30)\n",
        encoding="utf-8",
    )
    fake_rg.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}")
    searcher = RipgrepSearcher()
    started = time.monotonic()

    with pytest.raises(TimeoutError, match="timed out"):
        await searcher.search("needle", str(tmp_path), timeout_seconds=0.05)

    assert time.monotonic() - started < 2


@pytest.mark.asyncio
async def test_ripgrep_capture_budget_is_observable_and_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    target = tmp_path / "target.txt"
    target.write_text("needle\n", encoding="utf-8")
    event = {
        "type": "match",
        "data": {
            "path": {"text": str(target)},
            "lines": {"text": "needle\n"},
            "line_number": 1,
            "absolute_offset": 0,
            "submatches": [{"start": 0, "end": 6, "match": {"text": "needle"}}],
        },
    }
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_rg = fake_bin / "rg"
    fake_rg.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        f"event = {event!r}\n"
        "for _ in range(10000):\n"
        "    print(json.dumps(event), flush=True)\n",
        encoding="utf-8",
    )
    fake_rg.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}")

    results = await RipgrepSearcher().search(
        "needle",
        str(tmp_path),
        max_count=10000,
        max_output_bytes=1024,
        timeout_seconds=2,
    )

    assert results.truncated is True
    assert results.truncation_reason == "byte_budget"
    assert len(results) < 20
