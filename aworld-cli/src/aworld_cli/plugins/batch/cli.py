import argparse
import asyncio
from typing import List

from .runner import run_batch_job


def _build_parser() -> argparse.ArgumentParser:
    """
    Build argument parser for the batch-job inner plugin command.

    The parser expects a YAML configuration file path and optionally allows
    overriding the remote backend defined in the configuration.

    Returns:
        Configured ArgumentParser instance.
    """
    parser = argparse.ArgumentParser(
        prog="aworld-cli batch-job",
        description="Run batch jobs with agents using a YAML configuration file.",
    )
    parser.add_argument(
        "config_path",
        type=str,
        help="Path to batch job YAML configuration file.",
    )
    parser.add_argument(
        "--remote-backend",
        type=str,
        help="Override remote backend defined in config file.",
    )
    return parser


def run_batch_command(argv: List[str]) -> int:
    """
    Entry point for the batch-job inner plugin command.

    This function parses batch-specific arguments, loads the batch
    configuration, and executes the batch job using BatchExecutor.

    Args:
        argv: Remaining CLI arguments, excluding program name and command name.

    Returns:
        Process exit code. Zero indicates success, non-zero indicates failure.
    """
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse already printed the message; propagate exit code.
        return int(exc.code)

    try:
        asyncio.run(run_batch_job(args.config_path, args.remote_backend))
        return 0
    except Exception as exc:  # pylint: disable=broad-except
        print(f"❌ Error running batch job: {exc}")
        import traceback

        traceback.print_exc()
        return 1

