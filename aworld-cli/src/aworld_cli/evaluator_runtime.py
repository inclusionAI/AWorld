from __future__ import annotations

import atexit
import asyncio
import builtins
import hashlib
import importlib
import inspect
import json
import os
import re
import secrets
import stat
import tempfile
import threading
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
    cleanup_evaluation_private_artifact_session,
    describe_eval_target,
    register_evaluation_private_artifact_session,
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
_MAX_PROMPT_EVIDENCE_BUNDLE_BYTES = 4 * 1024 * 1024
_MAX_PROMPT_EVIDENCE_BUNDLE_ENTRIES = 256
_MAX_PROMPT_EVIDENCE_MANIFEST_BYTES = 1024 * 1024
_MAX_PROMPT_EVIDENCE_MANIFEST_ENTRIES = 256
_PROMPT_EVIDENCE_BUNDLE_FORMAT = "aworld.self_evolve.evidence_bundle"
_PROMPT_EVIDENCE_BUNDLE_VERSION = 1
_VERIFIED_EVIDENCE_SNAPSHOT_ROOT = (
    Path(tempfile.gettempdir())
    / (
        "aworld-evaluator-verified-evidence-"
        f"{getattr(os, 'getuid', lambda: 0)()}"
    )
)
_VERIFIED_EVIDENCE_SESSION_FORMAT = (
    "aworld.evaluator.verified_evidence_session"
)
_VERIFIED_EVIDENCE_SESSION_VERSION = 1
_VERIFIED_EVIDENCE_SESSION_METADATA = ".session.json"
_VERIFIED_EVIDENCE_SESSION_STALE_AGE_SECONDS = 60 * 60
_VERIFIED_EVIDENCE_SESSION_NAME_PATTERN = re.compile(
    r"^session-(?P<pid>[1-9][0-9]*)-(?P<created_ns>[0-9]{16,20})-"
    r"(?P<session_id>[0-9a-f]{32})$"
)
_VERIFIED_EVIDENCE_LEGACY_ROOT_NAME_PATTERN = re.compile(
    r"^aworld-evaluator-verified-evidence-(?P<uid>[0-9]+)-"
    r"(?P<pid>[1-9][0-9]*)-(?P<token>[0-9a-f]{16})$"
)
_ACTIVE_VERIFIED_EVIDENCE_SESSIONS: dict[str, Path] = {}
_ACTIVE_VERIFIED_EVIDENCE_SESSIONS_LOCK = threading.Lock()
_VERIFIED_EVIDENCE_SNAPSHOT_ROOT_LOCK = threading.RLock()
_VERIFIED_EVIDENCE_STALE_RECLAIMED = False
_PROMPT_EVIDENCE_MANIFEST_PAYLOAD_KEYS = (
    "excerpt",
    "excerpts",
    "bounded_excerpt",
    "bounded_excerpts",
    "field_list",
    "fields",
    "fields_extracted",
    "key_fields",
    "selected_fields",
    "claims_supported",
    "claims_supported_by",
    "summary",
    "structured_summary",
)
_PROMPT_EVIDENCE_MANIFEST_PAYLOAD_ALIASES = {
    "bounded_excerpt_fields": "bounded_excerpts",
}
_DEFAULT_ARTIFACT_READ_ROUNDS = 2
_CANONICAL_BUNDLE_ARTIFACT_READ_ROUNDS = 3
_DEFAULT_ARTIFACT_READ_TOTAL_CHARS = 80000
_CANONICAL_BUNDLE_ARTIFACT_READ_TOTAL_CHARS = 120000
_SELF_EVOLVE_REPLAY_MARKER = "Self-evolve replay evidence requirements:"
_TRAJECTORY_JUDGE_SYSTEM_CONTRACT = """AWorld trajectory evaluator runtime contract:
- Prefer evidence_digest over artifact_backed_evidence and any legacy TRAJECTORY_LOG parsing instructions in the judge document.
- Treat extracted_trajectory as a bounded prompt fallback, not as the complete raw log.
- Do not parse trajectory_log_path yourself unless evidence_digest and framework-provided artifact_read_results are insufficient.
- To inspect listed artifacts, return a single JSON object with artifact_read_requests, for example {"artifact_read_requests":[{"path":"<listed artifact path>","max_chars":4000}]}.
- Request only files listed in artifact_backed_evidence.artifacts; the framework will deny every other path.
- When a read is truncated, continue from next_start (or omit start to let the framework continue automatically); never request an overlapping range.
- Before declaring evidence incomplete only because a bounded projection omits needed detail, request the indexed source artifact or canonical bundle within the supplied read budget.
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
    judge_model_profile: str | None = None,
    system_prompt_prefix: str | None = None,
):
    executor_cache: dict[str, Any] = {}
    model_config = _resolve_named_judge_model_config(judge_model_profile)

    async def _executor(prompt, system_prompt):
        if isinstance(prompt, tuple):
            raise ValueError("CLI agent judge backend only supports text prompts")
        executor = executor_cache.get("executor")
        if executor is None:
            executor = await _load_cli_agent_executor(agent_name)
            _apply_model_config_to_local_executor(executor, model_config)
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
    judge_model_profile: str | None = None,
) -> JudgeBackend:
    if judge_agent_path is not None:
        model_config = _resolve_judge_model_config(
            judge_agent_path=judge_agent_path,
            judge_model_profile=judge_model_profile,
        )
        return AgentJudgeBackend.from_agent_markdown_as_instructions(
            judge_agent_path,
            backend_id=file_backend_id,
            prompt_builder=prompt_builder,
            timeout_seconds=judge_timeout_seconds,
            model_config=model_config,
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
            judge_model_profile=judge_model_profile,
            system_prompt_prefix=(
                _TRAJECTORY_JUDGE_SYSTEM_CONTRACT
                if file_backend_id == "trajectory-evaluator-agent-md"
                else None
            ),
        )
    if judge_backend_ref is not None and str(judge_backend_ref).strip():
        return _load_source_judge_backend_ref(str(judge_backend_ref).strip())
    raise ValueError("exactly one judge selector is required: --judge-agent, --judge-agent-name, or --judge-backend-ref")


def _resolve_judge_model_config(
    *,
    judge_agent_path: Path,
    judge_model_profile: str | None,
):
    profile = (judge_model_profile or "").strip()
    if not profile:
        profile = _agent_markdown_model_profile(judge_agent_path) or ""
    if not profile:
        return None
    from aworld_cli.core.model_profiles import resolve_model_profile

    return resolve_model_profile(profile)


def _resolve_named_judge_model_config(judge_model_profile: str | None):
    profile = (judge_model_profile or "").strip()
    if not profile:
        return None
    from aworld_cli.core.model_profiles import resolve_model_profile

    return resolve_model_profile(profile)


def _apply_model_config_to_local_executor(executor: Any, model_config: Any | None) -> None:
    if model_config is None or executor is None:
        return
    agents: list[Any] = []
    swarm = getattr(executor, "swarm", None)
    if swarm is not None:
        try:
            from aworld_cli.runtime.cli import _iter_swarm_config_agents

            agents.extend(_iter_swarm_config_agents(swarm))
        except Exception:
            pass
    if getattr(executor, "conf", None) is not None:
        agents.append(executor)

    seen: set[int] = set()
    for agent in agents:
        key = id(agent)
        if key in seen:
            continue
        seen.add(key)
        conf = getattr(agent, "conf", None)
        if conf is None:
            continue
        try:
            conf.llm_config = model_config
        except Exception:
            continue


def _agent_markdown_model_profile(path: Path) -> str | None:
    try:
        from aworld.utils.skill_loader import extract_front_matter

        lines = path.read_text(encoding="utf-8").splitlines()
        frontmatter, _ = extract_front_matter(lines)
    except Exception:
        return None
    value = frontmatter.get("model_profile")
    if value in (None, ""):
        return None
    return str(value).strip() or None


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
    evidence_bundle_path = (
        extracted_payload.get("evidence_bundle_path")
        or target.get("evidence_bundle_path")
    )
    extracted_payload.pop("evidence_bundle", None)
    evidence_bundle = _load_prompt_evidence_bundle(evidence_bundle_path)
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
            "evidence_repair_constraints": [
                {
                    "subject_kind": (
                        "artifact|bibliographic_claim|configuration_claim|"
                        "evidence_manifest|general_claim|quantitative_claim|quote|symbolic_claim"
                    ),
                    "failure_mode": (
                        "invalid_manifest|missing_source|projection_compacted|"
                        "source_mismatch|support_incomplete|unreadable_artifact|unsupported_claim"
                    ),
                    "source_layer": (
                        "artifact_capture|artifact_projection|candidate_output|"
                        "evidence_manifest|judge_runtime"
                    ),
                    "required_action": (
                        "capture_artifact|expand_bounded_projection|reconcile_source|"
                        "repair_artifact_reference|support_or_omit|validate_manifest"
                    ),
                    "owner": "candidate|framework|infrastructure|task",
                    "occurrence_count": "integer >= 1",
                }
            ],
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
            "Before setting evidence_incomplete solely because required detail is outside a bounded excerpt, "
            "request the corresponding indexed source artifact within artifact_backed_evidence.read_policy. "
            "Emit evidence_repair_constraints once per distinct constraint identity. Use owner=framework with "
            "failure_mode=projection_compacted only when required support exists in an indexed artifact but "
            "the bounded projection/read budget prevented inspection. Use owner=candidate with "
            "failure_mode=support_incomplete when the submitted evidence bundle or indexed artifacts do not "
            "contain support for final-answer claims. Do not put claim text or artifact contents in constraints. "
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
    digest = {
        "mode": "judge_ready_evidence_digest",
        "canonical_bundle_valid": bundle_valid,
        "entry_count": len(entries),
        "artifact_read_available": artifact_read_available,
        "entries": entries,
        "fallback_artifact_index": "artifact_backed_evidence.artifacts",
    }
    manifest = evidence_bundle.get("manifest")
    if isinstance(manifest, Mapping) and manifest:
        digest["manifest"] = dict(manifest)
    return digest


def _evidence_digest_bundle_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
    evidence = entry.get("bounded_evidence")
    if not isinstance(evidence, Mapping):
        evidence = {}
    digest_entry = {
        "source_id": str(entry.get("source_id") or ""),
        "artifact_path": str(entry.get("artifact_path") or ""),
        "evidence_type": str(entry.get("evidence_type") or ""),
        "extraction_method": str(entry.get("extraction_method") or ""),
        "metadata": (
            _compact_digest_mapping(entry["metadata"])
            if isinstance(entry.get("metadata"), Mapping)
            else {}
        ),
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
        artifact_path = Path(path).expanduser()
        try:
            artifact_stat = os.lstat(artifact_path)
            available = stat.S_ISREG(artifact_stat.st_mode)
        except OSError:
            artifact_stat = None
            available = False
        artifact = {
            "kind": kind,
            "path": path,
            "available": available,
        }
        if artifact_stat is not None:
            artifact["size_bytes"] = artifact_stat.st_size
        artifact.update({k: v for k, v in metadata.items() if v not in (None, "")})
        if not available:
            if artifact.get("present") is True:
                artifact["present"] = False
            if artifact.get("valid") is True:
                artifact["valid"] = False
            if artifact.get("readable") is True:
                artifact["readable"] = False
        artifacts.append(artifact)

    def add_source_artifact(path_value: object, **metadata: Any) -> None:
        if not _is_path_under_trusted_roots(path_value, trusted_roots):
            return
        add_artifact("source_artifact", path_value, **metadata)

    add_artifact("trajectory_log", runtime_context.get("trajectory_log_path"))
    add_artifact("extracted_trajectory_json", str(extracted_path) if extracted_path else None)
    manifest_for_index: Mapping[str, Any] | None = None
    manifest_was_valid = False
    if isinstance(evidence_bundle, Mapping):
        manifest = evidence_bundle.get("manifest")
        manifest_was_valid = (
            isinstance(manifest, Mapping)
            and manifest.get("valid") is True
        )
        if manifest_was_valid:
            private_session_id = _verified_snapshot_session_id(
                manifest.get("snapshot_session")
            )
            manifest_for_index = _validate_verified_manifest_snapshot(
                manifest
            )
            if manifest_for_index.get("valid") is not True:
                if private_session_id is not None:
                    cleanup_evaluation_private_artifact_session(
                        private_session_id
                    )
                if isinstance(manifest, dict):
                    manifest.clear()
                    manifest.update(manifest_for_index)
                if isinstance(evidence_bundle, dict):
                    evidence_bundle["valid"] = False
                if isinstance(evidence_summary, dict):
                    evidence_summary["canonical_bundle_valid"] = False
    add_artifact(
        "canonical_evidence_bundle",
        evidence_bundle.get("path") if isinstance(evidence_bundle, Mapping) else None,
        valid=bool(evidence_bundle.get("valid")) if isinstance(evidence_bundle, Mapping) else False,
        entry_count=evidence_bundle.get("entry_count") if isinstance(evidence_bundle, Mapping) else None,
    )
    if manifest_was_valid and isinstance(manifest_for_index, Mapping):
        manifest_path = manifest_for_index.get("path")
        snapshot_session_id = _verified_snapshot_session_id(
            manifest_for_index.get("snapshot_session")
        )
        if _is_verified_snapshot_path(
            manifest_path,
            session_id=snapshot_session_id,
        ):
            add_artifact(
                "evidence_manifest",
                manifest_path,
                present=manifest_for_index.get("present"),
                readable=manifest_for_index.get("readable"),
                regular_file=manifest_for_index.get("regular_file"),
                valid=manifest_for_index.get("valid"),
                entry_count=manifest_for_index.get("entry_count"),
                invalid_entry_count=manifest_for_index.get("invalid_entry_count"),
                size_bytes=manifest_for_index.get("size_bytes"),
                fingerprint=manifest_for_index.get("fingerprint"),
                validation_errors=manifest_for_index.get("validation_errors"),
                source_path=manifest_for_index.get("source_path"),
                content_addressed=True,
                integrity={
                    "algorithm": "sha256",
                    "fingerprint": manifest_for_index.get("fingerprint"),
                    "size_bytes": manifest_for_index.get("size_bytes"),
                    "max_bytes": _MAX_PROMPT_EVIDENCE_MANIFEST_BYTES,
                    "mode": 0o400,
                    "required": True,
                },
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

    canonical_bundle_valid = bool(evidence_summary.get("canonical_bundle_valid"))
    private_artifact_session = (
        manifest_for_index.get("snapshot_session")
        if (
            isinstance(manifest_for_index, Mapping)
            and manifest_for_index.get("valid") is True
            and isinstance(
                manifest_for_index.get("snapshot_session"),
                Mapping,
            )
        )
        else {}
    )
    return {
        "mode": "read_only_artifact_index",
        "private_artifact_session": private_artifact_session,
        "prompt_payload_is_bounded": True,
        "read_policy": {
            "read_only": True,
            "external_network_allowed": False,
            "mutation_allowed": False,
            "projection_strategy": "incremental_non_overlapping_ranges",
            "max_rounds": (
                _CANONICAL_BUNDLE_ARTIFACT_READ_ROUNDS
                if canonical_bundle_valid
                else _DEFAULT_ARTIFACT_READ_ROUNDS
            ),
            "max_requests_per_round": 8,
            "default_chars_per_read": 4000,
            "max_chars_per_read": 20000,
            "max_total_chars": (
                _CANONICAL_BUNDLE_ARTIFACT_READ_TOTAL_CHARS
                if canonical_bundle_valid
                else _DEFAULT_ARTIFACT_READ_TOTAL_CHARS
            ),
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
        bundle_size = path.stat().st_size
        if bundle_size > _MAX_PROMPT_EVIDENCE_BUNDLE_BYTES:
            raise ValueError("evidence bundle exceeds bounded read limit")
        with path.open("rb") as stream:
            bundle_bytes = stream.read(_MAX_PROMPT_EVIDENCE_BUNDLE_BYTES + 1)
        if len(bundle_bytes) > _MAX_PROMPT_EVIDENCE_BUNDLE_BYTES:
            raise ValueError("evidence bundle exceeds bounded read limit")
        bundle = json.loads(bundle_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
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
    validation_errors: list[str] = []
    if bundle.get("format") != _PROMPT_EVIDENCE_BUNDLE_FORMAT:
        validation_errors.append("bundle_format_mismatch")
    if bundle.get("version") != _PROMPT_EVIDENCE_BUNDLE_VERSION:
        validation_errors.append("bundle_version_mismatch")
    raw_entry_values = bundle.get("entries")
    entries_declared_valid = isinstance(raw_entry_values, list)
    if not entries_declared_valid:
        validation_errors.append("bundle_entries_not_list")
    all_raw_entries = (
        [
            entry
            for entry in raw_entry_values or []
            if isinstance(entry, Mapping)
        ]
        if entries_declared_valid
        else []
    )
    if entries_declared_valid and len(all_raw_entries) != len(raw_entry_values):
        validation_errors.append("bundle_entry_not_object")
    if entries_declared_valid and not raw_entry_values:
        validation_errors.append("bundle_entries_empty")
    entry_limit_exceeded = (
        len(all_raw_entries) > _MAX_PROMPT_EVIDENCE_BUNDLE_ENTRIES
    )
    if entry_limit_exceeded:
        validation_errors.append("bundle_entry_limit_exceeded")
    raw_entries = all_raw_entries[:_MAX_PROMPT_EVIDENCE_BUNDLE_ENTRIES]
    for index, entry in enumerate(raw_entries):
        entry_error = _prompt_evidence_bundle_entry_error(entry)
        if entry_error is not None:
            validation_errors.append(
                f"bundle_entry_schema_invalid:{index}:{entry_error}"
            )
    entries = [
        _compact_prompt_bundle_entry(entry)
        for entry in raw_entries
    ]
    artifact_entries = [
        _prompt_bundle_artifact_entry(entry)
        for entry in raw_entries
    ]
    manifest = _validate_prompt_evidence_manifest(
        bundle.get("manifest"),
        bundle_path=path,
        declared_manifest_path=bundle.get("manifest_path"),
        expected_entries=all_raw_entries,
    )
    if manifest.get("valid") is not True:
        validation_errors.append("manifest_validation_failed")
    return {
        "path": str(path),
        "format": str(bundle.get("format") or ""),
        "version": bundle.get("version"),
        "valid": (
            bundle.get("valid") is True
            and bool(entries)
            and entries_declared_valid
            and not entry_limit_exceeded
            and not validation_errors
            and manifest.get("valid") is True
        ),
        "entry_count": len(entries),
        "entries": entries[:5],
        "artifact_entries": artifact_entries,
        "manifest": manifest,
        "validation_errors": validation_errors,
    }


def _prompt_evidence_bundle_entry_error(entry: Mapping[str, Any]) -> str | None:
    if not str(entry.get("source_id") or "").strip():
        return "missing_source_id"
    if not str(entry.get("extraction_method") or "").strip():
        return "missing_extraction_method"
    bounded_evidence = entry.get("bounded_evidence")
    if not isinstance(bounded_evidence, Mapping) or not bounded_evidence:
        return "missing_bounded_evidence"
    if str(entry.get("evidence_type") or "").strip().lower() == "metadata":
        metadata = entry.get("metadata")
        if not isinstance(metadata, Mapping) or not metadata:
            return "missing_metadata"
        return None
    if not str(entry.get("artifact_path") or "").strip():
        return "missing_artifact_path"
    return None


def _validate_prompt_evidence_manifest(
    value: object,
    *,
    bundle_path: Path,
    declared_manifest_path: object,
    expected_entries: list[Mapping[str, Any]],
) -> dict[str, Any]:
    claimed = _compact_prompt_evidence_manifest(
        value,
        fallback_path=declared_manifest_path,
    )
    result: dict[str, Any] = {
        "path": str(claimed.get("path") or ""),
        "present": False,
        "readable": False,
        "regular_file": False,
        "valid": False,
        "entry_count": 0,
        "invalid_entry_count": 0,
        "size_bytes": 0,
        "validation_errors": [],
    }
    errors: list[str] = result["validation_errors"]

    def add_error(reason: str) -> None:
        if reason not in errors:
            errors.append(reason)

    if not isinstance(value, Mapping):
        add_error("manifest_metadata_missing")
    manifest_path_value = claimed.get("path")
    if not isinstance(manifest_path_value, str) or not manifest_path_value.strip():
        add_error("manifest_path_missing")
        return result
    manifest_path = Path(manifest_path_value).expanduser()
    if not manifest_path.is_absolute():
        manifest_path = bundle_path.parent / manifest_path
    result["path"] = str(manifest_path)

    if isinstance(declared_manifest_path, str) and declared_manifest_path.strip():
        declared_path = Path(declared_manifest_path).expanduser()
        if not declared_path.is_absolute():
            declared_path = bundle_path.parent / declared_path
        if declared_path.resolve(strict=False) != manifest_path.resolve(strict=False):
            add_error("manifest_path_mismatch")
    else:
        add_error("manifest_path_declaration_missing")

    trusted_root = bundle_path.parent.resolve(strict=False)
    if not _is_path_under_trusted_roots(str(manifest_path), [trusted_root]):
        add_error("manifest_path_untrusted")
        return result

    for key in ("present", "readable", "valid"):
        if claimed.get(key) is not True:
            add_error(f"manifest_metadata_{key}_false")
    if claimed.get("invalid_entry_count") != 0:
        add_error("manifest_metadata_invalid_entries")

    try:
        path_stat_before = os.lstat(manifest_path)
    except FileNotFoundError:
        add_error("manifest_missing")
        return result
    except OSError:
        add_error("manifest_unreadable")
        return result
    result["present"] = True
    result["regular_file"] = stat.S_ISREG(path_stat_before.st_mode)
    if result["regular_file"] is not True:
        add_error("manifest_not_regular_file")
        return result

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    manifest_bytes: bytes | None = None
    file_stat_before = None
    file_stat_after = None
    try:
        descriptor = os.open(manifest_path, flags)
        try:
            file_stat_before = os.fstat(descriptor)
            if not stat.S_ISREG(file_stat_before.st_mode):
                add_error("manifest_not_regular_file")
            elif file_stat_before.st_size > _MAX_PROMPT_EVIDENCE_MANIFEST_BYTES:
                result["readable"] = True
                result["size_bytes"] = file_stat_before.st_size
                add_error("manifest_size_limit_exceeded")
            else:
                with os.fdopen(descriptor, "rb", closefd=False) as stream:
                    manifest_bytes = stream.read(
                        _MAX_PROMPT_EVIDENCE_MANIFEST_BYTES + 1
                    )
                result["readable"] = True
                result["size_bytes"] = len(manifest_bytes)
                if len(manifest_bytes) > _MAX_PROMPT_EVIDENCE_MANIFEST_BYTES:
                    add_error("manifest_size_limit_exceeded")
            file_stat_after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except FileNotFoundError:
        add_error("manifest_missing")
        return result
    except OSError:
        add_error("manifest_unreadable")
        return result

    try:
        path_stat_after = os.lstat(manifest_path)
    except OSError:
        add_error("manifest_path_changed_during_read")
        path_stat_after = None
    if (
        file_stat_before is not None
        and file_stat_after is not None
        and (
            _stat_identity(path_stat_before) != _stat_identity(file_stat_before)
            or _stat_identity(file_stat_before) != _stat_identity(file_stat_after)
            or (
                path_stat_after is not None
                and _stat_identity(file_stat_after) != _stat_identity(path_stat_after)
            )
            or file_stat_before.st_size != file_stat_after.st_size
            or file_stat_before.st_mtime_ns != file_stat_after.st_mtime_ns
        )
    ):
        add_error("manifest_path_changed_during_read")

    if manifest_bytes is None:
        result["invalid_entry_count"] = len(errors)
        return result

    actual_fingerprint = (
        "sha256:" + hashlib.sha256(manifest_bytes).hexdigest()
    )
    result["fingerprint"] = actual_fingerprint
    if claimed.get("size_bytes") != len(manifest_bytes):
        add_error("manifest_size_mismatch")
    claimed_fingerprint = claimed.get("fingerprint")
    if not _is_sha256_fingerprint(claimed_fingerprint):
        add_error("manifest_fingerprint_invalid")
    elif claimed_fingerprint != actual_fingerprint:
        add_error("manifest_fingerprint_mismatch")

    records, record_errors, entry_limit_exceeded = (
        _decode_prompt_evidence_manifest_records(manifest_bytes)
    )
    for error in record_errors:
        add_error(error)
    if entry_limit_exceeded:
        add_error("manifest_entry_limit_exceeded")
    result["entry_count"] = len(records)
    if claimed.get("entry_count") != len(records):
        add_error("manifest_entry_count_mismatch")
    if len(expected_entries) != len(records):
        add_error("manifest_bundle_entry_count_mismatch")
    else:
        for index, (manifest_entry, bundle_entry) in enumerate(
            zip(records, expected_entries)
        ):
            canonical_entry, canonical_error = (
                _canonical_prompt_evidence_manifest_entry(
                    manifest_entry,
                    bundle_entry=bundle_entry,
                    bundle_path=bundle_path,
                )
            )
            if (
                canonical_error is not None
                or not _json_values_equal(
                    canonical_entry,
                    dict(bundle_entry),
                )
            ):
                add_error(
                    f"manifest_bundle_entry_content_mismatch:{index}"
                    + (
                        f":{canonical_error}"
                        if canonical_error is not None
                        else ""
                    )
                )
    result["invalid_entry_count"] = len(record_errors)
    result["valid"] = not errors and bool(records)
    if result["valid"] is True:
        (
            snapshot_path,
            snapshot_session,
            snapshot_error,
        ) = _materialize_verified_manifest_snapshot(
            manifest_bytes,
            fingerprint=actual_fingerprint,
        )
        if (
            snapshot_error is not None
            or snapshot_path is None
            or snapshot_session is None
        ):
            add_error(
                snapshot_error or "manifest_snapshot_materialization_failed"
            )
            result["valid"] = False
        else:
            result["source_path"] = result["path"]
            result["path"] = str(snapshot_path)
            result["content_addressed"] = True
            result["snapshot_session"] = snapshot_session
    return result


def _canonical_prompt_evidence_manifest_entry(
    manifest_entry: Mapping[str, Any],
    *,
    bundle_entry: Mapping[str, Any],
    bundle_path: Path,
) -> tuple[dict[str, Any], str | None]:
    bounded_evidence: dict[str, Any] = {}
    for key in _PROMPT_EVIDENCE_MANIFEST_PAYLOAD_KEYS:
        if key in manifest_entry:
            bounded_evidence[key] = manifest_entry[key]
    for alias, canonical_key in (
        _PROMPT_EVIDENCE_MANIFEST_PAYLOAD_ALIASES.items()
    ):
        if canonical_key not in bounded_evidence and alias in manifest_entry:
            bounded_evidence[canonical_key] = manifest_entry[alias]

    evidence_type = str(
        manifest_entry.get("evidence_type") or ""
    ).strip().lower()
    if evidence_type == "file":
        evidence_type = "artifact"
    if not evidence_type:
        evidence_type = (
            "metadata"
            if (
                not str(manifest_entry.get("artifact_path") or "").strip()
                and isinstance(manifest_entry.get("metadata"), Mapping)
            )
            else "artifact"
        )
    canonical: dict[str, Any] = {
        "source_id": str(manifest_entry.get("source_id") or ""),
        "extraction_method": str(
            manifest_entry.get("extraction_method") or ""
        ),
        "bounded_evidence": bounded_evidence,
    }
    if evidence_type == "metadata":
        metadata = manifest_entry.get("metadata")
        canonical["evidence_type"] = "metadata"
        canonical["metadata"] = (
            dict(metadata)
            if isinstance(metadata, Mapping) and metadata
            else dict(bounded_evidence)
        )
    elif evidence_type == "artifact":
        bundle_artifact_path = bundle_entry.get("artifact_path")
        if not _prompt_manifest_artifact_path_matches_bundle(
            manifest_entry.get("artifact_path"),
            bundle_artifact_path,
            bundle_path=bundle_path,
        ):
            return canonical, "artifact_path_mismatch"
        canonical["artifact_path"] = str(bundle_artifact_path)
        if not bounded_evidence:
            synthetic = _prompt_synthetic_artifact_excerpt(
                Path(str(bundle_artifact_path)).expanduser()
            )
            if synthetic is not None:
                bounded_evidence["bounded_excerpt"] = synthetic["text"]
                bounded_evidence["source"] = "artifact_preview"
                bounded_evidence["truncated"] = synthetic["truncated"]
    else:
        return canonical, "unsupported_evidence_type"
    fields_used = manifest_entry.get("fields_used")
    if fields_used and "fields_used" not in bounded_evidence:
        bounded_evidence["fields_used"] = fields_used
    return canonical, None


def _prompt_manifest_artifact_path_matches_bundle(
    manifest_path_value: object,
    bundle_path_value: object,
    *,
    bundle_path: Path,
) -> bool:
    if not isinstance(manifest_path_value, str) or not manifest_path_value.strip():
        return False
    if not isinstance(bundle_path_value, str) or not bundle_path_value.strip():
        return False
    manifest_path = Path(manifest_path_value).expanduser()
    if not manifest_path.is_absolute():
        manifest_path = bundle_path.parent / manifest_path
    bundle_artifact_path = Path(bundle_path_value).expanduser()
    if not bundle_artifact_path.is_absolute():
        bundle_artifact_path = bundle_path.parent / bundle_artifact_path
    manifest_resolved = manifest_path.resolve(strict=False)
    bundle_resolved = bundle_artifact_path.resolve(strict=False)
    if manifest_resolved == bundle_resolved:
        return True
    archive_root = (bundle_path.parent / "workspace_evidence").resolve(
        strict=False
    )
    if bundle_resolved.parent != archive_root:
        return False
    archive_prefix = (
        hashlib.sha256(str(manifest_resolved).encode("utf-8")).hexdigest()[:12]
        + "__"
    )
    path_parts = [
        part
        for part in manifest_resolved.parts
        if part not in {manifest_resolved.anchor, os.sep}
    ]
    valid_archive_names = {
        archive_prefix
        + "__".join(
            _safe_prompt_artifact_path_part(part)
            for part in path_parts[start:]
        )
        for start in range(len(path_parts))
    }
    return bundle_resolved.name in valid_archive_names


def _safe_prompt_artifact_path_part(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return safe or "artifact"


def _json_values_equal(left: object, right: object) -> bool:
    try:
        return json.dumps(
            left,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ) == json.dumps(
            right,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError):
        return False


def _prompt_synthetic_artifact_excerpt(
    artifact_path: Path,
) -> dict[str, Any] | None:
    try:
        with artifact_path.open(
            "r",
            encoding="utf-8",
            errors="replace",
        ) as stream:
            raw = stream.read(4001)
    except OSError:
        return None
    text = raw.strip()
    if not text:
        return None
    truncated = len(text) > 4000
    return {
        "text": text[:4000] if truncated else text,
        "truncated": truncated,
    }


def _ensure_verified_evidence_snapshot_root() -> str | None:
    with _VERIFIED_EVIDENCE_SNAPSHOT_ROOT_LOCK:
        root = _VERIFIED_EVIDENCE_SNAPSHOT_ROOT
        try:
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            root_stat = os.lstat(root)
            if not stat.S_ISDIR(root_stat.st_mode):
                return "manifest_snapshot_root_not_directory"
            getuid = getattr(os, "getuid", None)
            if callable(getuid) and root_stat.st_uid != getuid():
                return "manifest_snapshot_root_owner_mismatch"
            if stat.S_IMODE(root_stat.st_mode) != 0o700:
                os.chmod(root, 0o700)
                root_stat = os.lstat(root)
                if stat.S_IMODE(root_stat.st_mode) != 0o700:
                    return "manifest_snapshot_root_permissions_invalid"
        except OSError:
            return "manifest_snapshot_root_unavailable"
    return None


def _create_verified_evidence_session(
) -> tuple[Path | None, dict[str, Any] | None, str | None]:
    with _VERIFIED_EVIDENCE_SNAPSHOT_ROOT_LOCK:
        root_error = _ensure_verified_evidence_snapshot_root()
        if root_error is not None:
            return None, None, root_error
        _reclaim_stale_verified_evidence_sessions()
        root_error = _ensure_verified_evidence_snapshot_root()
        if root_error is not None:
            return None, None, root_error
        session_id = secrets.token_hex(16)
        created_ns = time.time_ns()
        pid = os.getpid()
        session_name = f"session-{pid}-{created_ns}-{session_id}"
        session_root = _VERIFIED_EVIDENCE_SNAPSHOT_ROOT / session_name
        uid = getattr(os, "getuid", lambda: 0)()
        metadata = {
            "format": _VERIFIED_EVIDENCE_SESSION_FORMAT,
            "version": _VERIFIED_EVIDENCE_SESSION_VERSION,
            "session_id": session_id,
            "pid": pid,
            "uid": uid,
            "created_ns": created_ns,
            "session_name": session_name,
        }
        metadata_bytes = json.dumps(
            metadata,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        try:
            session_root.mkdir(mode=0o700)
            metadata_path = (
                session_root / _VERIFIED_EVIDENCE_SESSION_METADATA
            )
            flags = (
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0)
            )
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(metadata_path, flags, 0o400)
            try:
                written = 0
                while written < len(metadata_bytes):
                    write_count = os.write(
                        descriptor,
                        metadata_bytes[written:],
                    )
                    if write_count <= 0:
                        raise OSError("zero-byte session metadata write")
                    written += write_count
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            directory_flags = (
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            )
            if hasattr(os, "O_NOFOLLOW"):
                directory_flags |= os.O_NOFOLLOW
            directory_descriptor = os.open(
                session_root,
                directory_flags,
            )
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except OSError:
            _cleanup_verified_evidence_session(
                session_id,
                session_root,
                allow_missing_metadata=True,
                expected_creator_pid=pid,
            )
            return None, None, "manifest_snapshot_session_create_failed"
        with _ACTIVE_VERIFIED_EVIDENCE_SESSIONS_LOCK:
            _ACTIVE_VERIFIED_EVIDENCE_SESSIONS[session_id] = session_root
    contract = {
        "format": "aworld.evaluation.private_artifact_session",
        "version": 1,
        "session_id": session_id,
    }
    return session_root, contract, None


def _read_verified_evidence_session_metadata(
    session_root: Path,
) -> dict[str, Any] | None:
    metadata_path = session_root / _VERIFIED_EVIDENCE_SESSION_METADATA
    try:
        metadata_stat = os.lstat(metadata_path)
        if not stat.S_ISREG(metadata_stat.st_mode):
            return None
        if stat.S_IMODE(metadata_stat.st_mode) != 0o400:
            return None
        getuid = getattr(os, "getuid", None)
        if callable(getuid) and metadata_stat.st_uid != getuid():
            return None
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(metadata_path, flags)
        try:
            opened_stat = os.fstat(descriptor)
            if _verified_evidence_stat_identity(opened_stat) != (
                _verified_evidence_stat_identity(metadata_stat)
            ) or opened_stat.st_size != metadata_stat.st_size:
                return None
            metadata_bytes = os.read(descriptor, 8193)
        finally:
            os.close(descriptor)
        if len(metadata_bytes) > 8192:
            return None
        final_stat = os.lstat(metadata_path)
        if _verified_evidence_stat_identity(final_stat) != (
            _verified_evidence_stat_identity(metadata_stat)
        ) or final_stat.st_size != metadata_stat.st_size:
            return None
        metadata = json.loads(metadata_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return dict(metadata) if isinstance(metadata, Mapping) else None


def _verified_evidence_stat_identity(
    value: os.stat_result,
) -> tuple[int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
    )


def _verified_evidence_session_metadata_matches(
    session_root: Path,
    metadata: Mapping[str, Any],
    *,
    session_id: str | None = None,
) -> bool:
    match = _VERIFIED_EVIDENCE_SESSION_NAME_PATTERN.fullmatch(
        session_root.name
    )
    if match is None:
        return False
    try:
        root_stat = os.lstat(session_root)
    except OSError:
        return False
    if not stat.S_ISDIR(root_stat.st_mode):
        return False
    if stat.S_IMODE(root_stat.st_mode) != 0o700:
        return False
    getuid = getattr(os, "getuid", None)
    current_uid = getuid() if callable(getuid) else 0
    if callable(getuid) and root_stat.st_uid != current_uid:
        return False
    expected_session_id = match.group("session_id")
    if session_id is not None and session_id != expected_session_id:
        return False
    return (
        metadata.get("format") == _VERIFIED_EVIDENCE_SESSION_FORMAT
        and type(metadata.get("version")) is int
        and metadata.get("version") == _VERIFIED_EVIDENCE_SESSION_VERSION
        and metadata.get("session_id") == expected_session_id
        and type(metadata.get("pid")) is int
        and metadata.get("pid") == int(match.group("pid"))
        and type(metadata.get("uid")) is int
        and metadata.get("uid") == current_uid
        and type(metadata.get("created_ns")) is int
        and metadata.get("created_ns") == int(match.group("created_ns"))
        and metadata.get("session_name") == session_root.name
    )


def _cleanup_verified_evidence_session(
    session_id: str,
    session_root: Path,
    *,
    allow_missing_metadata: bool = False,
    expected_creator_pid: int | None = None,
) -> bool:
    with _VERIFIED_EVIDENCE_SNAPSHOT_ROOT_LOCK:
        base = Path(
            os.path.abspath(
                os.path.normpath(str(_VERIFIED_EVIDENCE_SNAPSHOT_ROOT))
            )
        )
        root = Path(
            os.path.abspath(
                os.path.normpath(str(session_root.expanduser()))
            )
        )
        if root.parent != base:
            return False
        match = _VERIFIED_EVIDENCE_SESSION_NAME_PATTERN.fullmatch(root.name)
        if match is None or match.group("session_id") != session_id:
            return False
        if (
            expected_creator_pid is not None
            and int(match.group("pid")) != expected_creator_pid
        ):
            return False
        try:
            root_stat = os.lstat(root)
        except FileNotFoundError:
            root_stat = None
        except OSError:
            return False
        if root_stat is not None:
            getuid = getattr(os, "getuid", None)
            if (
                not stat.S_ISDIR(root_stat.st_mode)
                or stat.S_IMODE(root_stat.st_mode) != 0o700
                or (
                    callable(getuid)
                    and root_stat.st_uid != getuid()
                )
            ):
                return False
            metadata = _read_verified_evidence_session_metadata(root)
            if metadata is None:
                if not allow_missing_metadata:
                    return False
            elif not _verified_evidence_session_metadata_matches(
                root,
                metadata,
                session_id=session_id,
            ):
                return False
        try:
            directory_descriptor: int | None = None
            if root_stat is not None:
                directory_flags = (
                    os.O_RDONLY
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                )
                if hasattr(os, "O_NOFOLLOW"):
                    directory_flags |= os.O_NOFOLLOW
                directory_descriptor = os.open(root, directory_flags)
                opened_root_stat = os.fstat(directory_descriptor)
                if _verified_evidence_stat_identity(opened_root_stat) != (
                    _verified_evidence_stat_identity(root_stat)
                ):
                    os.close(directory_descriptor)
                    directory_descriptor = None
                    return False
                entries = list(os.scandir(directory_descriptor))
            else:
                entries = []
            for entry in entries:
                entry_stat = entry.stat(follow_symlinks=False)
                if not stat.S_ISREG(entry_stat.st_mode):
                    return False
                if entry.name == _VERIFIED_EVIDENCE_SESSION_METADATA:
                    continue
                if re.fullmatch(
                    r"evidence-manifest-[0-9a-f]{64}\.jsonl",
                    entry.name,
                ):
                    continue
                if re.fullmatch(
                    r"\.evidence-manifest-[0-9a-f]{64}-[0-9]+-[0-9]+\.tmp",
                    entry.name,
                ):
                    continue
                return False
            for entry in entries:
                os.unlink(entry.name, dir_fd=directory_descriptor)
            if directory_descriptor is not None:
                os.close(directory_descriptor)
                directory_descriptor = None
            if root_stat is not None:
                final_root_stat = os.lstat(root)
                if _verified_evidence_stat_identity(final_root_stat) != (
                    _verified_evidence_stat_identity(root_stat)
                ):
                    return False
                os.rmdir(root)
        except FileNotFoundError:
            pass
        except OSError:
            return False
        finally:
            if directory_descriptor is not None:
                os.close(directory_descriptor)
        with _ACTIVE_VERIFIED_EVIDENCE_SESSIONS_LOCK:
            _ACTIVE_VERIFIED_EVIDENCE_SESSIONS.pop(session_id, None)
    return True


def _verified_evidence_pid_is_alive(pid: int) -> bool:
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def _reclaim_stale_verified_evidence_sessions() -> None:
    global _VERIFIED_EVIDENCE_STALE_RECLAIMED
    with _ACTIVE_VERIFIED_EVIDENCE_SESSIONS_LOCK:
        if _VERIFIED_EVIDENCE_STALE_RECLAIMED:
            return
        _VERIFIED_EVIDENCE_STALE_RECLAIMED = True
    root_error = _ensure_verified_evidence_snapshot_root()
    if root_error is not None:
        return
    try:
        candidates = list(_VERIFIED_EVIDENCE_SNAPSHOT_ROOT.iterdir())
    except OSError:
        return
    now_ns = time.time_ns()
    stale_age_ns = int(
        _VERIFIED_EVIDENCE_SESSION_STALE_AGE_SECONDS * 1_000_000_000
    )
    for candidate in candidates:
        metadata = _read_verified_evidence_session_metadata(candidate)
        if metadata is None:
            continue
        if not _verified_evidence_session_metadata_matches(
            candidate,
            metadata,
        ):
            continue
        created_ns = int(metadata["created_ns"])
        pid = int(metadata["pid"])
        if now_ns - created_ns < stale_age_ns:
            continue
        if _verified_evidence_pid_is_alive(pid):
            continue
        _cleanup_verified_evidence_session(
            str(metadata["session_id"]),
            candidate,
        )
    _reclaim_stale_legacy_verified_evidence_roots(
        now_ns=now_ns,
        stale_age_ns=stale_age_ns,
    )


def _reclaim_stale_legacy_verified_evidence_roots(
    *,
    now_ns: int,
    stale_age_ns: int,
) -> None:
    legacy_parent = _VERIFIED_EVIDENCE_SNAPSHOT_ROOT.parent
    try:
        candidates = list(legacy_parent.iterdir())
    except OSError:
        return
    getuid = getattr(os, "getuid", None)
    current_uid = getuid() if callable(getuid) else 0
    for candidate in candidates:
        match = _VERIFIED_EVIDENCE_LEGACY_ROOT_NAME_PATTERN.fullmatch(
            candidate.name
        )
        if match is None or int(match.group("uid")) != current_uid:
            continue
        pid = int(match.group("pid"))
        if _verified_evidence_pid_is_alive(pid):
            continue
        try:
            root_stat = os.lstat(candidate)
        except OSError:
            continue
        if (
            not stat.S_ISDIR(root_stat.st_mode)
            or stat.S_IMODE(root_stat.st_mode) != 0o700
            or (
                callable(getuid)
                and root_stat.st_uid != current_uid
            )
            or now_ns - root_stat.st_mtime_ns < stale_age_ns
        ):
            continue
        _cleanup_stale_legacy_verified_evidence_root(
            candidate,
            root_stat=root_stat,
            now_ns=now_ns,
            stale_age_ns=stale_age_ns,
            current_uid=current_uid,
        )


def _cleanup_stale_legacy_verified_evidence_root(
    root: Path,
    *,
    root_stat: os.stat_result,
    now_ns: int,
    stale_age_ns: int,
    current_uid: int,
) -> bool:
    try:
        entries = list(os.scandir(root))
    except OSError:
        return False
    entry_identities: dict[str, tuple[int, int, int, int]] = {}
    for entry in entries:
        match = re.fullmatch(
            r"evidence-manifest-(?P<digest>[0-9a-f]{64})\.jsonl",
            entry.name,
        )
        if match is None:
            return False
        try:
            entry_stat = entry.stat(follow_symlinks=False)
        except OSError:
            return False
        if (
            not stat.S_ISREG(entry_stat.st_mode)
            or stat.S_IMODE(entry_stat.st_mode) != 0o400
            or entry_stat.st_uid != current_uid
            or now_ns - entry_stat.st_mtime_ns < stale_age_ns
        ):
            return False
        _, snapshot_error = _read_verified_snapshot_bytes(
            Path(entry.path),
            expected_size=entry_stat.st_size,
            expected_fingerprint=f"sha256:{match.group('digest')}",
        )
        if snapshot_error is not None:
            return False
        entry_identities[entry.name] = _verified_evidence_stat_identity(
            entry_stat
        )

    directory_descriptor: int | None = None
    try:
        directory_flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        if hasattr(os, "O_NOFOLLOW"):
            directory_flags |= os.O_NOFOLLOW
        directory_descriptor = os.open(root, directory_flags)
        opened_root_stat = os.fstat(directory_descriptor)
        if _verified_evidence_stat_identity(opened_root_stat) != (
            _verified_evidence_stat_identity(root_stat)
        ):
            return False
        final_entries = list(os.scandir(directory_descriptor))
        if {entry.name for entry in final_entries} != set(entry_identities):
            return False
        for entry in final_entries:
            entry_stat = entry.stat(follow_symlinks=False)
            if _verified_evidence_stat_identity(entry_stat) != (
                entry_identities[entry.name]
            ):
                return False
        for entry in final_entries:
            os.unlink(entry.name, dir_fd=directory_descriptor)
        os.close(directory_descriptor)
        directory_descriptor = None
        final_root_stat = os.lstat(root)
        if _verified_evidence_stat_identity(final_root_stat) != (
            _verified_evidence_stat_identity(root_stat)
        ):
            return False
        os.rmdir(root)
    except OSError:
        return False
    finally:
        if directory_descriptor is not None:
            os.close(directory_descriptor)
    return True


def _cleanup_all_verified_evidence_sessions() -> None:
    with _ACTIVE_VERIFIED_EVIDENCE_SESSIONS_LOCK:
        sessions = list(_ACTIVE_VERIFIED_EVIDENCE_SESSIONS.items())
    for session_id, session_root in sessions:
        _cleanup_verified_evidence_session(
            session_id,
            session_root,
            expected_creator_pid=os.getpid(),
        )


atexit.register(_cleanup_all_verified_evidence_sessions)


def _materialize_verified_manifest_snapshot(
    manifest_bytes: bytes,
    *,
    fingerprint: str,
) -> tuple[Path | None, dict[str, Any] | None, str | None]:
    digest = fingerprint.removeprefix("sha256:")
    if not _is_sha256_fingerprint(fingerprint):
        return None, None, "manifest_snapshot_fingerprint_invalid"
    root, session_contract, session_error = (
        _create_verified_evidence_session()
    )
    if session_error is not None or root is None or session_contract is None:
        return None, None, (
            session_error or "manifest_snapshot_session_create_failed"
        )
    session_id = str(session_contract["session_id"])

    def fail(reason: str) -> tuple[None, None, str]:
        _cleanup_verified_evidence_session(
            session_id,
            root,
            expected_creator_pid=os.getpid(),
        )
        return None, None, reason

    snapshot_path = root / f"evidence-manifest-{digest}.jsonl"
    existing_bytes, existing_error = _read_verified_snapshot_bytes(
        snapshot_path,
        expected_size=len(manifest_bytes),
        expected_fingerprint=fingerprint,
    )
    if existing_error is None:
        return snapshot_path, session_contract, None
    if existing_error != "snapshot_missing":
        return fail("manifest_snapshot_existing_object_invalid")

    temporary_path = root / (
        f".evidence-manifest-{digest}-{os.getpid()}-{time.time_ns()}.tmp"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    cleanup_failed = False
    try:
        descriptor = os.open(temporary_path, flags, 0o600)
        view = memoryview(manifest_bytes)
        written = 0
        while written < len(view):
            write_count = os.write(descriptor, view[written:])
            if write_count <= 0:
                raise OSError("zero-byte manifest snapshot write")
            written += write_count
        os.fchmod(descriptor, 0o400)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        try:
            os.link(
                temporary_path,
                snapshot_path,
                follow_symlinks=False,
            )
        except FileExistsError:
            existing_bytes, existing_error = _read_verified_snapshot_bytes(
                snapshot_path,
                expected_size=len(manifest_bytes),
                expected_fingerprint=fingerprint,
            )
            if existing_error is not None:
                return fail("manifest_snapshot_existing_object_invalid")
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            directory_flags |= os.O_NOFOLLOW
        directory_descriptor = os.open(root, directory_flags)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except OSError:
        return fail("manifest_snapshot_write_failed")
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            cleanup_failed = True

    if cleanup_failed:
        return fail("manifest_snapshot_cleanup_failed")

    snapshot_bytes, snapshot_error = _read_verified_snapshot_bytes(
        snapshot_path,
        expected_size=len(manifest_bytes),
        expected_fingerprint=fingerprint,
    )
    if snapshot_error is not None or snapshot_bytes != manifest_bytes:
        return fail("manifest_snapshot_verification_failed")

    def cleanup_session() -> None:
        if not _cleanup_verified_evidence_session(
            session_id,
            root,
            expected_creator_pid=os.getpid(),
        ):
            raise RuntimeError("verified evidence session cleanup failed")

    try:
        register_evaluation_private_artifact_session(
            session_id,
            cleanup_session,
        )
    except (TypeError, ValueError):
        return fail("manifest_snapshot_session_register_failed")
    return snapshot_path, session_contract, None


def _read_verified_snapshot_bytes(
    path: Path,
    *,
    expected_size: int,
    expected_fingerprint: str,
) -> tuple[bytes | None, str | None]:
    try:
        path_stat_before = os.lstat(path)
    except FileNotFoundError:
        return None, "snapshot_missing"
    except OSError:
        return None, "snapshot_unreadable"
    if not stat.S_ISREG(path_stat_before.st_mode):
        return None, "snapshot_not_regular_file"
    if stat.S_IMODE(path_stat_before.st_mode) != 0o400:
        return None, "snapshot_permissions_invalid"
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        file_stat_before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(file_stat_before.st_mode)
            or file_stat_before.st_size > _MAX_PROMPT_EVIDENCE_MANIFEST_BYTES
        ):
            return None, "snapshot_size_invalid"
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            content = stream.read(_MAX_PROMPT_EVIDENCE_MANIFEST_BYTES + 1)
        file_stat_after = os.fstat(descriptor)
    except OSError:
        return None, "snapshot_unreadable"
    finally:
        if descriptor is not None:
            os.close(descriptor)
    try:
        path_stat_after = os.lstat(path)
    except OSError:
        return None, "snapshot_changed_during_read"
    if (
        _stat_identity(path_stat_before) != _stat_identity(file_stat_before)
        or _stat_identity(file_stat_before) != _stat_identity(file_stat_after)
        or _stat_identity(file_stat_after) != _stat_identity(path_stat_after)
        or file_stat_before.st_size != file_stat_after.st_size
        or file_stat_before.st_mtime_ns != file_stat_after.st_mtime_ns
    ):
        return None, "snapshot_changed_during_read"
    if len(content) != expected_size:
        return None, "snapshot_size_mismatch"
    actual_fingerprint = "sha256:" + hashlib.sha256(content).hexdigest()
    if actual_fingerprint != expected_fingerprint:
        return None, "snapshot_integrity_mismatch"
    return content, None


def _validate_verified_manifest_snapshot(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    result = dict(value)
    errors = [
        str(item)
        for item in value.get("validation_errors") or []
        if str(item).strip()
    ]
    result["validation_errors"] = errors

    def add_error(reason: str) -> None:
        if reason not in errors:
            errors.append(reason)

    if value.get("content_addressed") is not True:
        add_error("manifest_snapshot_not_content_addressed")
    snapshot_session = value.get("snapshot_session")
    session_id = _verified_snapshot_session_id(snapshot_session)
    if session_id is None:
        add_error("manifest_snapshot_session_invalid")
    snapshot_path_value = value.get("path")
    if not _is_verified_snapshot_path(
        snapshot_path_value,
        session_id=session_id,
    ):
        add_error("manifest_snapshot_path_untrusted")
    expected_size = value.get("size_bytes")
    if not isinstance(expected_size, int) or isinstance(expected_size, bool):
        add_error("manifest_snapshot_size_invalid")
        expected_size = -1
    expected_fingerprint = value.get("fingerprint")
    if not _is_sha256_fingerprint(expected_fingerprint):
        add_error("manifest_snapshot_fingerprint_invalid")
        expected_fingerprint = ""
    snapshot_content: bytes | None = None
    if not errors and isinstance(snapshot_path_value, str):
        snapshot_content, snapshot_error = _read_verified_snapshot_bytes(
            Path(snapshot_path_value).expanduser(),
            expected_size=expected_size,
            expected_fingerprint=expected_fingerprint,
        )
        if snapshot_error is not None:
            add_error(f"manifest_{snapshot_error}")
    result["present"] = snapshot_content is not None
    result["readable"] = snapshot_content is not None
    result["regular_file"] = snapshot_content is not None
    result["valid"] = not errors and snapshot_content is not None
    return result


def _verified_snapshot_session_id(value: object) -> str | None:
    if not isinstance(value, Mapping):
        return None
    if value.get("format") != "aworld.evaluation.private_artifact_session":
        return None
    if value.get("version") != 1:
        return None
    session_id = value.get("session_id")
    if (
        not isinstance(session_id, str)
        or re.fullmatch(r"[0-9a-f]{32}", session_id) is None
    ):
        return None
    return session_id


def _is_verified_snapshot_path(
    value: object,
    *,
    session_id: str | None = None,
) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    root = Path(
        os.path.abspath(os.path.normpath(str(_VERIFIED_EVIDENCE_SNAPSHOT_ROOT)))
    )
    path = Path(os.path.abspath(os.path.normpath(str(Path(value).expanduser()))))
    if path.parent.parent != root:
        return False
    session_match = _VERIFIED_EVIDENCE_SESSION_NAME_PATTERN.fullmatch(
        path.parent.name
    )
    if session_match is None:
        return False
    if (
        session_id is not None
        and session_match.group("session_id") != session_id
    ):
        return False
    return re.fullmatch(
        r"evidence-manifest-[0-9a-f]{64}\.jsonl",
        path.name,
    ) is not None


def _stat_identity(value: object) -> tuple[object, object]:
    return (getattr(value, "st_dev", None), getattr(value, "st_ino", None))


def _is_sha256_fingerprint(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        return False
    digest = value.removeprefix("sha256:")
    return len(digest) == 64 and all(
        character in "0123456789abcdef" for character in digest
    )


def _decode_prompt_evidence_manifest_records(
    manifest_bytes: bytes,
) -> tuple[list[Mapping[str, Any]], list[str], bool]:
    text = manifest_bytes.decode("utf-8", errors="replace")
    decoder = json.JSONDecoder()
    records: list[Mapping[str, Any]] = []
    errors: list[str] = []
    cursor = 0
    decoded_entry_count = 0
    entry_limit_exceeded = False
    while cursor < len(text):
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        if cursor >= len(text):
            break
        if decoded_entry_count >= _MAX_PROMPT_EVIDENCE_MANIFEST_ENTRIES:
            entry_limit_exceeded = True
            break
        line_number = text.count("\n", 0, cursor) + 1
        try:
            value, end = decoder.raw_decode(text, cursor)
        except json.JSONDecodeError:
            errors.append(f"manifest_json_invalid:{line_number}")
            break
        decoded_entry_count += 1
        if not isinstance(value, Mapping):
            errors.append(f"manifest_entry_not_object:{line_number}")
        else:
            entry_error = _prompt_evidence_manifest_entry_error(value)
            if entry_error is not None:
                errors.append(
                    f"manifest_entry_schema_invalid:{line_number}:{entry_error}"
                )
            records.append(value)
        cursor = end
    return records, errors, entry_limit_exceeded


def _prompt_evidence_manifest_entry_error(
    entry: Mapping[str, Any],
) -> str | None:
    if not str(entry.get("source_id") or "").strip():
        return "missing_source_id"
    if not str(entry.get("extraction_method") or "").strip():
        return "missing_extraction_method"
    evidence_type = str(entry.get("evidence_type") or "").strip().lower()
    if evidence_type == "metadata" or (
        not str(entry.get("artifact_path") or "").strip()
        and isinstance(entry.get("metadata"), Mapping)
    ):
        metadata = entry.get("metadata")
        has_bounded_payload = any(
            key in entry
            for key in _PROMPT_EVIDENCE_MANIFEST_PAYLOAD_KEYS
        )
        if (
            (not isinstance(metadata, Mapping) or not metadata)
            and not has_bounded_payload
        ):
            return "missing_metadata"
        return None
    if evidence_type not in ("", "file", "artifact"):
        return "unsupported_evidence_type"
    if not str(entry.get("artifact_path") or "").strip():
        return "missing_artifact_path"
    return None


def _compact_prompt_evidence_manifest(
    value: object,
    *,
    fallback_path: object = None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return (
            {"path": str(fallback_path)}
            if isinstance(fallback_path, str) and fallback_path.strip()
            else {}
        )
    manifest: dict[str, Any] = {}
    path = value.get("path")
    if isinstance(path, str) and path.strip():
        manifest["path"] = path
    elif isinstance(fallback_path, str) and fallback_path.strip():
        manifest["path"] = fallback_path
    for key in ("present", "readable", "regular_file", "valid"):
        if isinstance(value.get(key), bool):
            manifest[key] = value[key]
    for key in ("entry_count", "invalid_entry_count", "size_bytes"):
        count = value.get(key)
        if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
            manifest[key] = count
    fingerprint = value.get("fingerprint")
    if isinstance(fingerprint, str) and fingerprint.strip():
        manifest["fingerprint"] = _compact_text(
            fingerprint,
            _MAX_EVIDENCE_DIGEST_VALUE_CHARS,
        )
    validation_errors = value.get("validation_errors")
    if isinstance(validation_errors, list):
        manifest["validation_errors"] = [
            str(item)
            for item in validation_errors[:8]
            if str(item).strip()
        ]
    return manifest


def _prompt_bundle_artifact_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
    compacted = {
        "source_id": str(entry.get("source_id") or ""),
        "artifact_path": str(entry.get("artifact_path") or ""),
        "evidence_type": str(entry.get("evidence_type") or ""),
        "extraction_method": str(entry.get("extraction_method") or ""),
    }
    metadata = entry.get("metadata")
    if isinstance(metadata, Mapping):
        compacted["metadata"] = _compact_digest_mapping(metadata)
    return {key: value for key, value in compacted.items() if value not in ("", {})}


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
    judge_model_profile: str | None = None,
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
            judge_model_profile=judge_model_profile,
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
            judge_model_profile=judge_model_profile,
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
            judge_model_profile=judge_model_profile,
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
    judge_model_profile: str | None = None,
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
        "judge_model_profile": judge_model_profile,
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
            "judge_model_profile": judge_model_profile,
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
        judge_model_profile=judge_model_profile,
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
        "judge_model_profile": judge_model_profile,
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
        "judge_model_profile": judge_model_profile,
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
