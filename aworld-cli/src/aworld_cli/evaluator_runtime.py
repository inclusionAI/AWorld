from __future__ import annotations

import asyncio
import builtins
import importlib
import inspect
import json
import time
from pathlib import Path
from typing import Any, Mapping

from aworld.plugins.discovery import discover_plugins
from aworld.evaluations.execution import normalize_task_response_to_eval_state
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
    CallableJudgeBackend,
    EvaluationFlowDef,
    GateMetricCondition,
    GatePolicyDef,
    JudgeBackend,
    JudgeSchemaDef,
    StateCheckGrader,
    describe_eval_target,
    run_evaluation_flow,
)
from aworld.evaluations.runtime_composition import RolloutState, RolloutTurn, derive_standard_metrics
from aworld.evaluations.sources import (
    AWorldTrajectoryLogSource,
    JsonlTaskAnswerSource,
    JsonlTaskSource,
    create_source_eval_suite,
    extract_aworld_trajectory_payload,
)
from aworld.evaluations.trajectory_judge import TrajectoryJudgeSchema
from aworld.runner import Runners
from pydantic import BaseModel
from aworld_cli.core.plugin_manager import PluginManager, get_builtin_plugin_roots
from aworld_cli.evaluator_rendering import render_evaluator_summary as _render_evaluator_summary
from aworld_cli.evaluator_workspace import (
    discover_workspace_suites,
    resolve_cli_target_path,
    resolve_workspace_suite_selection,
)
from aworld_cli.plugin_capabilities.hooks import PluginHookResult, load_plugin_hooks


_CLI_AGENT_RUNTIME_BOOTSTRAPPED = False
_SUPPORTED_SOURCE_KINDS = ("task", "answer", "trajectory")
_MAX_PROMPT_EVIDENCE_CONTENT_CHARS = 4000
_MAX_BUNDLE_FIRST_SYSTEM_PROMPT_CHARS = 0
_MAX_BUNDLE_FIRST_QUESTION_CHARS = 1500
_MAX_BUNDLE_FIRST_RAW_EVIDENCE_BLOCKS = 3
_MAX_BUNDLE_FIRST_STEP_COUNT = 8
_MAX_BUNDLE_FIRST_STEP_TEXT_CHARS = 180
_MAX_EVIDENCE_DIGEST_ENTRIES = 8
_MAX_EVIDENCE_DIGEST_VALUE_CHARS = 1200
_SELF_EVOLVE_REPLAY_MARKER = "Self-evolve replay evidence requirements:"
_TRAJECTORY_JUDGE_SYSTEM_CONTRACT = """AWorld trajectory evaluator runtime contract:
- Prefer evidence_digest over artifact_backed_evidence and any legacy TRAJECTORY_LOG parsing instructions in the judge document.
- Treat extracted_trajectory as a bounded prompt fallback, not as the complete raw log.
- Do not parse trajectory_log_path yourself unless evidence_digest and framework-provided artifact_read_results are insufficient.
- To inspect listed artifacts, return a single JSON object with artifact_read_requests, for example {"artifact_read_requests":[{"path":"<listed artifact path>","max_chars":4000}]}.
- Request only files listed in artifact_backed_evidence.artifacts; the framework will deny every other path.
- After artifact_read_results are provided, return the final compact JSON assessment matching required_output_schema.
- Never call network, shell, browser, task execution, or mutation tools while judging.
"""


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
        automation["agent"] = source_selection.get("agent")
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
    - `evaluator.pre_run` event payload for source mode: `mode=source`, `input`, `kind`, `task_id`, judge selector fields, `agent`, `workspace_path`, `output_path`
    - `evaluator.post_run` event payload for target mode: `mode=target`, `report`, `target`, `suite`, `workspace_path`
    - `evaluator.post_run` event payload for source mode: `mode=source`, `report`, `input`, `kind`, `task_id`, judge selector fields, `agent`, `workspace_path`, `output_path`
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
    veto_triggered: bool = False


def _looks_like_aworld_trajectory_log(path: Path) -> bool:
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                return stripped.startswith("{") and "'trajectory'" in stripped and "'task_id'" in stripped
    except OSError:
        return False
    return False


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
        "required_output_schema": {
            "score": "number, weighted score from 0 to 100",
            "verdict": "string",
            "veto_triggered": "boolean, true only for one-vote veto failures",
        },
        "instruction": "Evaluate the existing answer/state and return exactly one JSON object.",
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _case_query(case) -> str:
    case_input = getattr(case, "input", {}) or {}
    for key in ("input", "query", "prompt"):
        if key in case_input and case_input[key] is not None:
            return str(case_input[key])
    raise ValueError("task source case is missing input/query/prompt")


def _case_source_metadata(case) -> dict[str, Any]:
    metadata = getattr(case, "metadata", {}) or {}
    source_record = metadata.get("source_record")
    if isinstance(source_record, Mapping) and isinstance(source_record.get("metadata"), Mapping):
        return dict(source_record["metadata"])
    return {}


def _judge_selector_count(
    *,
    judge_agent: str | None,
    judge_agent_name: str | None,
    judge_backend_ref: str | None,
) -> int:
    return sum(
        1
        for value in (judge_agent, judge_agent_name, judge_backend_ref)
        if value is not None and str(value).strip()
    )


def _validate_judge_selectors(
    *,
    judge_agent: str | None,
    judge_agent_name: str | None,
    judge_backend_ref: str | None,
) -> None:
    if _judge_selector_count(
        judge_agent=judge_agent,
        judge_agent_name=judge_agent_name,
        judge_backend_ref=judge_backend_ref,
    ) != 1:
        raise ValueError("exactly one judge selector is required: --judge-agent, --judge-agent-name, or --judge-backend-ref")


def _load_ref(ref: str) -> Any:
    module_name, separator, attr_path = ref.partition(":")
    if not separator or not module_name or not attr_path:
        raise ValueError(f"judge backend ref must use module:callable format: {ref}")
    module = importlib.import_module(module_name)
    value: Any = module
    for attr in attr_path.split("."):
        if not attr:
            raise ValueError(f"judge backend ref has an empty attribute segment: {ref}")
        value = getattr(value, attr)
    return value


def _can_call_without_arguments(value: Any) -> bool:
    try:
        signature = inspect.signature(value)
    except (TypeError, ValueError):
        return False
    for parameter in signature.parameters.values():
        if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
            continue
        if parameter.default is parameter.empty:
            return False
    return True


def _coerce_source_judge_backend(value: Any, *, backend_id: str) -> JudgeBackend:
    if hasattr(value, "execute"):
        return value
    if callable(value):
        return CallableJudgeBackend(backend_id=backend_id, judge=value)
    raise ValueError("judge backend ref must resolve to a JudgeBackend-compatible object or callable")


def _load_source_judge_backend_ref(ref: str) -> JudgeBackend:
    value = _load_ref(ref)
    if hasattr(value, "execute"):
        return value
    if callable(value) and _can_call_without_arguments(value):
        produced = value()
        if inspect.isawaitable(produced):
            raise ValueError("judge backend ref factory must be synchronous")
        return _coerce_source_judge_backend(produced, backend_id=f"judge-backend-ref:{ref}")
    return _coerce_source_judge_backend(value, backend_id=f"judge-backend-ref:{ref}")


def _build_cli_agent_judge_backend(
    *,
    agent_name: str,
    backend_id: str,
    prompt_builder,
    judge_timeout_seconds: float | None = None,
    system_prompt_prefix: str | None = None,
):
    executor_cache: dict[str, Any] = {}

    async def _executor(prompt, system_prompt):
        if isinstance(prompt, tuple):
            raise ValueError("CLI agent judge backend only supports text prompts")
        executor = executor_cache.get("executor")
        if executor is None:
            executor = await _load_cli_agent_executor(agent_name)
            executor_cache["executor"] = executor
        swarm = getattr(executor, "swarm", None)
        if swarm is not None:
            response = await Runners.run(input=str(prompt), swarm=swarm)
        else:
            response = await executor.chat(str(prompt))
        return str(getattr(response, "answer", response))

    return AgentJudgeBackend(
        backend_id=backend_id,
        system_prompt=(
            f"{system_prompt_prefix.rstrip()}\n\nCLI agent judge loaded from {agent_name}"
            if system_prompt_prefix
            else f"CLI agent judge loaded from {agent_name}"
        ),
        executor=_executor,
        prompt_builder=prompt_builder,
        timeout_seconds=judge_timeout_seconds,
    )


def _resolve_source_judge_backend(
    *,
    judge_agent_path: Path | None,
    judge_agent_name: str | None,
    judge_backend_ref: str | None,
    file_backend_id: str,
    named_backend_prefix: str,
    prompt_builder,
    judge_timeout_seconds: float | None = None,
) -> JudgeBackend:
    if judge_agent_path is not None:
        return AgentJudgeBackend.from_agent_markdown_as_instructions(
            judge_agent_path,
            backend_id=file_backend_id,
            prompt_builder=prompt_builder,
            timeout_seconds=judge_timeout_seconds,
            system_prompt_prefix=(
                _TRAJECTORY_JUDGE_SYSTEM_CONTRACT
                if file_backend_id == "trajectory-evaluator-agent-md"
                else None
            ),
        )
    if judge_agent_name is not None and str(judge_agent_name).strip():
        resolved_name = str(judge_agent_name).strip()
        return _build_cli_agent_judge_backend(
            agent_name=resolved_name,
            backend_id=f"{named_backend_prefix}:{resolved_name}",
            prompt_builder=prompt_builder,
            judge_timeout_seconds=judge_timeout_seconds,
            system_prompt_prefix=(
                _TRAJECTORY_JUDGE_SYSTEM_CONTRACT
                if file_backend_id == "trajectory-evaluator-agent-md"
                else None
            ),
        )
    if judge_backend_ref is not None and str(judge_backend_ref).strip():
        return _load_source_judge_backend_ref(str(judge_backend_ref).strip())
    raise ValueError("exactly one judge selector is required: --judge-agent, --judge-agent-name, or --judge-backend-ref")


class _CliAgentRuntimeHarness:
    def __init__(self, *, agent_name: str):
        self.agent_name = agent_name
        self._executor = None

    async def run_rollout(self, *, case, target: Mapping[str, Any]) -> RolloutState:
        query = _case_query(case)
        started_at = time.monotonic()
        source_metadata = _case_source_metadata(case)
        turns = [RolloutTurn(role="user", content=query)]
        executor = await self._get_executor()
        try:
            swarm = getattr(executor, "swarm", None)
            if swarm is not None:
                answer = await Runners.run(input=query, swarm=swarm)
            else:
                answer = await executor.chat(query)
        except Exception as exc:
            duration_ms = int((time.monotonic() - started_at) * 1000)
            state = RolloutState(
                case_id=str(getattr(case, "case_id", "case")),
                status="failed",
                turns=turns,
                trajectory=[turn.to_dict() for turn in turns],
                timing={"duration_ms": duration_ms},
                error={"type": exc.__class__.__name__, "message": str(exc)},
                outcome={"has_answer": False, "agent": self.agent_name},
                metadata={**source_metadata, "agent": self.agent_name},
            )
            state.standard_metrics.update(derive_standard_metrics(state))
            return state

        duration_ms = int((time.monotonic() - started_at) * 1000)
        eval_state = normalize_task_response_to_eval_state(
            case_id=str(getattr(case, "case_id", "case")),
            response=answer,
            target=target,
            metadata={**source_metadata, "agent": self.agent_name},
        )
        assistant_turn = RolloutTurn(role="assistant", content=eval_state.answer)
        turns.append(assistant_turn)
        trajectory = list(eval_state.trajectory) or [turn.to_dict() for turn in turns]
        extracted_trajectory = {}
        if trajectory:
            try:
                extracted_trajectory = extract_aworld_trajectory_payload(
                    trajectory,
                    task_id=eval_state.case_id,
                    is_sub_task=False,
                )
            except Exception:
                extracted_trajectory = {}
        evidence_blocks = len(extracted_trajectory.get("evidence") or [])
        is_finished = any(
            bool(step.get("is_agent_finished"))
            for step in extracted_trajectory.get("steps", [])
            if isinstance(step, Mapping)
        )
        state = RolloutState(
            case_id=eval_state.case_id,
            status=eval_state.status,
            answer=eval_state.answer,
            turns=turns,
            trajectory=trajectory,
            tool_calls=list(eval_state.tool_calls),
            usage=dict(eval_state.usage),
            timing={**dict(eval_state.timing), "duration_ms": duration_ms},
            error=eval_state.error,
            outcome={
                "has_answer": eval_state.answer is not None,
                "agent": self.agent_name,
                "task_id": eval_state.case_id,
                "question": query,
                "evidence_blocks": evidence_blocks,
                "num_steps": len(trajectory),
                "is_finished": is_finished or eval_state.status == "success",
                "final_answer_len": len(str(eval_state.answer or "")),
            },
            metadata=dict(eval_state.metadata),
        )
        state.standard_metrics.update(derive_standard_metrics(state))
        return state

    async def _get_executor(self):
        if self._executor is None:
            self._executor = await _load_cli_agent_executor(self.agent_name)
        return self._executor


def _build_cli_agent_runtime_harness(*, agent_name: str):
    return _CliAgentRuntimeHarness(agent_name=agent_name)


async def _load_cli_agent_executor(agent_name: str):
    from aworld.core.scheduler import get_scheduler
    from aworld_cli.main import _resolve_agent_dirs
    from aworld_cli.runtime.cli import CliRuntime

    _ensure_cli_agent_runtime_bootstrapped()
    runtime = CliRuntime(
        agent_name=agent_name,
        local_dirs=_resolve_agent_dirs(None),
        disable_live_display=True,
    )
    all_agents = await runtime._load_agents()
    selected_agent = next((item for item in all_agents if item.name == agent_name), None)
    if selected_agent is None:
        available = ", ".join(sorted(item.name for item in all_agents)) or "none"
        raise ValueError(f"agent '{agent_name}' not found; available agents: {available}")

    runtime._scheduler = get_scheduler()
    runtime._bind_scheduler_default_agent(selected_agent.name)
    executor = await runtime._create_executor(selected_agent)
    if executor is None:
        raise ValueError(f"failed to create executor for agent '{agent_name}'")
    executor._base_runtime = runtime
    executor._suppress_interactive_loading_status = True
    return executor


def _ensure_cli_agent_runtime_bootstrapped() -> None:
    global _CLI_AGENT_RUNTIME_BOOTSTRAPPED
    if _CLI_AGENT_RUNTIME_BOOTSTRAPPED:
        return
    from aworld_cli.main import _show_banner, init_middlewares
    from aworld_cli.runtime_bootstrap import RuntimeBootstrapError, bootstrap_runtime

    try:
        bootstrap_runtime(
            env_file=".env",
            skill_paths=None,
            show_banner=False,
            init_middlewares_fn=init_middlewares,
            show_banner_fn=_show_banner,
        )
    except RuntimeBootstrapError as exc:
        raise ValueError(str(exc)) from exc
    _CLI_AGENT_RUNTIME_BOOTSTRAPPED = True


def _build_trajectory_prompt(case_input: dict, target: dict, suite) -> str:
    outcome = (target.get("artifacts") or {}).get("outcome") or {}
    extracted_path = outcome.get("extracted_path")
    extracted_payload = {}
    if extracted_path:
        extracted_payload = json.loads(Path(str(extracted_path)).read_text(encoding="utf-8"))
    elif isinstance(target.get("trajectory"), list) and target.get("trajectory"):
        task_id = str(target.get("case_id") or case_input.get("id") or case_input.get("input_id") or case_input.get("_case_id") or "case")
        extracted_payload = extract_aworld_trajectory_payload(
            target["trajectory"],
            task_id=task_id,
            is_sub_task=False,
        )
        if not extracted_payload.get("final_answer") and target.get("answer") is not None:
            extracted_payload["final_answer"] = target.get("answer")
        case_value = case_input.get("input") or case_input.get("query") or case_input.get("prompt")
        if not extracted_payload.get("question") and case_value is not None:
            extracted_payload["question"] = str(case_value)
    evidence_bundle = _load_prompt_evidence_bundle(
        extracted_payload.get("evidence_bundle_path") or target.get("evidence_bundle_path")
    )
    if evidence_bundle:
        extracted_payload["evidence_bundle"] = evidence_bundle
    runtime_context = _trajectory_runtime_context(
        case_input=case_input,
        target=target,
        extracted_payload=extracted_payload,
    )
    prompt_trajectory, evidence_summary = _trajectory_prompt_payload(extracted_payload)
    artifact_backed_evidence = _artifact_backed_evidence_index(
        runtime_context=runtime_context,
        target=target,
        extracted_path=extracted_path,
        extracted_payload=extracted_payload,
        evidence_bundle=evidence_bundle,
        evidence_summary=evidence_summary,
    )
    evidence_digest = _evidence_digest(
        extracted_payload=extracted_payload,
        evidence_bundle=evidence_bundle,
        artifact_backed_evidence=artifact_backed_evidence,
    )
    payload = {
        "case": {key: value for key, value in case_input.items() if not str(key).startswith("_")},
        "evaluation_runtime_contract": _evaluation_runtime_contract(),
        "runtime_context": runtime_context,
        "evidence_digest": evidence_digest,
        "artifact_backed_evidence": artifact_backed_evidence,
        "extracted_trajectory": prompt_trajectory,
        "evidence_summary": evidence_summary,
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
            "has_evidence": (
                "boolean, true when extracted_trajectory.evidence_bundle is valid "
                "or extracted_trajectory.evidence contains usable source evidence"
            ),
            "evidence_block_count": (
                "integer count of usable evidence blocks, preferring canonical "
                "evidence_bundle entries when present"
            ),
            "evidence_compacted": "boolean, true when any evidence block is a compacted/truncated preview",
            "evidence_incomplete": "boolean, true when available evidence is insufficient to support specific final-answer claims",
            "evidence_quality": {
                "has_evidence": "boolean",
                "evidence_block_count": "integer",
                "evidence_compacted": "boolean",
                "evidence_incomplete": "boolean",
                "evidence_issues": "array of short strings",
            },
        },
        "instruction": (
            "Apply the trajectory evaluator contract to the extracted trajectory. "
            "Runtime_context contains framework-provided paths and compatibility aliases "
            "for judge agents that expect TRAJECTORY_LOG, TASK_ID, or OUT_DIR. "
            "Do not ask the user for TRAJECTORY_LOG, TASK_ID, OUT_DIR, report paths, or other parameters. "
            "Do not call external tools, network tools, task execution tools, or mutation tools. "
            "Use evidence_digest as the default evidence view for scoring. "
            "If your runtime provides read-only artifact access, inspect only files listed in "
            "artifact_backed_evidence.artifacts only when evidence_digest is insufficient. "
            "Otherwise, use the bounded extracted_trajectory payload. "
            "When extracted_trajectory.evidence_bundle.valid is true, treat that canonical bundle as the "
            "primary evidence; raw evidence and steps may be metadata-only execution context. "
            "Evidence content may be bounded for prompt size; use evidence_summary to account for compaction. "
            "If extracted_trajectory is insufficient, return a valid JSON failure assessment instead of requesting more input. "
            "Return only one compact JSON object matching required_output_schema. "
            "Do not include analysis, rationale prose, or tables. "
            "Do not include markdown, fenced code blocks, or extra JSON objects. "
            "Keep arrays short: at most 3 evidence_issues and no long quotes."
        ),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _evaluation_runtime_contract() -> dict[str, object]:
    return {
        "inputs_are_complete": True,
        "primary_evaluation_input": "evidence_digest",
        "secondary_evaluation_input": "artifact_backed_evidence",
        "bounded_prompt_input": "extracted_trajectory",
        "full_evidence_location": "artifact_backed_evidence.artifacts",
        "canonical_evidence_bundle_supported": True,
        "when_evidence_bundle_valid": (
            "Use extracted_trajectory.evidence_bundle as the authoritative evidence source. "
            "Raw evidence and steps are execution context and may omit large content."
        ),
        "runtime_context_is_informational": True,
        "do_not_request_missing_parameters": True,
        "do_not_call_external_tools": True,
        "may_use_read_only_artifact_access": True,
        "do_not_reread_raw_log": True,
        "output_format": "single_json_object",
        "on_insufficient_evidence": "return_valid_json_failure_assessment",
    }


def _evidence_digest(
    *,
    extracted_payload: Mapping[str, Any],
    evidence_bundle: Mapping[str, Any],
    artifact_backed_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    bundle_valid = _is_valid_prompt_evidence_bundle(evidence_bundle)
    entries: list[dict[str, Any]] = []
    if bundle_valid:
        for entry in evidence_bundle.get("entries") or []:
            if not isinstance(entry, Mapping):
                continue
            digest_entry = _evidence_digest_bundle_entry(entry)
            if digest_entry:
                entries.append(digest_entry)
            if len(entries) >= _MAX_EVIDENCE_DIGEST_ENTRIES:
                break
    else:
        for item in extracted_payload.get("evidence") or []:
            if not isinstance(item, Mapping):
                continue
            digest_entry = _evidence_digest_raw_evidence_entry(item)
            if digest_entry:
                entries.append(digest_entry)
            if len(entries) >= _MAX_EVIDENCE_DIGEST_ENTRIES:
                break

    artifacts = artifact_backed_evidence.get("artifacts")
    artifact_read_available = bool(artifacts) if isinstance(artifacts, list) else False
    return {
        "mode": "judge_ready_evidence_digest",
        "canonical_bundle_valid": bundle_valid,
        "entry_count": len(entries),
        "artifact_read_available": artifact_read_available,
        "entries": entries,
        "fallback_artifact_index": "artifact_backed_evidence.artifacts",
    }


def _evidence_digest_bundle_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
    evidence = entry.get("bounded_evidence")
    if not isinstance(evidence, Mapping):
        evidence = {}
    digest_entry = {
        "source_id": str(entry.get("source_id") or ""),
        "artifact_path": str(entry.get("artifact_path") or ""),
        "extraction_method": str(entry.get("extraction_method") or ""),
        "evidence": _compact_digest_mapping(evidence),
    }
    return {key: value for key, value in digest_entry.items() if value not in ("", {})}


def _evidence_digest_raw_evidence_entry(item: Mapping[str, Any]) -> dict[str, Any]:
    content = item.get("content")
    digest_entry = {
        "source_id": str(item.get("source_id") or item.get("source") or ""),
        "source": str(item.get("source") or ""),
        "tool_name": str(item.get("tool_name") or item.get("action_name") or ""),
        "evidence": {
            "excerpt": _compact_digest_value(content),
        },
    }
    return {key: value for key, value in digest_entry.items() if value not in ("", {})}


def _compact_digest_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    compacted: dict[str, Any] = {}
    for key, item in list(value.items())[:8]:
        compacted[str(key)] = _compact_digest_value(item)
    return compacted


def _compact_digest_value(value: Any) -> Any:
    if isinstance(value, str):
        if len(value) <= _MAX_EVIDENCE_DIGEST_VALUE_CHARS:
            return value
        omitted = len(value) - _MAX_EVIDENCE_DIGEST_VALUE_CHARS
        return f"{value[:_MAX_EVIDENCE_DIGEST_VALUE_CHARS]}\n... [omitted {omitted} chars from evidence digest] ..."
    if isinstance(value, Mapping):
        return _compact_digest_mapping(value)
    if isinstance(value, list):
        return [_compact_digest_value(item) for item in value[:8]]
    return value


def _trajectory_prompt_payload(extracted_payload: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = dict(extracted_payload or {})
    evidence_bundle = payload.get("evidence_bundle")
    bundle_first = _is_valid_prompt_evidence_bundle(evidence_bundle)
    if bundle_first:
        payload = _bundle_first_trajectory_payload(payload)

    evidence_items = []
    for item in payload.get("evidence") or []:
        if isinstance(item, Mapping):
            evidence_items.append(
                _compact_prompt_evidence_metadata(item)
                if bundle_first
                else _compact_prompt_evidence(item)
            )
    payload["evidence"] = evidence_items
    summary = _summarize_prompt_evidence(
        evidence_items,
        evidence_bundle=payload.get("evidence_bundle"),
    )
    if bundle_first:
        summary["bundle_first"] = True
        summary["raw_evidence_content_suppressed"] = True
    return payload, summary


def _is_valid_prompt_evidence_bundle(value: object) -> bool:
    return (
        isinstance(value, Mapping)
        and value.get("valid") is True
        and isinstance(value.get("entries"), list)
        and bool(value.get("entries"))
    )


def _bundle_first_trajectory_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    compacted = dict(payload)
    compacted["question"] = _compact_bundle_first_question(compacted.get("question"))
    compacted["system_prompt_excerpt"] = _compact_text(
        compacted.get("system_prompt_excerpt"),
        _MAX_BUNDLE_FIRST_SYSTEM_PROMPT_CHARS,
    )
    compacted["steps"] = _compact_bundle_first_steps(compacted.get("steps"))
    evidence = compacted.get("evidence")
    if isinstance(evidence, list):
        compacted["evidence"] = [
            item
            for item in evidence[:_MAX_BUNDLE_FIRST_RAW_EVIDENCE_BLOCKS]
            if isinstance(item, Mapping)
        ]
    else:
        compacted["evidence"] = []
    return compacted


def _compact_bundle_first_question(value: object) -> str:
    text = str(value or "")
    marker_index = text.find(_SELF_EVOLVE_REPLAY_MARKER)
    if marker_index >= 0:
        text = text[:marker_index].rstrip()
    return _compact_text(text, _MAX_BUNDLE_FIRST_QUESTION_CHARS)


def _compact_bundle_first_steps(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    compacted_steps: list[dict[str, Any]] = []
    for step in value[:_MAX_BUNDLE_FIRST_STEP_COUNT]:
        if not isinstance(step, Mapping):
            continue
        compacted_step: dict[str, Any] = {}
        for key in ("step", "pre_agent", "agent_id", "is_agent_finished"):
            if key in step:
                compacted_step[key] = step[key]
        assistant_content = _compact_text(
            step.get("assistant_content"),
            _MAX_BUNDLE_FIRST_STEP_TEXT_CHARS,
        )
        if assistant_content:
            compacted_step["assistant_content"] = assistant_content
        tool_calls = []
        for call in step.get("tool_calls") or []:
            if not isinstance(call, Mapping):
                continue
            tool_call = {
                key: call.get(key)
                for key in ("id", "name", "type")
                if call.get(key) is not None
            }
            if tool_call:
                tool_calls.append(tool_call)
        if tool_calls:
            compacted_step["tool_calls"] = tool_calls[:5]
        compacted_steps.append(compacted_step)
    return compacted_steps


def _compact_text(value: object, max_chars: int) -> str:
    text = str(value or "")
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    marker = f"\n... [omitted {len(text) - max_chars} chars] ...\n"
    remaining = max_chars - len(marker)
    if remaining <= 0:
        return text[:max_chars]
    head_chars = max(1, remaining // 2)
    tail_chars = max(1, remaining - head_chars)
    return f"{text[:head_chars]}{marker}{text[-tail_chars:]}"


def _artifact_backed_evidence_index(
    *,
    runtime_context: Mapping[str, str],
    target: Mapping[str, Any],
    extracted_path: object,
    extracted_payload: Mapping[str, Any],
    evidence_bundle: Mapping[str, Any],
    evidence_summary: Mapping[str, Any],
) -> dict[str, Any]:
    artifacts: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    trusted_roots = _artifact_trusted_roots(
        runtime_context=runtime_context,
        extracted_path=extracted_path,
        evidence_bundle=evidence_bundle,
    )

    def add_artifact(kind: str, path_value: object, **metadata: Any) -> None:
        if not isinstance(path_value, str) or not path_value.strip():
            return
        path = str(Path(path_value).expanduser())
        key = (kind, path)
        if key in seen:
            return
        seen.add(key)
        artifact = {
            "kind": kind,
            "path": path,
            "available": Path(path).expanduser().exists(),
        }
        artifact.update({k: v for k, v in metadata.items() if v not in (None, "")})
        artifacts.append(artifact)

    def add_source_artifact(path_value: object, **metadata: Any) -> None:
        if not _is_path_under_trusted_roots(path_value, trusted_roots):
            return
        add_artifact("source_artifact", path_value, **metadata)

    add_artifact("trajectory_log", runtime_context.get("trajectory_log_path"))
    add_artifact("extracted_trajectory_json", str(extracted_path) if extracted_path else None)
    add_artifact(
        "canonical_evidence_bundle",
        evidence_bundle.get("path") if isinstance(evidence_bundle, Mapping) else None,
        valid=bool(evidence_bundle.get("valid")) if isinstance(evidence_bundle, Mapping) else False,
        entry_count=evidence_bundle.get("entry_count") if isinstance(evidence_bundle, Mapping) else None,
    )
    add_artifact("report_output", runtime_context.get("report_output_path"))

    if isinstance(evidence_bundle, Mapping):
        for entry in evidence_bundle.get("artifact_entries") or evidence_bundle.get("entries") or []:
            if not isinstance(entry, Mapping):
                continue
            add_source_artifact(
                entry.get("artifact_path"),
                source_id=entry.get("source_id"),
                extraction_method=entry.get("extraction_method"),
            )

    return {
        "mode": "read_only_artifact_index",
        "prompt_payload_is_bounded": True,
        "read_policy": {
            "read_only": True,
            "external_network_allowed": False,
            "mutation_allowed": False,
            "allowed_artifact_kinds": sorted({str(item["kind"]) for item in artifacts}),
        },
        "artifacts": artifacts,
        "summary": {
            "task_id": str(extracted_payload.get("task_id") or runtime_context.get("task_id") or ""),
            "num_steps": extracted_payload.get("num_steps"),
            "evidence_block_count": evidence_summary.get("evidence_block_count"),
            "canonical_bundle_valid": evidence_summary.get("canonical_bundle_valid"),
            "canonical_bundle_entry_count": evidence_summary.get("canonical_bundle_entry_count"),
            "bundle_first": evidence_summary.get("bundle_first", False),
            "raw_evidence_content_suppressed": evidence_summary.get(
                "raw_evidence_content_suppressed",
                False,
            ),
        },
    }


def _artifact_trusted_roots(
    *,
    runtime_context: Mapping[str, str],
    extracted_path: object,
    evidence_bundle: Mapping[str, Any],
) -> list[Path]:
    roots: list[Path] = []

    def add_root(path_value: object, *, use_parent: bool = False) -> None:
        if not isinstance(path_value, str) or not path_value.strip():
            return
        path = Path(path_value).expanduser()
        root = path.parent if use_parent else path
        resolved = root.resolve(strict=False)
        if resolved not in roots:
            roots.append(resolved)

    add_root(runtime_context.get("out_dir"))
    add_root(str(extracted_path) if extracted_path else None, use_parent=True)
    add_root(
        evidence_bundle.get("path") if isinstance(evidence_bundle, Mapping) else None,
        use_parent=True,
    )
    add_root(runtime_context.get("report_output_path"), use_parent=True)
    return roots


def _is_path_under_trusted_roots(path_value: object, trusted_roots: list[Path]) -> bool:
    if not isinstance(path_value, str) or not path_value.strip():
        return False
    if not trusted_roots:
        return False
    path = Path(path_value).expanduser().resolve(strict=False)
    for root in trusted_roots:
        try:
            if path == root or path.is_relative_to(root):
                return True
        except ValueError:
            continue
    return False


def _load_prompt_evidence_bundle(value: object) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        return {}
    path = Path(value).expanduser()
    try:
        bundle = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "path": str(path),
            "valid": False,
            "entry_count": 0,
            "entries": [],
        }
    if not isinstance(bundle, Mapping):
        return {
            "path": str(path),
            "valid": False,
            "entry_count": 0,
            "entries": [],
        }
    raw_entries = [
        entry
        for entry in bundle.get("entries") or []
        if isinstance(entry, Mapping)
    ]
    entries = [
        _compact_prompt_bundle_entry(entry)
        for entry in raw_entries
    ]
    artifact_entries = [
        _prompt_bundle_artifact_entry(entry)
        for entry in raw_entries
    ]
    return {
        "path": str(path),
        "format": str(bundle.get("format") or ""),
        "version": bundle.get("version"),
        "valid": bool(bundle.get("valid")) and bool(entries),
        "entry_count": len(entries),
        "entries": entries[:5],
        "artifact_entries": artifact_entries,
    }


def _prompt_bundle_artifact_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source_id": str(entry.get("source_id") or ""),
        "artifact_path": str(entry.get("artifact_path") or ""),
        "extraction_method": str(entry.get("extraction_method") or ""),
    }


def _compact_prompt_bundle_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
    compacted = _prompt_bundle_artifact_entry(entry)
    bounded = entry.get("bounded_evidence")
    if isinstance(bounded, Mapping):
        compacted["bounded_evidence"] = _compact_bounded_evidence_payload(bounded)
    return compacted


def _compact_bounded_evidence_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    compacted: dict[str, Any] = {}
    for index, (key, value) in enumerate(payload.items()):
        if index >= 8:
            break
        compacted[str(key)] = _compact_bounded_evidence_value(value)
    return compacted


def _compact_bounded_evidence_value(value: Any) -> Any:
    if isinstance(value, str):
        if len(value) <= _MAX_PROMPT_EVIDENCE_CONTENT_CHARS:
            return value
        edge_chars = _MAX_PROMPT_EVIDENCE_CONTENT_CHARS // 2
        omitted = len(value) - (edge_chars * 2)
        return (
            f"{value[:edge_chars]}\n"
            f"... [omitted {omitted} chars from evidence bundle] ...\n"
            f"{value[-edge_chars:]}"
        )
    if isinstance(value, Mapping):
        return {
            str(k): _compact_bounded_evidence_value(v)
            for k, v in list(value.items())[:8]
        }
    if isinstance(value, list):
        return [_compact_bounded_evidence_value(item) for item in value[:8]]
    return value


def _compact_prompt_evidence(item: Mapping[str, Any]) -> dict[str, Any]:
    compacted = dict(item)
    content = str(compacted.get("content") or "")
    original_length = compacted.get("original_length")
    if not isinstance(original_length, int):
        original_length = len(content)
    compacted["original_length"] = original_length
    compacted["prompt_content_length"] = len(content)
    if len(content) <= _MAX_PROMPT_EVIDENCE_CONTENT_CHARS:
        compacted.setdefault("prompt_compacted", False)
        return compacted

    edge_chars = _MAX_PROMPT_EVIDENCE_CONTENT_CHARS // 2
    omitted = len(content) - (edge_chars * 2)
    compacted["content"] = (
        f"{content[:edge_chars]}\n"
        f"... [omitted {omitted} chars from evidence for prompt] ...\n"
        f"{content[-edge_chars:]}"
    )
    compacted["prompt_compacted"] = True
    compacted["prompt_content_length"] = len(compacted["content"])
    return compacted


def _compact_prompt_evidence_metadata(item: Mapping[str, Any]) -> dict[str, Any]:
    content = str(item.get("content") or "")
    original_length = item.get("original_length")
    if not isinstance(original_length, int):
        original_length = len(content)
    compacted: dict[str, Any] = {
        "original_length": original_length,
        "prompt_content_length": 0,
        "prompt_compacted": bool(content),
        "content_suppressed": bool(content),
    }
    for key in (
        "source",
        "source_id",
        "step",
        "message_index",
        "role",
        "tool_name",
        "action_name",
        "truncated",
    ):
        if key in item:
            compacted[key] = item[key]
    return compacted


def _summarize_prompt_evidence(
    evidence_items: list[Mapping[str, Any]],
    *,
    evidence_bundle: object = None,
) -> dict[str, Any]:
    sources = []
    total_original_chars = 0
    prompt_compacted_count = 0
    source_truncated_count = 0
    for item in evidence_items:
        source = str(item.get("source") or "")
        if source and source not in sources:
            sources.append(source)
        original_length = item.get("original_length")
        if isinstance(original_length, int):
            total_original_chars += original_length
        else:
            total_original_chars += len(str(item.get("content") or ""))
        if item.get("prompt_compacted"):
            prompt_compacted_count += 1
        if item.get("truncated"):
            source_truncated_count += 1
    summary = {
        "evidence_block_count": len(evidence_items),
        "sources": sources,
        "total_original_chars": total_original_chars,
        "prompt_compacted_count": prompt_compacted_count,
        "source_truncated_count": source_truncated_count,
        "max_prompt_evidence_content_chars": _MAX_PROMPT_EVIDENCE_CONTENT_CHARS,
    }
    if isinstance(evidence_bundle, Mapping):
        summary["canonical_bundle_valid"] = bool(evidence_bundle.get("valid"))
        entry_count = evidence_bundle.get("entry_count")
        if isinstance(entry_count, int):
            summary["canonical_bundle_entry_count"] = entry_count
    return summary


def _trajectory_runtime_context(
    *,
    case_input: Mapping[str, Any],
    target: Mapping[str, Any],
    extracted_payload: Mapping[str, Any],
) -> dict[str, str]:
    case_metadata = case_input.get("_case_metadata")
    if not isinstance(case_metadata, Mapping):
        case_metadata = {}
    source_record = case_metadata.get("source_record")
    if not isinstance(source_record, Mapping):
        source_record = {}
    source_input = source_record.get("input")
    if not isinstance(source_input, Mapping):
        source_input = {}
    source_metadata = source_record.get("metadata")
    if not isinstance(source_metadata, Mapping):
        source_metadata = {}

    trajectory_log_path = (
        case_input.get("trajectory_log")
        or source_input.get("trajectory_log")
        or target.get("trajectory_log_path")
        or (
            target.get("target_path")
            if str(target.get("source_kind") or "").strip().lower() == "trajectory"
            else None
        )
        or ""
    )
    task_id = (
        case_input.get("task_id")
        or source_input.get("task_id")
        or extracted_payload.get("task_id")
        or target.get("task_id")
        or target.get("case_id")
        or ""
    )
    out_dir = (
        target.get("source_out_dir")
        or source_metadata.get("extraction_dir")
        or target.get("out_dir")
        or ""
    )
    report_output_path = target.get("report_output_path") or target.get("output_path") or ""
    return {
        "trajectory_log_path": str(trajectory_log_path),
        "task_id": str(task_id),
        "out_dir": str(out_dir),
        "report_output_path": str(report_output_path),
        "TRAJECTORY_LOG": str(trajectory_log_path),
        "TASK_ID": str(task_id),
        "OUT_DIR": str(out_dir),
    }


def _build_source_suite(
    *,
    kind: str,
    input_path: Path,
    judge_agent_path: Path | None,
    judge_agent_name: str | None = None,
    judge_backend_ref: str | None = None,
    task_id: str | None,
    id_field: str,
    task_field: str,
    answer_field: str,
    out_dir: str | None,
    agent: str | None = None,
    judge_timeout_seconds: float | None = None,
):
    agent_name = agent or "Aworld"
    trajectory_gate = GatePolicyDef(
        pass_all=(
            GateMetricCondition(metric_name="score", op=">=", threshold=70.0),
            GateMetricCondition(metric_name="A1_groundedness", op=">=", threshold=3),
            GateMetricCondition(metric_name="veto_triggered", op="==", threshold=False),
            GateMetricCondition(metric_name="has_evidence", op="==", threshold=1.0),
            GateMetricCondition(metric_name="agent_finished", op="==", threshold=1.0),
        )
    )
    trajectory_outcome_scorers = (
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
    )
    answer_gate = GatePolicyDef(
        pass_all=(
            GateMetricCondition(metric_name="score", op=">=", threshold=70.0),
            GateMetricCondition(metric_name="veto_triggered", op="==", threshold=False),
        )
    )
    if kind == "task":
        source = JsonlTaskSource(
            path=input_path,
            id_field=id_field,
            input_field=task_field,
        )
        judge_backend = _resolve_source_judge_backend(
            judge_agent_path=judge_agent_path,
            judge_agent_name=judge_agent_name,
            judge_backend_ref=judge_backend_ref,
            file_backend_id="source-agent-md",
            named_backend_prefix="source-agent",
            prompt_builder=_build_source_prompt,
            judge_timeout_seconds=judge_timeout_seconds,
        )
        return create_source_eval_suite(
            suite_id="task-source-evaluator",
            source=source,
            runtime_harness=_build_cli_agent_runtime_harness(agent_name=agent_name),
            judge_backend=judge_backend,
            judge_schema=JudgeSchemaDef(output_model=_SourceJudgeOutput),
            gate_policy=answer_gate,
            metadata={"agent": agent_name},
        )

    if kind == "answer":
        source = JsonlTaskAnswerSource(
            path=input_path,
            id_field=id_field,
            input_field=task_field,
            answer_field=answer_field,
        )
        judge_backend = _resolve_source_judge_backend(
            judge_agent_path=judge_agent_path,
            judge_agent_name=judge_agent_name,
            judge_backend_ref=judge_backend_ref,
            file_backend_id="source-agent-md",
            named_backend_prefix="source-agent",
            prompt_builder=_build_source_prompt,
            judge_timeout_seconds=judge_timeout_seconds,
        )
        return create_source_eval_suite(
            suite_id="answer-source-evaluator",
            source=source,
            judge_backend=judge_backend,
            judge_schema=JudgeSchemaDef(output_model=_SourceJudgeOutput),
            gate_policy=answer_gate,
        )

    if kind == "trajectory":
        if task_id or _looks_like_aworld_trajectory_log(input_path):
            source = AWorldTrajectoryLogSource(
                path=input_path,
                task_ids=[task_id] if task_id else None,
                extraction_dir=out_dir,
            )
            runtime_harness = None
        else:
            source = JsonlTaskSource(
                path=input_path,
                id_field=id_field,
                input_field=task_field,
            )
            runtime_harness = _build_cli_agent_runtime_harness(agent_name=agent_name)
        judge_backend = _resolve_source_judge_backend(
            judge_agent_path=judge_agent_path,
            judge_agent_name=judge_agent_name,
            judge_backend_ref=judge_backend_ref,
            file_backend_id="trajectory-evaluator-agent-md",
            named_backend_prefix="trajectory-evaluator-agent",
            prompt_builder=_build_trajectory_prompt,
            judge_timeout_seconds=judge_timeout_seconds,
        )
        return create_source_eval_suite(
            suite_id="trajectory-source-evaluator",
            source=source,
            runtime_harness=runtime_harness,
            judge_backend=judge_backend,
            judge_schema=TrajectoryJudgeSchema.default(),
            outcome_scorers=trajectory_outcome_scorers,
            gate_policy=trajectory_gate,
            metadata={"agent": agent_name} if not task_id else None,
        )

    raise ValueError(f"unsupported source kind: {kind}; expected one of: {', '.join(_SUPPORTED_SOURCE_KINDS)}")


def run_evaluator_source_cli(
    *,
    input: str,
    kind: str,
    judge_agent: str | None = None,
    judge_agent_name: str | None = None,
    judge_backend_ref: str | None = None,
    out_dir: str | None = None,
    output: str | None = None,
    task_id: str | None = None,
    agent: str | None = None,
    id_field: str = "id",
    task_field: str = "input",
    answer_field: str = "answer",
    interactive_approval: bool = False,
    judge_timeout_seconds: float | None = None,
) -> dict:
    hooks = _load_evaluator_hooks()
    kind = (kind or "").strip().lower()
    input_path = Path(input).expanduser().resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"source input does not exist: {input_path}")
    _validate_judge_selectors(
        judge_agent=judge_agent,
        judge_agent_name=judge_agent_name,
        judge_backend_ref=judge_backend_ref,
    )
    judge_agent_path = Path(judge_agent).expanduser().resolve() if judge_agent else None
    if judge_agent_path is not None and not judge_agent_path.exists():
        raise FileNotFoundError(f"judge agent does not exist: {judge_agent_path}")

    workspace_path = str(input_path.parent if input_path.is_file() else input_path)
    event_base = {
        "mode": "source",
        "input": str(input_path),
        "kind": kind,
        "task_id": task_id,
        "judge_agent": str(judge_agent_path) if judge_agent_path is not None else None,
        "judge_agent_name": judge_agent_name,
        "judge_backend_ref": judge_backend_ref,
        "agent": agent,
        "workspace_path": workspace_path,
        "output_path": str(Path(output).expanduser().resolve()) if output else None,
        "judge_timeout_seconds": judge_timeout_seconds,
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
            "judge_agent": str(judge_agent_path) if judge_agent_path is not None else None,
            "judge_agent_name": judge_agent_name,
            "judge_backend_ref": judge_backend_ref,
            "agent": agent,
            "interactive_approval": interactive_approval,
            "judge_timeout_seconds": judge_timeout_seconds,
        },
    )
    suite = _build_source_suite(
        kind=kind,
        input_path=input_path,
        judge_agent_path=judge_agent_path,
        judge_agent_name=judge_agent_name,
        judge_backend_ref=judge_backend_ref,
        task_id=task_id,
        id_field=id_field,
        task_field=task_field,
        answer_field=answer_field,
        out_dir=out_dir,
        agent=agent,
        judge_timeout_seconds=judge_timeout_seconds,
    )
    agent_name = agent or "Aworld"
    executes_agent = kind == "task" or (kind == "trajectory" and not task_id)
    target_info = {
        "target_kind": "source",
        "target_path": str(input_path),
        "source_kind": kind,
        "task_id": task_id,
        "judge_agent": str(judge_agent_path) if judge_agent_path is not None else None,
        "judge_agent_name": judge_agent_name,
        "judge_backend_ref": judge_backend_ref,
        "agent": agent_name if executes_agent else agent,
        "judge_timeout_seconds": judge_timeout_seconds,
        "source_out_dir": str(Path(out_dir).expanduser().resolve()) if out_dir else None,
        "report_output_path": str(Path(output).expanduser().resolve()) if output else None,
    }
    for key, value in hook_state.items():
        if key not in {
            "mode",
            "input",
            "kind",
            "task_id",
            "judge_agent",
            "judge_agent_name",
            "judge_backend_ref",
            "agent",
            "interactive_approval",
            "judge_timeout_seconds",
            "summary_suffix",
        }:
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
        "judge_agent": str(judge_agent_path) if judge_agent_path is not None else None,
        "judge_agent_name": judge_agent_name,
        "judge_backend_ref": judge_backend_ref,
        "agent": agent_name if executes_agent else agent,
        "judge_timeout_seconds": judge_timeout_seconds,
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
