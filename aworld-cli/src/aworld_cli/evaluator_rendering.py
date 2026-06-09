from __future__ import annotations


def render_evaluator_summary(report: dict, *, summary_suffix: str | None = None) -> str:
    suite_id = report.get("suite_id", "unknown-suite")
    gate = report.get("gate", {})
    status = gate.get("status", "unknown")
    metric_value = gate.get("value")
    summary_line = f"Evaluator suite: {suite_id}\nGate: {status}"
    if metric_value is not None:
        if isinstance(metric_value, (int, float)):
            summary_line += f" ({metric_value:.2f})"
        else:
            summary_line += f" ({metric_value})"
    selection = report.get("suite_selection") or {}
    if selection.get("resolved"):
        summary_line += f"\nSuite selection: {selection.get('mode', 'unknown')} -> {selection['resolved']}"
    backend = report.get("judge_backend", {}).get("backend_id")
    if backend:
        summary_line += f"\nJudge backend: {backend}"
    report_path = report.get("report_path")
    if report_path:
        summary_line += f"\nReport: {report_path}"
    if summary_suffix:
        summary_line += f"\n{summary_suffix}"
    return summary_line
