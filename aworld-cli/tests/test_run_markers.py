from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from aworld_cli.top_level_commands.run_cmd import _write_outcome_sidecar


def test_final_markers_follow_buffered_stdout_in_merged_pipe() -> None:
    source_root = Path(__file__).resolve().parents[1] / "src"
    env = os.environ.copy()
    env.pop("PYTHONUNBUFFERED", None)
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(source_root), env.get("PYTHONPATH")))
    )
    script = """
from aworld_cli.top_level_commands.run_cmd import _write_final_markers

print("EARLIER_STDOUT", end="")
_write_final_markers(["AWORLD_ATIF_EXPORT={}", "AWORLD_RUN_OUTCOME={}"])
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=5,
        check=True,
    )

    assert completed.stdout.splitlines() == [
        "EARLIER_STDOUT",
        "AWORLD_ATIF_EXPORT={}",
        "AWORLD_RUN_OUTCOME={}",
    ]


def test_outcome_sidecar_is_atomic_and_content_free(tmp_path: Path) -> None:
    destination = tmp_path / "outcome.json"
    payload = {
        "schema_version": "aworld.run.outcome.v1",
        "semantic_status": "succeeded",
    }

    _write_outcome_sidecar(str(destination), payload)

    assert destination.read_text(encoding="utf-8").endswith("\n")
    assert __import__("json").loads(destination.read_text(encoding="utf-8")) == payload
    assert not list(tmp_path.glob(".outcome.json.*.tmp"))


def test_outcome_sidecar_replaces_symlink_without_following_it(tmp_path: Path) -> None:
    protected = tmp_path / "protected.json"
    protected.write_text('{"keep": true}\n', encoding="utf-8")
    destination = tmp_path / "outcome.json"
    destination.symlink_to(protected)

    _write_outcome_sidecar(
        str(destination),
        {"schema_version": "aworld.run.outcome.v1"},
    )

    assert destination.is_symlink() is False
    assert protected.read_text(encoding="utf-8") == '{"keep": true}\n'
