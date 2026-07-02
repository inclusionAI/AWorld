from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _cli_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "aworld-cli" / "src") + ":" + str(REPO_ROOT)
    return env


def test_top_level_help_lists_acp_command() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "aworld_cli.main", "--help"],
        cwd=str(REPO_ROOT),
        env=_cli_env(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0
    assert "acp" in proc.stdout


def test_chinese_help_lists_acp_command() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "aworld_cli.main", "--zh"],
        cwd=str(REPO_ROOT),
        env=_cli_env(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0
    assert "acp" in proc.stdout
