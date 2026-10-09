"""Select the full Rich CLI by default or the minimal Session/Run CLI."""

from __future__ import annotations

import os
import sys


_MINIMAL_FLAGS = {"--minimal", "--session-run"}
_RICH_FLAG = "--rich"
_MODEL_OVERRIDE_ENV = "AWORLD_CLI_MODEL_OVERRIDE"


def _model_override(arguments: list[str]) -> str | None:
    for index, argument in enumerate(arguments):
        if argument.startswith("--model="):
            return argument.split("=", 1)[1]
        if argument == "--model" and index + 1 < len(arguments):
            return arguments[index + 1]
    return None


def _run_rich(arguments: list[str]) -> int | None:
    original = sys.argv
    # This must happen before importing aworld_cli.main: that module imports
    # AWorld logging transitively, and logger sinks are fixed at construction.
    os.environ.setdefault("AWORLD_DISABLE_CONSOLE_LOG", "true")
    previous_model_override = os.environ.get(_MODEL_OVERRIDE_ENV)
    model_override = _model_override(arguments)
    if model_override:
        os.environ[_MODEL_OVERRIDE_ENV] = model_override
    sys.argv = [original[0], *arguments]
    try:
        try:
            from aworld_cli.main import main as rich_main
        except ModuleNotFoundError as exc:
            if exc.name == "aworld_cli.main":
                print(
                    "aworld-cli: this thin installation does not include Rich mode; "
                    "using the minimal Session/Run CLI",
                    file=sys.stderr,
                )
                from aworld.cli.main import main as kernel_main
                return kernel_main(arguments)
            print(
                "aworld-cli: Rich mode is unavailable in this installation "
                f"(missing {exc.name}). Install the AWorld Runtime compatibility "
                "package, or use --minimal.",
                file=sys.stderr,
            )
            return 2
        return rich_main()
    finally:
        sys.argv = original
        if previous_model_override is None:
            os.environ.pop(_MODEL_OVERRIDE_ENV, None)
        else:
            os.environ[_MODEL_OVERRIDE_ENV] = previous_model_override


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    minimal = any(argument in _MINIMAL_FLAGS for argument in arguments)
    rich = _RICH_FLAG in arguments
    if minimal and rich:
        print("aworld-cli: use either --rich or --minimal, not both", file=sys.stderr)
        return 2
    arguments = [
        argument for argument in arguments
        if argument not in _MINIMAL_FLAGS and argument != _RICH_FLAG
    ]
    if not minimal:
        return _run_rich(arguments)
    from aworld.cli.main import main as kernel_main
    return kernel_main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
