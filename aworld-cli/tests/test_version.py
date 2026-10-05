from __future__ import annotations

import pytest

import aworld_cli
from aworld_cli.main import build_parser


def test_version_flag_uses_distribution_version(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(["--version"])

    assert exc_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"aworld-cli {aworld_cli.__version__}"
