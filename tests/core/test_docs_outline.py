from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_outline_includes_generated_home_page_in_nav() -> None:
    subprocess.run(
        [sys.executable, "docs/outline.py"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    config = yaml.safe_load((REPO_ROOT / "mkdocs.yml").read_text(encoding="utf-8"))

    assert {"Home": "index.md"} in config["nav"]
