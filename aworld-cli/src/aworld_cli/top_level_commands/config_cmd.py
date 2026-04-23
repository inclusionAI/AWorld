from __future__ import annotations

import asyncio


class ConfigTopLevelCommand:
    @property
    def name(self) -> str:
        return "config"

    @property
    def description(self) -> str:
        return "Launch the interactive global configuration editor."

    @property
    def aliases(self) -> tuple[str, ...]:
        return tuple()

    @property
    def visible_in_help(self) -> bool:
        return False

    def register_parser(self, subparsers) -> None:
        subparsers.add_parser(
            "config",
            help=self.description,
            description=self.description,
            prog="aworld-cli config",
        )

    def run(self, args, context) -> int | None:
        from aworld_cli.main import AWorldCLI

        async def _run_config():
            cli = AWorldCLI()
            await cli._interactive_config_editor()

        asyncio.run(_run_config())
        return 0
