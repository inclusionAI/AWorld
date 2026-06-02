from __future__ import annotations

import asyncio
import json
from pathlib import Path

from aworld.evaluations.substrate import (
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
