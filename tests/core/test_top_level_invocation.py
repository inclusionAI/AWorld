import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))


def test_parse_command_invocation_args_merges_prefix_and_suffix_options() -> None:
    from aworld_cli.top_level_commands.invocation import parse_command_invocation_args

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--agent-dir", action="append")

    args = parse_command_invocation_args(
        ["aworld-cli", "--env-file", "custom.env", "interactive", "--agent-dir", "./agents"],
        command_name="interactive",
        parser=parser,
        options_with_values={"--env-file", "--agent-dir"},
    )

    assert args.env_file == "custom.env"
    assert args.agent_dir == ["./agents"]


def test_find_command_index_ignores_option_values() -> None:
    from aworld_cli.top_level_commands.invocation import find_command_index

    index = find_command_index(
        ["aworld-cli", "--agent", "interactive"],
        command_name="interactive",
        options_with_values={"--agent"},
    )

    assert index is None

