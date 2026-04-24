from __future__ import annotations

import asyncio

from aworld_cli.acp.cli import register_acp_subcommands


class AcpTopLevelCommand:
    @property
    def name(self) -> str:
        return "acp"

    @property
    def description(self) -> str:
        return "ACP backend host and validation commands"

    @property
    def aliases(self) -> tuple[str, ...]:
        return tuple()

    def register_parser(self, subparsers) -> None:
        parser = subparsers.add_parser(
            "acp",
            help=self.description,
            description=self.description,
            prog="aworld-cli acp",
        )
        register_acp_subcommands(parser)

    def run(self, args, context) -> int | None:
        if args.acp_action == "self-test":
            from aworld_cli.acp.self_test import run_self_test

            return asyncio.run(run_self_test())

        if args.acp_action == "describe-validation":
            from aworld_cli.acp.validate_host import build_validate_stdio_host_help

            import json
            import sys

            sys.stdout.write(json.dumps(build_validate_stdio_host_help(), ensure_ascii=False) + "\n")
            sys.stdout.flush()
            return 0

        if args.acp_action == "render-validation-config":
            from aworld_cli.acp.validate_host import (
                render_validation_config,
                write_rendered_validation_config,
            )

            import json
            import sys

            payload = render_validation_config(
                topology=args.topology,
                expand_placeholders_flag=args.expand_placeholders,
                env_assignments=args.env,
            )
            write_rendered_validation_config(payload, output_file=args.output_file)
            sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            sys.stdout.flush()
            return 0

        if args.acp_action == "validate-stdio-host":
            from aworld_cli.acp.validate_host import run_validate_stdio_host

            return asyncio.run(
                run_validate_stdio_host(
                    command=args.command,
                    config_file=args.config_file,
                    topology=args.topology,
                    cwd=args.cwd,
                    env_assignments=args.env,
                    env_json=args.env_json,
                    profile_name=args.profile,
                    session_params_json=args.session_params_json,
                    startup_timeout_seconds=args.startup_timeout_seconds,
                    startup_retries=args.startup_retries,
                )
            )

        from aworld_cli.acp.server import run_stdio_server

        return asyncio.run(run_stdio_server())
