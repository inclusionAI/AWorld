import json
import sys
from pathlib import Path

report_path = Path(sys.argv[1]).resolve()
report = json.loads(report_path.read_text())
summary = {
    key: report.get(key)
    for key in (
        "run_id", "status", "selected_candidate_id", "repair_focus_candidate_id",
        "acceptance_confidence", "rejection_attribution", "campaign",
    )
}
summary["report_path"] = str(report_path)
summary["gates"] = []
for gate in report.get("gate_results", []):
    details = gate.get("details") or {}
    selected = {
        key: value for key, value in details.items()
        if value is None or isinstance(value, (str, bool, int, float))
    }
    if gate["gate_name"] in {
        "score_improvement", "cost_latency_regression", "evaluation_runtime_health",
        "global_regression_benchmark", "held_out_verification",
    } or not gate.get("passed"):
        summary["gates"].append({
            "name": gate["gate_name"], "passed": gate.get("passed"),
            "reason": gate.get("reason"), "details": selected,
        })
summary["passed_gate_count"] = sum(g.get("passed") is True for g in report.get("gate_results", []))
summary["failed_gate_count"] = sum(g.get("passed") is not True for g in report.get("gate_results", []))
summary["evaluator_report_paths"] = report.get("evaluator_report_paths", [])
print(json.dumps(summary, ensure_ascii=False, indent=2))
