from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld_cli import main as main_module


def test_registry_registers_builtin_evaluator_command() -> None:
    registry = main_module._build_top_level_command_registry()

    command = registry.get("evaluator")

    assert command is not None
    assert command.name == "evaluator"


def test_maybe_dispatch_top_level_command_runs_evaluator_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / "artifact.txt"
    target.write_text("artifact", encoding="utf-8")

    def fake_run_evaluator_cli(**kwargs):
        assert kwargs["target"] == str(target)
        return {
            "suite_id": "app-evaluator",
            "gate": {"status": "pass"},
            "summary": {"app-evaluator": {"score": {"mean": 0.9}}},
            "results": [],
        }

    monkeypatch.setattr(
        "aworld_cli.top_level_commands.evaluator_cmd.run_evaluator_cli",
        fake_run_evaluator_cli,
    )

    handled = main_module._maybe_dispatch_top_level_command(
        ["aworld-cli", "evaluator", "--target", str(target)]
    )
    output = capsys.readouterr().out

    assert handled is True
    assert "app-evaluator" in output
    assert "pass" in output
