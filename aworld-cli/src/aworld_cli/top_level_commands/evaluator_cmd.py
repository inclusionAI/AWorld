from __future__ import annotations

import json
from pathlib import Path

from aworld_cli.evaluator_runtime import (
    available_evaluator_suites,
    evaluator_exit_code,
    get_evaluator_suite_selection,
    get_evaluator_report_schema,
    render_evaluator_summary,
    run_evaluator_cli,
    validate_evaluator_report,
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
        parser.add_argument("--print-report-schema", action="store_true")
        parser.add_argument("--validate-report", type=str)

    def run(self, args, context) -> int:
        if getattr(args, "print_report_schema", False):
            print(json.dumps(get_evaluator_report_schema(), ensure_ascii=False, indent=2))
            return 0

        if getattr(args, "validate_report", None):
            report_path = Path(args.validate_report).expanduser().resolve()
            report = json.loads(report_path.read_text(encoding="utf-8"))
            try:
                validate_evaluator_report(report)
            except ValueError as exc:
                print(f"Report is invalid: {exc}")
                return 4
            print(f"Report is valid: {report_path}")
            return 0

        if getattr(args, "list_suites", False):
            if getattr(args, "target", None):
                print("Available evaluator suites for target:")
                suite_names = available_evaluator_suites(target=args.target)
            else:
                print("Available evaluator suites:")
                suite_names = available_evaluator_suites()
            for suite_name in suite_names:
                print(f"  - {suite_name}")
            if getattr(args, "target", None) and suite_names:
                selection = get_evaluator_suite_selection(target=args.target, suite=args.suite)
                print(f"Default suite: {selection['resolved']}")
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
        return evaluator_exit_code(report)
