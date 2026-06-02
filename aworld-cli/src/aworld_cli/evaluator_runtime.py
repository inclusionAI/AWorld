from __future__ import annotations

import asyncio
import json
from pathlib import Path

from aworld.evaluations.substrate import (
    EVALUATOR_REPORT_FORMAT_ID,
    EVALUATOR_REPORT_FORMAT_VERSION,
    EvaluationFlowDef,
    describe_eval_target,
    list_eval_suites,
    list_matching_eval_suites,
    resolve_eval_suite_selection,
    run_evaluation_flow,
)


def _sanitize_path_token(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in value).strip("-") or "target"


def default_evaluator_report_path(*, target_path: Path, suite_id: str, cwd: Path | None = None) -> Path:
    root = (cwd or Path.cwd()).expanduser().resolve()
    report_dir = root / ".aworld" / "evaluations"
    report_dir.mkdir(parents=True, exist_ok=True)
    target_token = _sanitize_path_token(target_path.stem or target_path.name)
    suite_token = _sanitize_path_token(suite_id)
    return report_dir / f"{target_token}.{suite_token}.json"


def available_evaluator_suites(*, target: str | None = None) -> list[str]:
    if target is None:
        return list_eval_suites()
    return list_matching_eval_suites(target)


def get_evaluator_suite_selection(
    *,
    target: str,
    suite: str | None = None,
) -> dict[str, str | None]:
    selection = resolve_eval_suite_selection(suite, target)
    return {
        "requested": suite,
        "resolved": selection.suite_id,
        "mode": selection.mode,
    }


def evaluator_exit_code(report: dict) -> int:
    gate_status = report.get("gate", {}).get("status")
    approval = report.get("approval") or {}
    if gate_status == "fail":
        return 2
    if gate_status == "needs_approval" and not approval.get("approved", False):
        return 3
    return 0


def _build_automation_summary(report: dict) -> dict[str, object]:
    gate = report.get("gate") or {}
    approval = report.get("approval") or {}
    result_counts = report.get("result_counts") or {}
    return {
        "gate_status": gate.get("status"),
        "metric_name": gate.get("metric_name"),
        "metric_value": gate.get("value"),
        "approval_required": approval.get("required", False),
        "approval_resolved": approval.get("resolved", False),
        "approved": approval.get("approved"),
        "suggested_exit_code": evaluator_exit_code(report),
        "case_count": result_counts.get("cases_total", len(report.get("results") or [])),
        "judge_backend": (report.get("judge_backend") or {}).get("backend_id"),
    }


def get_evaluator_report_schema() -> dict[str, object]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://schemas.aworld.dev/evaluator/report/v{EVALUATOR_REPORT_FORMAT_VERSION}.json",
        "title": "AWorld Evaluator Report",
        "type": "object",
        "required": [
            "report_version",
            "report_format",
            "generated_at",
            "suite_id",
            "target",
            "summary",
            "metrics",
            "results",
            "result_counts",
            "approval",
        ],
        "properties": {
            "report_version": {"type": "integer", "const": EVALUATOR_REPORT_FORMAT_VERSION},
            "report_format": {
                "type": "object",
                "required": ["id", "version"],
                "properties": {
                    "id": {"type": "string", "const": EVALUATOR_REPORT_FORMAT_ID},
                    "version": {"type": "integer", "const": EVALUATOR_REPORT_FORMAT_VERSION},
                },
                "additionalProperties": False,
            },
            "generated_at": {"type": "string", "format": "date-time"},
            "suite_id": {"type": "string"},
            "target": {"type": "object"},
            "summary": {"type": "object"},
            "metrics": {"type": "object"},
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["case_id", "input", "metrics", "judge"],
                    "properties": {
                        "case_id": {"type": "string"},
                        "input": {"type": "object"},
                        "metrics": {"type": "object"},
                        "judge": {"type": "object"},
                        "judge_backend": {
                            "type": ["object", "null"],
                            "properties": {
                                "backend_id": {"type": "string"},
                            },
                            "required": ["backend_id"],
                            "additionalProperties": False,
                        },
                    },
                    "additionalProperties": True,
                },
            },
            "result_counts": {
                "type": "object",
                "required": ["cases_total", "cases_with_metrics", "cases_with_judge"],
                "properties": {
                    "cases_total": {"type": "integer", "minimum": 0},
                    "cases_with_metrics": {"type": "integer", "minimum": 0},
                    "cases_with_judge": {"type": "integer", "minimum": 0},
                },
                "additionalProperties": False,
            },
            "gate": {"type": "object"},
            "approval": {"type": "object"},
            "judge_backend": {"type": "object"},
            "suite_selection": {"type": "object"},
            "automation": {"type": "object"},
            "report_path": {"type": "string"},
        },
        "additionalProperties": True,
    }


def run_evaluator_cli(
    *,
    target: str,
    suite: str | None = None,
    output: str | None = None,
    interactive_approval: bool = False,
) -> dict:
    target_path = Path(target).expanduser().resolve()
    selection = resolve_eval_suite_selection(suite, target_path)
    suite_def = selection.suite
    target_info = describe_eval_target(target_path)
    flow = EvaluationFlowDef(
        target=target_info,
        suite=suite_def,
        interactive_approval=interactive_approval,
        output_path=output,
    )
    report = asyncio.run(run_evaluation_flow(flow))
    approval = dict(report.get("approval") or {})
    approval.setdefault("required", report.get("gate", {}).get("status") == "needs_approval")
    approval.setdefault("resolved", False)
    approval.setdefault("approved", None)
    if approval["required"] and interactive_approval:
        approved = input("Evaluation requires approval. Approve? [y/N]: ").strip().lower() in {"y", "yes"}
        approval["resolved"] = True
        approval["approved"] = approved
    report["approval"] = approval
    report["suite_selection"] = {
        "requested": suite,
        "resolved": selection.suite_id,
        "mode": selection.mode,
    }
    report["automation"] = _build_automation_summary(report)
    output_path = (
        Path(output).expanduser().resolve()
        if output
        else default_evaluator_report_path(target_path=target_path, suite_id=report["suite_id"])
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report["report_path"] = str(output_path)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def render_evaluator_summary(report: dict) -> str:
    suite_id = report.get("suite_id", "unknown-suite")
    gate = report.get("gate", {})
    status = gate.get("status", "unknown")
    metric_value = gate.get("value")
    summary_line = f"Evaluator suite: {suite_id}\nGate: {status}"
    if metric_value is not None:
        summary_line += f" ({metric_value:.2f})"
    selection = report.get("suite_selection") or {}
    if selection.get("resolved"):
        summary_line += f"\nSuite selection: {selection.get('mode', 'unknown')} -> {selection['resolved']}"
    backend = report.get("judge_backend", {}).get("backend_id")
    if backend:
        summary_line += f"\nJudge backend: {backend}"
    report_path = report.get("report_path")
    if report_path:
        summary_line += f"\nReport: {report_path}"
    return summary_line
