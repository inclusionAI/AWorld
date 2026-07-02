from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_cli.acp.validation import REQUIRED_PHASE1_CASE_IDS, build_summary


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_build_summary_is_machine_checkable() -> None:
    summary = build_summary(
        cases=[
            {"id": "initialize_handshake", "ok": True},
            {"id": "new_session_usable", "ok": False, "detail": "boom"},
        ]
    )

    assert summary["ok"] is False
    assert summary["summary"]["passed"] == 1
    assert summary["summary"]["failed"] == 1
    assert summary["cases"][1]["id"] == "new_session_usable"


def test_acp_self_test_reports_required_phase1_case_ids() -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "aworld-cli" / "src") + ":" + str(REPO_ROOT)

    proc = subprocess.run(
        [sys.executable, "-m", "aworld_cli.main", "--no-banner", "acp", "self-test"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    payload = json.loads(proc.stdout.strip())
    case_ids = [case["id"] for case in payload["cases"]]

    for case_id in REQUIRED_PHASE1_CASE_IDS:
        assert case_id in case_ids
