from __future__ import annotations

import asyncio
import json
from pathlib import Path

from aworld.evaluations.substrate import EvaluationFlowDef, resolve_eval_suite, run_evaluation_flow


def run_evaluator_cli(
    *,
    target: str,
    suite: str | None = None,
    output: str | None = None,
    interactive_approval: bool = False,
) -> dict:
    target_path = Path(target).expanduser().resolve()
    suite_def = resolve_eval_suite(suite, target_path)
    flow = EvaluationFlowDef(
        target={
            "target_path": str(target_path),
            "target_kind": "directory" if target_path.is_dir() else "file",
        },
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
    if output:
        output_path = Path(output).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
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
    backend = report.get("judge_backend", {}).get("backend_id")
    if backend:
        summary_line += f"\nJudge backend: {backend}"
    return summary_line
