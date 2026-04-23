from __future__ import annotations


class HelpZhTopLevelCommand:
    @property
    def name(self) -> str:
        return "help-zh"

    @property
    def description(self) -> str:
        return "Show CLI help in Chinese."

    @property
    def aliases(self) -> tuple[str, ...]:
        return tuple()

    @property
    def visible_in_help(self) -> bool:
        return False

    def register_parser(self, subparsers) -> None:
        subparsers.add_parser(
            "help-zh",
            help=self.description,
            description=self.description,
            prog="aworld-cli help-zh",
        )

    def run(self, args, context) -> int | None:
        from aworld_cli.main import print_help_text

        print_help_text(zh=True)
        return 0
