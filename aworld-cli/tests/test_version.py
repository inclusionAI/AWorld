from __future__ import annotations

from pathlib import Path

import pytest

import aworld_cli
from aworld_cli.main import build_parser


def test_version_flag_uses_distribution_version(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(["--version"])

    assert exc_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"aworld-cli {aworld_cli.__version__}"


def test_source_checkout_ignores_unrelated_installed_metadata(monkeypatch) -> None:
    class StaleDistribution:
        version = "0.1.0"

        @staticmethod
        def locate_file(path: str) -> Path:
            return Path("/unrelated/site-packages") / path

    monkeypatch.setattr(aworld_cli, "distribution", lambda _name: StaleDistribution())

    assert aworld_cli._resolve_version() == "1.0.0a4"


def test_installed_cli_uses_its_own_distribution_metadata(monkeypatch) -> None:
    class CurrentDistribution:
        version = "0.1.4"

        @staticmethod
        def locate_file(_path: str) -> Path:
            return Path(aworld_cli.__file__)

    monkeypatch.setattr(aworld_cli, "_is_source_checkout", lambda: False)
    monkeypatch.setattr(aworld_cli, "distribution", lambda _name: CurrentDistribution())

    assert aworld_cli._resolve_version() == "0.1.4"
