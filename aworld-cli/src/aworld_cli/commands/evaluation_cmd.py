"""
/evaluation command - Run evaluator flows from chat.
"""
from __future__ import annotations

import argparse
import shlex

from aworld_cli.core.command_system import Command, CommandContext, register_command
from aworld_cli.evaluator_rendering import render_evaluator_summary
from aworld_cli.evaluator_runtime import run_evaluator_source_cli


def _usage() -> str:
    return """Usage:
  /evaluation --input <path> --kind task-answer --judge-agent <agent.md> [--out-dir <dir>]
  /evaluation --input <path> --kind aworld-trajectory-log --task-id <id> --judge-agent <agent.md> [--out-dir <dir>]

Examples:
  /evaluation --input ./task_answers.jsonl --kind task-answer --judge-agent ./eval/answer_judge/agent.md
  /evaluation --input ~/Documents/logs/trajectory.log --kind aworld-trajectory-log --task-id task_123 --judge-agent ./eval/trajectory_evaluator/agent.md
"""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="/evaluation", add_help=False)
    parser.add_argument("--input", required=True)
    parser.add_argument("--kind", required=True)
    parser.add_argument("--judge-agent", required=True)
    parser.add_argument("--out-dir")
    parser.add_argument("--output")
    parser.add_argument("--task-id")
    parser.add_argument("--agent")
    parser.add_argument("--id-field", default="id")
    parser.add_argument("--task-field", default="input")
    parser.add_argument("--answer-field", default="answer")
    parser.add_argument("--interactive-approval", action="store_true")
    parser.add_argument("--help", action="store_true")
    return parser


@register_command
class EvaluationCommand(Command):
    @property
    def name(self) -> str:
        return "evaluation"

    @property
    def description(self) -> str:
        return "Run evaluator flows"

    @property
    def command_type(self) -> str:
        return "tool"

    @property
    def completion_items(self) -> dict[str, str]:
        return {
            "/evaluation --kind task-answer": "Evaluate task+answer JSONL records",
            "/evaluation --kind aworld-trajectory-log": "Evaluate an AWorld trajectory log task",
        }

    async def execute(self, context: CommandContext) -> str:
        raw_args = (context.user_args or "").strip()
        if not raw_args:
            return _usage()

        try:
            parts = shlex.split(raw_args)
        except ValueError as exc:
            return f"Evaluator error: {exc}\n\n{_usage()}"

        if not parts or parts[0] in {"help", "--help", "-h"}:
            return _usage()

        parser = _build_parser()
        try:
            args = parser.parse_args(parts)
        except SystemExit:
            return _usage()

        if args.help:
            return _usage()

        try:
            report = run_evaluator_source_cli(
                input=args.input,
                kind=args.kind,
                judge_agent=args.judge_agent,
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
            return f"Evaluator error: {exc}"

        summary = render_evaluator_summary(report)
        report_path = report.get("report_path")
        if report_path:
            return f"{summary}\nReport: {report_path}"
        return summary
