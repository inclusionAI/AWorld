import argparse


def build_acp_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ACP backend host commands",
        prog="aworld-cli acp",
    )
    register_acp_subcommands(parser)
    parser.set_defaults(acp_action="serve")
    return parser


def register_acp_subcommands(parser: argparse.ArgumentParser) -> None:
    subparsers = parser.add_subparsers(dest="acp_action")
    subparsers.required = False
    subparsers.add_parser("serve", help="Run the ACP stdio host")
    subparsers.add_parser("self-test", help="Run ACP self-validation")
    subparsers.add_parser(
        "describe-validation",
        help="Print machine-checkable ACP validation profile and default-input metadata",
    )
    render_validation = subparsers.add_parser(
        "render-validation-config",
        help="Render a topology-specific ACP validation config template",
    )
    render_validation.add_argument(
        "--topology",
        default="base",
        help="Validation topology template name such as base, same-host, or distributed",
    )
    render_validation.add_argument(
        "--expand-placeholders",
        action="store_true",
        help="Expand ${VAR} placeholders using the current environment plus repeated --env overrides",
    )
    render_validation.add_argument(
        "--output-file",
        default=None,
        help="Optional path to write the rendered validation config JSON",
    )
    render_validation.add_argument(
        "--env",
        action="append",
        default=[],
        help="Environment override in KEY=VALUE form used when expanding template placeholders",
    )
    validate_host = subparsers.add_parser(
        "validate-stdio-host",
        help="Validate an ACP stdio host against the phase-1 contract",
    )
    validate_host.add_argument(
        "--command",
        default=None,
        help="Shell-style command used to launch the target ACP stdio host",
    )
    validate_host.add_argument(
        "--config-file",
        default=None,
        help="JSON file describing validate-stdio-host inputs such as command/cwd/profile/sessionParams/env",
    )
    validate_host.add_argument(
        "--topology",
        default=None,
        help="Optional topology template name used as the validation config base, such as same-host or distributed",
    )
    validate_host.add_argument(
        "--cwd",
        default=".",
        help="Working directory used when launching the target host",
    )
    validate_host.add_argument(
        "--profile",
        default="self-test",
        help="Validation profile name used to drive prompt and expectation fixtures",
    )
    validate_host.add_argument(
        "--session-params-json",
        default=None,
        help="JSON object used as the newSession params payload; defaults to {'cwd': '.', 'mcpServers': []}",
    )
    validate_host.add_argument(
        "--startup-timeout-seconds",
        type=float,
        default=None,
        help="Optional timeout applied to initialize/newSession responses during host startup validation",
    )
    validate_host.add_argument(
        "--startup-retries",
        type=int,
        default=0,
        help="Number of startup retries after initialize/newSession timeout or startup failure",
    )
    validate_host.add_argument(
        "--env",
        action="append",
        default=[],
        help="Environment override in KEY=VALUE form; may be provided multiple times",
    )
    validate_host.add_argument(
        "--env-json",
        default=None,
        help="JSON object merged into the launched host environment before repeated --env overrides",
    )
