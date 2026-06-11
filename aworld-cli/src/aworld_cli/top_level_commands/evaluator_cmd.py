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
    run_evaluator_source_cli,
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
        parser.add_argument("--input", type=str)
        parser.add_argument("--kind", type=str)
        parser.add_argument("--judge-agent", type=str)
        parser.add_argument("--judge-agent-name", type=str)
        parser.add_argument("--judge-backend-ref", type=str)
        parser.add_argument("--out-dir", type=str)
        parser.add_argument("--task-id", type=str)
        parser.add_argument("--agent", type=str)
        parser.add_argument("--id-field", default="id")
        parser.add_argument("--task-field", default="input")
        parser.add_argument("--answer-field", default="answer")

    def run(self, args, context) -> int:
        if getattr(args, "input", None):
            incompatible_args = (
                ("target", "--target"),
                ("suite", "--suite"),
                ("list_suites", "--list-suites"),
                ("print_report_schema", "--print-report-schema"),
                ("validate_report", "--validate-report"),
            )
            for attr_name, flag_name in incompatible_args:
                if getattr(args, attr_name, None):
                    print(f"Evaluator error: {flag_name} cannot be used with --input")
                    return 1
            if not getattr(args, "kind", None):
                print("Evaluator error: --kind is required with --input")
                return 1
            judge_selectors = [
                getattr(args, "judge_agent", None),
                getattr(args, "judge_agent_name", None),
                getattr(args, "judge_backend_ref", None),
            ]
            if sum(1 for value in judge_selectors if value) != 1:
                print("Evaluator error: exactly one of --judge-agent, --judge-agent-name, or --judge-backend-ref is required with --input")
                return 1
            try:
                report = run_evaluator_source_cli(
                    input=args.input,
                    kind=args.kind,
                    judge_agent=args.judge_agent,
                    judge_agent_name=args.judge_agent_name,
                    judge_backend_ref=args.judge_backend_ref,
                    out_dir=args.out_dir,
                    output=args.output,
                    task_id=args.task_id,
                    agent=args.agent,
                    id_field=args.id_field,
                    task_field=args.task_field,
                    answer_field=args.answer_field,
                    interactive_approval=args.interactive_approval,
                )
            except (FileNotFoundError, ValueError, KeyError) as exc:
                print(f"Evaluator error: {exc}")
                return 1
            print(render_evaluator_summary(report))
            return evaluator_exit_code(report)

        source_only_args = (
            ("kind", "--kind"),
            ("judge_agent", "--judge-agent"),
            ("judge_agent_name", "--judge-agent-name"),
            ("judge_backend_ref", "--judge-backend-ref"),
            ("out_dir", "--out-dir"),
            ("task_id", "--task-id"),
            ("agent", "--agent"),
        )
        for attr_name, flag_name in source_only_args:
            if getattr(args, attr_name, None):
                print(f"Evaluator error: --input is required when using {flag_name}")
                return 1

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
            try:
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
            except (FileNotFoundError, ValueError, KeyError) as exc:
                print(f"Evaluator error: {exc}")
                return 1
            return 0

        if not getattr(args, "target", None):
            print("❌ --target is required unless --list-suites is used")
            return 1

        try:
            report = run_evaluator_cli(
                target=args.target,
                suite=args.suite,
                output=args.output,
                interactive_approval=args.interactive_approval,
            )
        except (FileNotFoundError, ValueError, KeyError) as exc:
            print(f"Evaluator error: {exc}")
            return 1
        print(render_evaluator_summary(report))
        return evaluator_exit_code(report)
