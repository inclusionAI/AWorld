from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld_cli import main as main_module
from aworld_cli.core.top_level_command_system import TopLevelCommandContext
from aworld_cli.top_level_commands.evaluator_cmd import EvaluatorTopLevelCommand


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


def test_evaluator_command_returns_nonzero_for_failed_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "aworld_cli.top_level_commands.evaluator_cmd.run_evaluator_cli",
        lambda **kwargs: {
            "suite_id": "app-evaluator",
            "gate": {"status": "fail", "value": 0.3},
            "approval": {"required": False, "resolved": False, "approved": None},
        },
    )

    exit_code = EvaluatorTopLevelCommand().run(
        SimpleNamespace(
            target="artifact.txt",
            suite=None,
            output=None,
            interactive_approval=False,
        ),
        TopLevelCommandContext(cwd="/tmp"),
    )

    assert exit_code == 2


def test_evaluator_command_returns_nonzero_for_unresolved_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "aworld_cli.top_level_commands.evaluator_cmd.run_evaluator_cli",
        lambda **kwargs: {
            "suite_id": "app-evaluator",
            "gate": {"status": "needs_approval", "value": 0.7},
            "approval": {"required": True, "resolved": False, "approved": None},
        },
    )

    exit_code = EvaluatorTopLevelCommand().run(
        SimpleNamespace(
            target="artifact.txt",
            suite=None,
            output=None,
            interactive_approval=False,
        ),
        TopLevelCommandContext(cwd="/tmp"),
    )

    assert exit_code == 3


def test_evaluator_command_lists_available_suites(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = EvaluatorTopLevelCommand().run(
        SimpleNamespace(
            target=None,
            suite=None,
            output=None,
            interactive_approval=False,
            list_suites=True,
        ),
        TopLevelCommandContext(cwd="/tmp"),
    )

    output = capsys.readouterr().out

    assert exit_code == 0
    assert "app-evaluator" in output


def test_evaluator_command_lists_target_matching_suites_and_default(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / "artifact.txt"
    target.write_text("artifact", encoding="utf-8")

    exit_code = EvaluatorTopLevelCommand().run(
        SimpleNamespace(
            target=str(target),
            suite=None,
            output=None,
            interactive_approval=False,
            list_suites=True,
        ),
        TopLevelCommandContext(cwd="/tmp"),
    )

    output = capsys.readouterr().out

    assert exit_code == 0
    assert "Available evaluator suites for target:" in output
    assert "Default suite: app-evaluator" in output


def test_evaluator_command_returns_usage_error_without_target(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = EvaluatorTopLevelCommand().run(
        SimpleNamespace(
            target=None,
            suite=None,
            output=None,
            interactive_approval=False,
            list_suites=False,
        ),
        TopLevelCommandContext(cwd="/tmp"),
    )

    output = capsys.readouterr().out

    assert exit_code == 1
    assert "--target is required" in output
