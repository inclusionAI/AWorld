from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld_cli.evaluator_runtime import run_evaluator_cli


def test_run_evaluator_cli_persists_approval_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "artifact.txt"
    target.write_text("artifact", encoding="utf-8")
    output = tmp_path / "report.json"

    async def fake_run_evaluation_flow(flow):
        return {
            "report_version": 1,
            "suite_id": "app-evaluator",
            "judge_backend": {"backend_id": "stub-agent"},
            "summary": {"app-evaluator": {"score": {"mean": 0.7}}},
            "results": [],
            "gate": {"status": "needs_approval", "metric_name": "score", "value": 0.7},
            "approval": {"required": True, "resolved": False, "approved": None},
        }

    monkeypatch.setattr("aworld_cli.evaluator_runtime.run_evaluation_flow", fake_run_evaluation_flow)
    monkeypatch.setattr("builtins.input", lambda _: "y")

    report = run_evaluator_cli(
        target=str(target),
        interactive_approval=True,
        output=str(output),
    )

    persisted = json.loads(output.read_text(encoding="utf-8"))

    assert report["approval"]["resolved"] is True
    assert report["approval"]["approved"] is True
    assert persisted["approval"]["approved"] is True
    assert persisted["judge_backend"]["backend_id"] == "stub-agent"
