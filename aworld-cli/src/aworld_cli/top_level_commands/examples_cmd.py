from __future__ import annotations


class ExamplesTopLevelCommand:
    @property
    def name(self) -> str:
        return "examples"

    @property
    def description(self) -> str:
        return "Show CLI usage examples."

    @property
    def aliases(self) -> tuple[str, ...]:
        return tuple()

    @property
    def visible_in_help(self) -> bool:
        return False

    def register_parser(self, subparsers) -> None:
        subparsers.add_parser(
            "examples",
            help=self.description,
            description=self.description,
            prog="aworld-cli examples",
        )

    def run(self, args, context) -> int | None:
        from aworld_cli.main import print_usage_examples

        print_usage_examples(zh=bool(getattr(args, "zh", False)))
        return 0
