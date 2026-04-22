from __future__ import annotations

import asyncio
from pathlib import Path

from aworld.logs.util import logger
from aworld.memory.main import _default_file_memory_store

from aworld_gateway import GATEWAY_DISPLAY_NAME
from aworld_cli import gateway_cli


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
        from aworld_cli._globals import console
        from aworld_cli.core.config import has_model_config, load_config_with_env
        from aworld_cli.core.skill_registry import get_skill_registry
        from aworld_cli.main import _resolve_agent_dirs, _show_banner, init_middlewares

        global_args = gateway_cli.parse_gateway_global_args(context.argv)
        config_dict, _, _ = load_config_with_env(global_args.env_file)
        init_middlewares(
            init_memory=True,
            init_retriever=False,
            custom_memory_store=_default_file_memory_store(),
        )

        if "--no-banner" not in context.argv:
            _show_banner()

        if not has_model_config(config_dict):
            console.print(
                "[yellow]No model configuration (API key, etc.) detected. Please configure before starting.[/yellow]"
            )
            console.print("[dim]Run: aworld-cli --config[/dim]")
            console.print(
                "[dim]Or create .env in the current directory. See: [link=https://github.com/inclusionAI/AWorld/blob/main/README.md]README[/link][/dim]"
            )
            return 1

        if global_args.skill_path:
            registry = get_skill_registry(skill_paths=global_args.skill_path)
        else:
            registry = get_skill_registry()

        all_skills = registry.get_all_skills()
        if all_skills:
            skill_names = list(all_skills.keys())
            logger.info(
                "Loaded %d global skill(s): %s",
                len(skill_names),
                ", ".join(skill_names),
            )

        asyncio.run(
            gateway_cli.serve_gateway(
                base_dir=Path(context.cwd),
                remote_backends=global_args.remote_backend,
                local_dirs=_resolve_agent_dirs(global_args.agent_dir),
                agent_files=global_args.agent_file,
            )
        )
        return 0
