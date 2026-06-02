from __future__ import annotations

from aworld_cli.evaluator_runtime import (
    available_evaluator_suites,
    render_evaluator_summary,
    run_evaluator_cli,
)


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
        parser.add_argument("--target", type=str)
        parser.add_argument("--suite", type=str)
        parser.add_argument("--output", type=str)
        parser.add_argument("--interactive-approval", action="store_true")
        parser.add_argument("--list-suites", action="store_true")

    def run(self, args, context) -> int:
        if getattr(args, "list_suites", False):
            print("Available evaluator suites:")
            for suite_name in available_evaluator_suites():
                print(f"  - {suite_name}")
            return 0

        if not getattr(args, "target", None):
            print("❌ --target is required unless --list-suites is used")
            return 1

        report = run_evaluator_cli(
            target=args.target,
            suite=args.suite,
            output=args.output,
            interactive_approval=args.interactive_approval,
        )
        print(render_evaluator_summary(report))
        gate_status = report.get("gate", {}).get("status")
        approval = report.get("approval") or {}
        if gate_status == "fail":
            return 2
        if gate_status == "needs_approval" and not approval.get("approved", False):
            return 3
        return 0
