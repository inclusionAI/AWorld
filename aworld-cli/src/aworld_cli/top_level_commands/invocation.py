from __future__ import annotations

import argparse
from collections.abc import Collection, Sequence


def find_command_index(
    argv: Sequence[str],
    *,
    command_name: str,
    options_with_values: Collection[str],
) -> int | None:
    index = 1 if argv else 0

    while index < len(argv):
        token = argv[index]
        if token in options_with_values:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        if token == command_name:
            return index
        return None

    return None


def parse_command_invocation_args(
    argv: Sequence[str],
    *,
    command_name: str,
    parser: argparse.ArgumentParser,
    options_with_values: Collection[str],
) -> argparse.Namespace:
    command_index = find_command_index(
        argv,
        command_name=command_name,
        options_with_values=options_with_values,
    )
    if command_index is None:
        return parser.parse_args([])

    parse_argv = list(argv[1:command_index]) + list(argv[command_index + 1 :])
    args, _ = parser.parse_known_args(parse_argv)
    return args
