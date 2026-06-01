from __future__ import annotations

from aworld_cli.evaluator_runtime import render_evaluator_summary, run_evaluator_cli


class EvaluatorTopLevelCommand:
    @property
    def name(self) -> str:
        return "evaluator"

    @property
    def description(self) -> str:
        return "Run a suite-backed evaluation flow for a local target."

    @property
    def aliases(self) -> tuple[str, ...]:
        return tuple()

    def register_parser(self, subparsers) -> None:
        parser = subparsers.add_parser(
            "evaluator",
            help=self.description,
            description=self.description,
            prog="aworld-cli evaluator",
        )
        parser.add_argument("--target", type=str, required=True)
        parser.add_argument("--suite", type=str)
        parser.add_argument("--output", type=str)
        parser.add_argument("--interactive-approval", action="store_true")

    def run(self, args, context) -> int:
        report = run_evaluator_cli(
            target=args.target,
            suite=args.suite,
            output=args.output,
            interactive_approval=args.interactive_approval,
        )
        print(render_evaluator_summary(report))
        return 0
