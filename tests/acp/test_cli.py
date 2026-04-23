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


def test_build_acp_parser_supports_describe_validation() -> None:
    parser = build_acp_parser()

    args = parser.parse_args(["describe-validation"])

    assert args.acp_action == "describe-validation"


def test_build_acp_parser_supports_render_validation_config() -> None:
    parser = build_acp_parser()

    args = parser.parse_args(
        [
            "render-validation-config",
            "--topology",
            "distributed",
            "--expand-placeholders",
            "--output-file",
            "/tmp/rendered.json",
            "--env",
            "AWORLD_WORKER_WORKSPACE=/tmp/worker",
        ]
    )

    assert args.acp_action == "render-validation-config"
    assert args.topology == "distributed"
    assert args.expand_placeholders is True
    assert args.output_file == "/tmp/rendered.json"
    assert args.env == ["AWORLD_WORKER_WORKSPACE=/tmp/worker"]


def test_build_acp_parser_supports_validate_stdio_host() -> None:
    parser = build_acp_parser()

    args = parser.parse_args(
        [
            "validate-stdio-host",
            "--config-file",
            "/tmp/acp-validate.json",
            "--topology",
            "same-host",
            "--command",
            "python -m demo_host",
            "--cwd",
            "/tmp/demo",
            "--profile",
            "self-test",
            "--session-params-json",
            '{"cwd":"/tmp/session","mcpServers":["demo"]}',
            "--startup-timeout-seconds",
            "1.5",
            "--startup-retries",
            "2",
            "--env-json",
            '{"FROM_JSON":"1"}',
            "--env",
            "FOO=bar",
            "--env",
            "BAZ=qux",
        ]
    )

    assert args.acp_action == "validate-stdio-host"
    assert args.config_file == "/tmp/acp-validate.json"
    assert args.topology == "same-host"
    assert args.command == "python -m demo_host"
    assert args.cwd == "/tmp/demo"
    assert args.profile == "self-test"
    assert args.session_params_json == '{"cwd":"/tmp/session","mcpServers":["demo"]}'
    assert args.startup_timeout_seconds == 1.5
    assert args.startup_retries == 2
    assert args.env_json == '{"FROM_JSON":"1"}'
    assert args.env == ["FOO=bar", "BAZ=qux"]
