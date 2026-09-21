from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


_SENTINEL = "AWORLD_TEST_DOTENV_SENTINEL"


def _import_aworld(tmp_path: Path, *, disable_auto_dotenv: bool) -> dict[str, str | None]:
    (tmp_path / ".env").write_text(f"{_SENTINEL}=from-task-dotenv\n", encoding="utf-8")
    env = os.environ.copy()
    env.pop(_SENTINEL, None)
    if disable_auto_dotenv:
        env["AWORLD_DISABLE_AUTO_DOTENV"] = "1"
    else:
        env.pop("AWORLD_DISABLE_AUTO_DOTENV", None)
    source_root = Path(__file__).resolve().parents[2]
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(source_root), env.get("PYTHONPATH")))
    )
    script = (
        "import json, os, aworld; "
        f"print(json.dumps({{{_SENTINEL!r}: os.environ.get({_SENTINEL!r})}}))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=5,
        check=True,
    )
    return json.loads(completed.stdout)


def test_library_import_preserves_default_dotenv_compatibility(tmp_path: Path) -> None:
    assert _import_aworld(tmp_path, disable_auto_dotenv=False) == {
        _SENTINEL: "from-task-dotenv"
    }


def test_embedding_runtime_can_disable_import_time_dotenv(tmp_path: Path) -> None:
    assert _import_aworld(tmp_path, disable_auto_dotenv=True) == {_SENTINEL: None}
