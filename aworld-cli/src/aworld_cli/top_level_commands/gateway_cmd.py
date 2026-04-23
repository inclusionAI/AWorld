from __future__ import annotations

import asyncio
from pathlib import Path

from aworld_gateway import GATEWAY_DISPLAY_NAME
from aworld_cli import gateway_cli
from aworld_cli.runtime_bootstrap import RuntimeBootstrapError, bootstrap_runtime


class GatewayTopLevelCommand:
    @property
    def name(self) -> str:
        return "gateway"

    @property
    def description(self) -> str:
        return f"{GATEWAY_DISPLAY_NAME} management commands"

    @property
    def aliases(self) -> tuple[str, ...]:
        return tuple()

    def register_parser(self, subparsers) -> None:
        parser = subparsers.add_parser(
            "gateway",
            help=self.description,
            description=self.description,
            prog="aworld-cli gateway",
        )
        gateway_cli.register_gateway_subcommands(parser)

    def run(self, args, context) -> int | None:
        if args.gateway_action == "status":
            print(gateway_cli.handle_gateway_status())
            return 0

        if (
            args.gateway_action == "channels"
            and getattr(args, "channels_action", None) == "list"
        ):
            print(gateway_cli.handle_gateway_channels_list())
            return 0

        if args.gateway_action == "server":
            return self._run_server(context)

        raise ValueError(f"Unsupported gateway action: {args.gateway_action}")

    def _run_server(self, context) -> int:
        from aworld_cli.main import _resolve_agent_dirs, _show_banner, init_middlewares

        global_args = gateway_cli.parse_gateway_global_args(context.argv)
        try:
            bootstrap_runtime(
                env_file=global_args.env_file,
                skill_paths=global_args.skill_path,
                show_banner="--no-banner" not in context.argv,
                init_middlewares_fn=init_middlewares,
                show_banner_fn=_show_banner,
            )
        except RuntimeBootstrapError:
            return 1

        asyncio.run(
            gateway_cli.serve_gateway(
                base_dir=Path(context.cwd),
                remote_backends=global_args.remote_backend,
                local_dirs=_resolve_agent_dirs(global_args.agent_dir),
                agent_files=global_args.agent_file,
            )
        )
        return 0
