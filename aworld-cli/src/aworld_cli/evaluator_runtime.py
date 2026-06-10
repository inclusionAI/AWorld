from __future__ import annotations

import asyncio
import builtins
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
    AgentJudgeBackend,
    EvaluationFlowDef,
    GateMetricCondition,
    GatePolicyDef,
    JudgeSchemaDef,
    StateCheckGrader,
    describe_eval_target,
    run_evaluation_flow,
)
from aworld.evaluations.sources import AWorldTrajectoryLogSource, JsonlTaskAnswerSource, create_source_eval_suite
from aworld.evaluations.trajectory_judge import TrajectoryJudgeSchema
from pydantic import BaseModel
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
    automation = {
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
    source_selection = report.get("source_selection") or {}
    if source_selection:
        automation["source_kind"] = source_selection.get("kind")
        automation["source_input"] = source_selection.get("input")
        automation["task_id"] = source_selection.get("task_id")
    return automation


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
    - `evaluator.pre_run` event payload for target mode: `mode=target`, `target`, `suite`, `workspace_path`
    - `evaluator.pre_run` event payload for source mode: `mode=source`, `input`, `kind`, `task_id`, `judge_agent`, `agent`, `workspace_path`, `output_path`
    - `evaluator.post_run` event payload for target mode: `mode=target`, `report`, `target`, `suite`, `workspace_path`
    - `evaluator.post_run` event payload for source mode: `mode=source`, `report`, `input`, `kind`, `task_id`, `judge_agent`, `agent`, `workspace_path`, `output_path`
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


class _SourceJudgeOutput(BaseModel):
    score: float
    verdict: str


def _source_report_path(
    *,
    input_path: Path,
    suite_id: str,
    task_id: str | None,
    output: str | None,
    out_dir: str | None,
) -> Path:
    if output:
        return Path(output).expanduser().resolve()
    root = Path(out_dir).expanduser().resolve() if out_dir else Path.cwd() / ".aworld" / "evaluations"
    root.mkdir(parents=True, exist_ok=True)
    token = _sanitize_path_token(task_id or input_path.stem or input_path.name)
    return root / f"{token}.{_sanitize_path_token(suite_id)}.json"


def _build_source_prompt(case_input: dict, target: dict, suite) -> str:
    payload = {
        "case": {key: value for key, value in case_input.items() if not str(key).startswith("_")},
        "state": {
            "answer": target.get("answer"),
            "status": target.get("status"),
            "artifacts": target.get("artifacts"),
            "trajectory": target.get("trajectory"),
            "tool_calls": target.get("tool_calls"),
        },
        "required_output_schema": {"score": "number, weighted score from 0 to 100", "verdict": "string"},
        "instruction": "Evaluate the existing answer/state and return exactly one JSON object.",
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _build_trajectory_prompt(case_input: dict, target: dict, suite) -> str:
    outcome = (target.get("artifacts") or {}).get("outcome") or {}
    extracted_path = outcome.get("extracted_path")
    extracted_payload = {}
    if extracted_path:
        extracted_payload = json.loads(Path(str(extracted_path)).read_text(encoding="utf-8"))
    payload = {
        "case": {key: value for key, value in case_input.items() if not str(key).startswith("_")},
        "extracted_trajectory": extracted_payload,
        "required_output_schema": {
            "score": "number, weighted score from 0 to 100",
            "verdict": "Excellent|Pass|Marginal|Fail",
            "A1_groundedness": "integer 1-5",
            "A2_completeness": "integer 1-5",
            "A3_relevance": "integer 1-5",
            "A4_readability": "integer 1-5",
            "B1_tool_use": "integer 1-5",
            "B2_efficiency": "integer 1-5",
            "B3_compliance": "integer 1-5",
            "B4_robustness": "integer 1-5",
            "veto_triggered": "boolean",
        },
        "instruction": (
            "Apply the trajectory evaluator contract to the extracted trajectory. "
            "Do not call tools and do not re-read the raw log; all required evidence is in extracted_trajectory. "
            "Return exactly one JSON object matching required_output_schema, with no markdown."
        ),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _build_source_suite(
    *,
    kind: str,
    input_path: Path,
    judge_agent_path: Path,
    task_id: str | None,
    id_field: str,
    task_field: str,
    answer_field: str,
    out_dir: str | None,
):
    if kind == "task-answer":
        source = JsonlTaskAnswerSource(
            path=input_path,
            id_field=id_field,
            input_field=task_field,
            answer_field=answer_field,
        )
        return create_source_eval_suite(
            suite_id="source-evaluator",
            source=source,
            judge_backend=AgentJudgeBackend.from_agent_markdown(
                judge_agent_path,
                backend_id="source-agent-md",
                prompt_builder=_build_source_prompt,
            ),
            judge_schema=JudgeSchemaDef(output_model=_SourceJudgeOutput),
            gate_policy=GatePolicyDef(metric_name="score", pass_threshold=70.0),
        )

    if kind == "aworld-trajectory-log":
        if not task_id:
            raise ValueError("--task-id is required for aworld-trajectory-log source")
        source = AWorldTrajectoryLogSource(
            path=input_path,
            task_ids=[task_id],
            extraction_dir=out_dir,
        )
        return create_source_eval_suite(
            suite_id="trajectory-log-source-evaluator",
            source=source,
            judge_backend=AgentJudgeBackend.from_agent_markdown(
                judge_agent_path,
                backend_id="trajectory-evaluator-agent-md",
                prompt_builder=_build_trajectory_prompt,
            ),
            judge_schema=TrajectoryJudgeSchema.default(),
            outcome_scorers=(
                StateCheckGrader(
                    metric_name="has_evidence",
                    source="outcome",
                    path=("evidence_blocks",),
                    op=">",
                    expected=0,
                ),
                StateCheckGrader(
                    metric_name="agent_finished",
                    source="outcome",
                    path=("is_finished",),
                    op="==",
                    expected=True,
                ),
            ),
            gate_policy=GatePolicyDef(
                pass_all=(
                    GateMetricCondition(metric_name="score", op=">=", threshold=70.0),
                    GateMetricCondition(metric_name="A1_groundedness", op=">=", threshold=3),
                    GateMetricCondition(metric_name="has_evidence", op="==", threshold=1.0),
                    GateMetricCondition(metric_name="agent_finished", op="==", threshold=1.0),
                )
            ),
        )

    raise ValueError(f"unsupported source kind: {kind}")


def run_evaluator_source_cli(
    *,
    input: str,
    kind: str,
    judge_agent: str,
    out_dir: str | None = None,
    output: str | None = None,
    task_id: str | None = None,
    agent: str | None = None,
    id_field: str = "id",
    task_field: str = "input",
    answer_field: str = "answer",
    interactive_approval: bool = False,
) -> dict:
    hooks = _load_evaluator_hooks()
    input_path = Path(input).expanduser().resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"source input does not exist: {input_path}")
    judge_agent_path = Path(judge_agent).expanduser().resolve()
    if not judge_agent_path.exists():
        raise FileNotFoundError(f"judge agent does not exist: {judge_agent_path}")

    workspace_path = str(input_path.parent if input_path.is_file() else input_path)
    event_base = {
        "mode": "source",
        "input": str(input_path),
        "kind": kind,
        "task_id": task_id,
        "judge_agent": str(judge_agent_path),
        "agent": agent,
        "workspace_path": workspace_path,
        "output_path": str(Path(output).expanduser().resolve()) if output else None,
    }
    hook_state = _run_evaluator_hooks(
        hooks,
        "evaluator.pre_run",
        event=event_base,
        state={
            "mode": "source",
            "input": str(input_path),
            "kind": kind,
            "task_id": task_id,
            "judge_agent": str(judge_agent_path),
            "agent": agent,
            "interactive_approval": interactive_approval,
        },
    )
    suite = _build_source_suite(
        kind=kind,
        input_path=input_path,
        judge_agent_path=judge_agent_path,
        task_id=task_id,
        id_field=id_field,
        task_field=task_field,
        answer_field=answer_field,
        out_dir=out_dir,
    )
    target_info = {
        "target_kind": "source",
        "target_path": str(input_path),
        "source_kind": kind,
        "task_id": task_id,
        "judge_agent": str(judge_agent_path),
    }
    for key, value in hook_state.items():
        if key not in {"mode", "input", "kind", "task_id", "judge_agent", "agent", "interactive_approval", "summary_suffix"}:
            target_info[key] = value
    flow = EvaluationFlowDef(
        target=target_info,
        suite=suite,
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
        approved = builtins.input("Evaluation requires approval. Approve? [y/N]: ").strip().lower() in {"y", "yes"}
        approval["resolved"] = True
        approval["approved"] = approved
    report["approval"] = approval
    report["source_selection"] = {
        "mode": "source",
        "input": str(input_path),
        "kind": kind,
        "task_id": task_id,
        "judge_agent": str(judge_agent_path),
    }
    report["automation"] = _build_automation_summary(report)
    output_path = _source_report_path(
        input_path=input_path,
        suite_id=report["suite_id"],
        task_id=task_id,
        output=output,
        out_dir=out_dir,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report["report_path"] = str(output_path)
    post_event = {
        **event_base,
        "output_path": str(output_path),
        "report": report,
    }
    _run_evaluator_hooks(
        hooks,
        "evaluator.post_run",
        event=post_event,
        state=hook_state,
    )
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


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
        event={
            "mode": "target",
            "target": str(target_path),
            "suite": suite_selection["resolved"],
            "workspace_path": workspace_path,
        },
        state={
            "mode": "target",
            "target": str(target_path),
            "suite": suite,
            "interactive_approval": interactive_approval,
        },
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
        approved = builtins.input("Evaluation requires approval. Approve? [y/N]: ").strip().lower() in {"y", "yes"}
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
            "mode": "target",
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
