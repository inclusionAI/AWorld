from __future__ import annotations

from pathlib import Path
import os
import sys
from types import ModuleType


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "aworld-cli" / "src"))

from aworld_cli import entrypoint


def test_rich_cli_is_default_and_mode_flag_is_removed(monkeypatch):
    observed = {}
    rich_module = ModuleType("aworld_cli.main")

    def rich_main():
        observed["argv"] = tuple(sys.argv[1:])
        observed["disable_console"] = os.environ.get("AWORLD_DISABLE_CONSOLE_LOG")
        return 17

    rich_module.main = rich_main
    monkeypatch.setitem(sys.modules, "aworld_cli.main", rich_module)

    assert entrypoint.main(["--rich", "--no-banner"]) == 17
    assert observed["argv"] == ("--no-banner",)
    assert observed["disable_console"] == "true"


def test_rich_cli_exposes_model_override_during_execution(monkeypatch):
    observed = {}
    rich_module = ModuleType("aworld_cli.main")

    def rich_main():
        observed["model"] = os.environ.get("AWORLD_CLI_MODEL_OVERRIDE")
        return 0

    rich_module.main = rich_main
    monkeypatch.setitem(sys.modules, "aworld_cli.main", rich_module)
    monkeypatch.delenv("AWORLD_CLI_MODEL_OVERRIDE", raising=False)

    assert entrypoint.main(["--model", "rich-model"]) == 0
    assert observed["model"] == "rich-model"
    assert "AWORLD_CLI_MODEL_OVERRIDE" not in os.environ


def test_minimal_flag_dispatches_to_session_run_cli(monkeypatch):
    observed = {}

    def kernel_main(arguments):
        observed["arguments"] = arguments
        return 23

    monkeypatch.setattr("aworld.cli.main.main", kernel_main)

    assert entrypoint.main(["--minimal", "--model", "fixture"]) == 23
    assert observed["arguments"] == ["--model", "fixture"]


def test_session_run_alias_selects_minimal_cli(monkeypatch):
    observed = {}

    def kernel_main(arguments):
        observed["arguments"] = arguments
        return 29

    monkeypatch.setattr("aworld.cli.main.main", kernel_main)

    assert entrypoint.main(["--session-run", "--demo"]) == 29
    assert observed["arguments"] == ["--demo"]


def test_thin_installation_falls_back_to_minimal_cli(monkeypatch, capsys):
    observed = {}
    real_import = __import__

    def importing(name, *args, **kwargs):
        if name == "aworld_cli.main":
            raise ModuleNotFoundError(
                "No module named 'aworld_cli.main'", name="aworld_cli.main"
            )
        return real_import(name, *args, **kwargs)

    def kernel_main(arguments):
        observed["arguments"] = arguments
        return 31

    monkeypatch.setattr("builtins.__import__", importing)
    monkeypatch.setattr("aworld.cli.main.main", kernel_main)

    assert entrypoint.main(["--demo"]) == 31
    assert observed["arguments"] == ["--demo"]
    assert "thin installation" in capsys.readouterr().err


def test_conflicting_mode_flags_are_rejected(capsys):
    assert entrypoint.main(["--rich", "--minimal"]) == 2
    assert "either --rich or --minimal" in capsys.readouterr().err
