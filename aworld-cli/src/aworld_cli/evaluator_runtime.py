from __future__ import annotations

import asyncio
import json
from pathlib import Path

from aworld.plugins.discovery import discover_plugins
from aworld.evaluations.manifests import (
    get_declared_eval_suite_schema as _get_declared_eval_suite_schema,
)
from aworld.evaluations.report import (
    EVALUATOR_REPORT_FORMAT_ID,
    EVALUATOR_REPORT_FORMAT_VERSION,
    get_evaluator_report_schema as _get_evaluator_report_schema,
    validate_evaluator_report as _validate_evaluator_report,
)
from aworld.evaluations.substrate import (
    EvaluationFlowDef,
    describe_eval_target,
    run_evaluation_flow,
)
from aworld_cli.core.plugin_manager import PluginManager, get_builtin_plugin_roots
from aworld_cli.evaluator_rendering import render_evaluator_summary as _render_evaluator_summary
from aworld_cli.evaluator_workspace import (
    discover_workspace_suites,
    resolve_cli_target_path,
    resolve_workspace_suite_selection,
)
from aworld_cli.plugin_capabilities.hooks import PluginHookResult, load_plugin_hooks


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
    hooks = _load_evaluator_hooks()
    target_path = resolve_cli_target_path(target) if target is not None else None
    workspace_path = str((target_path.parent if target_path and target_path.is_file() else target_path) or Path.cwd())
    hook_state = _run_evaluator_hooks(
        hooks,
        "evaluator.pre_discover",
        event={"target": target, "workspace_path": workspace_path},
        state={"target": target, "workspace_path": workspace_path},
    )
    suites = discover_workspace_suites(target=target)
    hook_state = _run_evaluator_hooks(
        hooks,
        "evaluator.post_discover",
        event={"target": target, "workspace_path": workspace_path, "suite_names": suites},
        state={**hook_state, "suite_names": suites},
    )
    overridden = hook_state.get("suite_names")
    if isinstance(overridden, list):
        return [str(item) for item in overridden]
    return suites


def get_evaluator_suite_selection(
    *,
    target: str,
    suite: str | None = None,
) -> dict[str, str | None]:
    return resolve_workspace_suite_selection(target=target, suite=suite)


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


def get_declared_evaluator_suite_schema() -> dict[str, object]:
    return _get_declared_eval_suite_schema()


def get_evaluator_report_schema() -> dict[str, object]:
    return _get_evaluator_report_schema()


def validate_evaluator_report(report: dict) -> None:
    _validate_evaluator_report(report)


def _load_evaluator_hooks() -> dict[str, tuple[object, ...]]:
    builtin_plugin_roots = tuple(Path(root).resolve() for root in get_builtin_plugin_roots())
    plugin_manager = PluginManager()
    if hasattr(plugin_manager, "get_runtime_plugin_roots"):
        plugin_roots = [Path(root).resolve() for root in plugin_manager.get_runtime_plugin_roots()]
    else:
        plugin_roots = list(builtin_plugin_roots)
    return load_plugin_hooks(discover_plugins(plugin_roots))


def _run_evaluator_hooks(
    hooks: dict[str, tuple[object, ...]],
    hook_point: str,
    *,
    event: dict[str, object],
    state: dict[str, object],
) -> dict[str, object]:
    """
    Evaluator hook contract:
    - `evaluator.pre_discover` event payload: `target`, `workspace_path`
    - `evaluator.post_discover` event payload: `target`, `workspace_path`, `suite_names`
    - `evaluator.pre_run` event payload: `target`, `suite`, `workspace_path`
    - `evaluator.post_run` event payload: `report`, `target`, `suite`, `workspace_path`
    - `evaluator.render_summary` event payload: `report`, `workspace_path`
    - mutable state: lightweight CLI assembly metadata only
    - allowed side effects: report upload, notifications, summary augmentation
    - hooks do not redefine framework execution, scoring, or gate semantics
    """
    merged = dict(state)
    for hook in hooks.get((hook_point or "").strip().lower(), ()):
        result = asyncio.run(hook.run(event=event, state=merged))
        hook_result = result if isinstance(result, PluginHookResult) else PluginHookResult.from_payload(result)
        if hook_result.metadata:
            merged.update(dict(hook_result.metadata))
    return merged


def run_evaluator_cli(
    *,
    target: str,
    suite: str | None = None,
    output: str | None = None,
    interactive_approval: bool = False,
) -> dict:
    hooks = _load_evaluator_hooks()
    target_path = resolve_cli_target_path(target)
    workspace_path = str(target_path.parent if target_path.is_file() else target_path)
    suite_selection = resolve_workspace_suite_selection(target=target, suite=suite)
    from aworld.evaluations.substrate import resolve_eval_suite_selection

    selection = resolve_eval_suite_selection(suite, target_path)
    suite_def = selection.suite
    hook_state = _run_evaluator_hooks(
        hooks,
        "evaluator.pre_run",
        event={"target": str(target_path), "suite": suite_selection["resolved"], "workspace_path": workspace_path},
        state={"target": str(target_path), "suite": suite, "interactive_approval": interactive_approval},
    )
    target_info = describe_eval_target(target_path)
    for key, value in hook_state.items():
        if key not in {"target", "suite", "interactive_approval", "summary_suffix", "suite_names"}:
            target_info[key] = value
    flow = EvaluationFlowDef(
        target=target_info,
        suite=suite_def,
        interactive_approval=interactive_approval,
        output_path=output,
    )
    report = asyncio.run(run_evaluation_flow(flow))
    if hasattr(report, "to_dict"):
        report = report.to_dict()
    approval = dict(report.get("approval") or {})
    approval.setdefault("required", report.get("gate", {}).get("status") == "needs_approval")
    approval.setdefault("resolved", False)
    approval.setdefault("approved", None)
    if approval["required"] and interactive_approval:
        approved = input("Evaluation requires approval. Approve? [y/N]: ").strip().lower() in {"y", "yes"}
        approval["resolved"] = True
        approval["approved"] = approved
    report["approval"] = approval
    report["suite_selection"] = suite_selection
    report["automation"] = _build_automation_summary(report)
    output_path = (
        Path(output).expanduser().resolve()
        if output
        else default_evaluator_report_path(target_path=target_path, suite_id=report["suite_id"])
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report["report_path"] = str(output_path)
    _run_evaluator_hooks(
        hooks,
        "evaluator.post_run",
        event={
            "report": report,
            "target": str(target_path),
            "suite": suite_selection["resolved"],
            "workspace_path": workspace_path,
        },
        state=hook_state,
    )
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def render_evaluator_summary(report: dict) -> str:
    hooks = _load_evaluator_hooks()
    workspace_path = str(Path(report.get("report_path", report.get("target", {}).get("target_path", Path.cwd()))).resolve().parent)
    hook_state = _run_evaluator_hooks(
        hooks,
        "evaluator.render_summary",
        event={"report": report, "workspace_path": workspace_path},
        state={"summary_suffix": None},
    )
    return _render_evaluator_summary(report, summary_suffix=hook_state.get("summary_suffix"))
