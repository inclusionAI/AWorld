from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld_cli.evaluator_runtime import available_evaluator_suites, run_evaluator_cli


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


def test_run_evaluator_cli_writes_default_report_when_output_is_omitted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "artifact.txt"
    target.write_text("artifact", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    async def fake_run_evaluation_flow(flow):
        return {
            "report_version": 1,
            "suite_id": "app-evaluator",
            "judge_backend": {"backend_id": "stub-agent"},
            "summary": {"app-evaluator": {"score": {"mean": 0.9}}},
            "results": [],
            "gate": {"status": "pass", "metric_name": "score", "value": 0.9},
            "approval": {"required": False, "resolved": False, "approved": None},
        }

    monkeypatch.setattr("aworld_cli.evaluator_runtime.run_evaluation_flow", fake_run_evaluation_flow)

    report = run_evaluator_cli(target=str(target))

    report_path = Path(report["report_path"])
    persisted = json.loads(report_path.read_text(encoding="utf-8"))

    assert report_path.exists()
    assert report_path.parent == tmp_path / ".aworld" / "evaluations"
    assert persisted["suite_id"] == "app-evaluator"


def test_available_evaluator_suites_lists_builtin_suite() -> None:
    suites = available_evaluator_suites()

    assert "app-evaluator" in suites


def test_run_evaluator_cli_marks_image_targets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "artifact.png"
    target.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aA1EAAAAASUVORK5CYII="
        )
    )

    async def fake_run_evaluation_flow(flow):
        assert flow.target["target_kind"] == "image"
        return {
            "report_version": 1,
            "suite_id": "app-evaluator",
            "judge_backend": {"backend_id": "stub-agent"},
            "summary": {"app-evaluator": {"score": {"mean": 0.9}}},
            "results": [],
            "gate": {"status": "pass", "metric_name": "score", "value": 0.9},
            "approval": {"required": False, "resolved": False, "approved": None},
        }

    monkeypatch.setattr("aworld_cli.evaluator_runtime.run_evaluation_flow", fake_run_evaluation_flow)

    report = run_evaluator_cli(target=str(target))

    assert report["suite_id"] == "app-evaluator"
