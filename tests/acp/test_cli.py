from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_cli.acp.cli import build_acp_parser, find_acp_command_index


def test_find_acp_command_index_detects_top_level_command() -> None:
    assert find_acp_command_index(["aworld-cli", "acp"]) == 1
    assert find_acp_command_index(["aworld-cli", "--no-banner", "acp", "self-test"]) == 2


def test_build_acp_parser_defaults_to_serve() -> None:
    parser = build_acp_parser()

    args = parser.parse_args([])

    assert args.acp_action == "serve"


def test_build_acp_parser_supports_self_test() -> None:
    parser = build_acp_parser()

    args = parser.parse_args(["self-test"])

    assert args.acp_action == "self-test"
