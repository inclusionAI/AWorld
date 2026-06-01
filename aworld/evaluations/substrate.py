# coding: utf-8
from __future__ import annotations

import json
import inspect
import os
import re
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Awaitable, Callable, ClassVar, Mapping

from aworld.config.conf import EvaluationConfig
from aworld.evaluations.base import EvalDataCase, EvalDataset
from aworld.evaluations.base import NoActionEvalTarget
from aworld.runners.evaluate_runner import EvaluateRunner


JudgeCallable = Callable[[dict[str, Any], dict[str, Any]], Mapping[str, Any] | Awaitable[Mapping[str, Any]]]
JudgeExecutor = Callable[[str, str], Mapping[str, Any] | str | Awaitable[Mapping[str, Any] | str]]


@dataclass(frozen=True)
class EvalCaseDef:
    case_id: str
    input: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class JudgeSchemaDef:
    required_fields: tuple[str, ...] = tuple()

    def validate(self, payload: Mapping[str, Any]) -> None:
        missing = [field for field in self.required_fields if field not in payload]
        if missing:
            joined = ", ".join(missing)
            raise ValueError(f"missing required judge fields: {joined}")


@dataclass(frozen=True)
class GateDecision:
    status: str
    metric_name: str
    value: float


@dataclass(frozen=True)
class GatePolicyDef:
    metric_name: str
    pass_threshold: float
    approval_threshold: float | None = None

    def evaluate(self, metrics: Mapping[str, Any]) -> GateDecision:
        value = float(metrics[self.metric_name])
        if value >= self.pass_threshold:
            return GateDecision(status="pass", metric_name=self.metric_name, value=value)
        if self.approval_threshold is not None and value >= self.approval_threshold:
            return GateDecision(status="needs_approval", metric_name=self.metric_name, value=value)
        return GateDecision(status="fail", metric_name=self.metric_name, value=value)


@dataclass(frozen=True)
class JudgeExecution:
    backend_id: str
    payload: dict[str, Any]


class JudgeBackend:
    backend_id: ClassVar[str] = "judge-backend"

    def is_available(self) -> bool:
        return True

    async def execute(self, case_input: dict[str, Any], target: dict[str, Any], suite: "EvalSuiteDef") -> JudgeExecution:
        raise NotImplementedError


@dataclass(frozen=True)
class CallableJudgeBackend:
    backend_id: str
    judge: JudgeCallable

    def is_available(self) -> bool:
        return True

    async def execute(self, case_input: dict[str, Any], target: dict[str, Any], suite: "EvalSuiteDef") -> JudgeExecution:
        payload = await _maybe_await_judge(self.judge, case_input, target)
        return JudgeExecution(backend_id=self.backend_id, payload=dict(payload))


@dataclass(frozen=True)
class AgentJudgeBackend:
    backend_id: str
    system_prompt: str
    executor: JudgeExecutor | None = None
    prompt_builder: Callable[[dict[str, Any], dict[str, Any], "EvalSuiteDef"], str] | None = None
    timeout_seconds: float | None = None

    def is_available(self) -> bool:
        if self.executor is not None:
            return True
        model_name = os.getenv("LLM_MODEL_NAME")
        api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
        return bool(model_name and api_key)

    async def execute(self, case_input: dict[str, Any], target: dict[str, Any], suite: "EvalSuiteDef") -> JudgeExecution:
        if not self.is_available():
            raise RuntimeError(f"judge backend '{self.backend_id}' is not available")
        prompt_builder = self.prompt_builder or _build_default_judge_prompt
        prompt = prompt_builder(case_input, target, suite)
        executor = self.executor or _default_agent_judge_executor
        async def _run_executor():
            result = executor(prompt, self.system_prompt)
            if inspect.isawaitable(result):
                return await result
            return result

        if self.timeout_seconds is not None:
            task = asyncio.create_task(_run_executor())
            try:
                response = await asyncio.wait_for(task, timeout=self.timeout_seconds)
            except Exception:
                task.cancel()
                try:
                    await task
                except BaseException:
                    pass
                raise
        else:
            response = await _run_executor()
        payload = _coerce_judge_payload(response)
        return JudgeExecution(backend_id=self.backend_id, payload=payload)

    async def judge(self, case_input: dict[str, Any], target: dict[str, Any], suite: "EvalSuiteDef") -> dict[str, Any]:
        execution = await self.execute(case_input, target, suite)
        return execution.payload


@dataclass(frozen=True)
class FallbackJudgeBackend:
    backend_id: str
    backends: tuple[JudgeBackend, ...]

    def is_available(self) -> bool:
        return any(backend.is_available() for backend in self.backends)

    async def execute(self, case_input: dict[str, Any], target: dict[str, Any], suite: "EvalSuiteDef") -> JudgeExecution:
        errors: list[str] = []
        for backend in self.backends:
            if not backend.is_available():
                errors.append(f"{backend.backend_id}:unavailable")
                continue
            try:
                return await backend.execute(case_input, target, suite)
            except Exception as exc:
                errors.append(f"{backend.backend_id}:{exc}")
        joined = "; ".join(errors) if errors else "no candidate backend"
        raise RuntimeError(f"no judge backend succeeded: {joined}")


@dataclass(frozen=True)
class _LegacyJudgeBackendAdapter:
    backend: Any

    @property
    def backend_id(self) -> str:
        return getattr(self.backend, "backend_id", "legacy-judge-backend")

    def is_available(self) -> bool:
        available = getattr(self.backend, "is_available", None)
        if callable(available):
            return bool(available())
        return True

    async def execute(self, case_input: dict[str, Any], target: dict[str, Any], suite: "EvalSuiteDef") -> JudgeExecution:
        payload = self.backend.judge(case_input, target, suite)
        if inspect.isawaitable(payload):
            payload = await payload
        return JudgeExecution(backend_id=self.backend_id, payload=dict(payload))


@dataclass(frozen=True)
class EvalSuiteDef:
    suite_id: str
    cases: list[EvalCaseDef] = field(default_factory=list)
    judge_schema: JudgeSchemaDef = field(default_factory=JudgeSchemaDef)
    gate_policy: GatePolicyDef | None = None
    judge: JudgeCallable | None = None
    judge_backend: JudgeBackend | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def with_cases(self, cases: list[EvalCaseDef]) -> "EvalSuiteDef":
        return replace(self, cases=cases)

    def resolve_judge_backend(self) -> JudgeBackend:
        if self.judge_backend is not None:
            if hasattr(self.judge_backend, "execute"):
                return self.judge_backend
            if hasattr(self.judge_backend, "judge"):
                return _LegacyJudgeBackendAdapter(self.judge_backend)
            return self.judge_backend
        if self.judge is not None:
            return CallableJudgeBackend(
                backend_id=f"{self.suite_id}-callable",
                judge=self.judge,
            )
        raise ValueError(f"suite '{self.suite_id}' has no judge backend")


@dataclass(frozen=True)
class EvaluationFlowDef:
    target: dict[str, Any]
    suite: EvalSuiteDef
    interactive_approval: bool = False
    output_path: str | None = None


@dataclass(frozen=True)
class CompiledEvaluationPlan:
    suite: EvalSuiteDef
    target: dict[str, Any]
    dataset: EvalDataset
    eval_config: EvaluationConfig
    gate_policy: GatePolicyDef | None


def _normalize_target(target: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(target)
    value = normalized.pop("value", None)
    if isinstance(value, Mapping):
        normalized.update(value)
    return normalized


def build_eval_dataset(cases: list[EvalCaseDef], target: dict[str, Any]) -> EvalDataset:
    dataset_id = uuid.uuid4().hex
    normalized_target = _normalize_target(target)
    eval_cases = [
        EvalDataCase(
            eval_case_id=case.case_id,
            eval_dataset_id=dataset_id,
            case_data={**case.input, "_target": normalized_target, "_case_metadata": dict(case.metadata)},
        )
        for case in cases
    ]
    return EvalDataset(eval_dataset_id=dataset_id, eval_dataset_name="suite_eval_dataset", eval_cases=eval_cases)


def compile_evaluation_flow(flow: EvaluationFlowDef) -> CompiledEvaluationPlan:
    normalized_target = _normalize_target(flow.target)
    dataset = build_eval_dataset(flow.suite.cases, normalized_target)
    gate_policy = flow.suite.gate_policy or GatePolicyDef(metric_name="score", pass_threshold=0.0)
    eval_criteria = {
        "metric_name": gate_policy.metric_name,
        "threshold": gate_policy.pass_threshold,
        "scorer_params": {
            "suite": flow.suite,
            "name": flow.suite.suite_id,
        },
    }
    eval_config = EvaluationConfig(
        eval_suite_id=flow.suite.suite_id,
        eval_target=NoActionEvalTarget(),
        eval_criterias=[eval_criteria],
        eval_dataset=dataset,
    )
    return CompiledEvaluationPlan(
        suite=flow.suite,
        target=normalized_target,
        dataset=dataset,
        eval_config=eval_config,
        gate_policy=flow.suite.gate_policy,
    )


def _extract_metric_value(summary: Mapping[str, Any], metric_name: str) -> float:
    metric_summary = summary.get(metric_name, {})
    if "mean" in metric_summary:
        return float(metric_summary["mean"])
    if "true_rate" in metric_summary:
        return float(metric_summary["true_rate"])
    if "value" in metric_summary:
        return float(metric_summary["value"])
    raise KeyError(f"metric {metric_name} is missing aggregate summary")


async def run_evaluation_flow(flow: EvaluationFlowDef) -> dict[str, Any]:
    compiled = compile_evaluation_flow(flow)
    eval_result = await EvaluateRunner(config=compiled.eval_config).run()

    suite_summary = eval_result.summary.get(compiled.suite.suite_id, {})
    gate_metrics = {}
    gate = None
    if compiled.gate_policy is not None:
        gate_metrics[compiled.gate_policy.metric_name] = _extract_metric_value(
            suite_summary,
            compiled.gate_policy.metric_name,
        )
        gate = compiled.gate_policy.evaluate(gate_metrics)

    results = []
    report_backend_id = None
    for case_result in eval_result.eval_case_results:
        score_row = case_result.score_rows.get(compiled.suite.suite_id)
        judge_payload = {}
        if score_row is not None:
            metric_result = score_row.metric_results.get(compiled.gate_policy.metric_name if compiled.gate_policy else "score", {})
            judge_payload = dict(metric_result.get("metadata", {}))
            report_backend_id = report_backend_id or judge_payload.pop("_judge_backend", None)
        results.append(
            {
                "case_id": case_result.eval_case_id,
                "input": dict(case_result.input.case_data if hasattr(case_result.input, "case_data") else case_result.input),
                "judge": judge_payload,
            }
        )

    report = {
        "report_version": 1,
        "suite_id": compiled.suite.suite_id,
        "target": dict(compiled.target),
        "summary": eval_result.summary,
        "results": results,
        "approval": {
            "required": bool(gate and gate.status == "needs_approval"),
            "resolved": False,
            "approved": None,
        },
    }
    if report_backend_id is not None:
        report["judge_backend"] = {"backend_id": report_backend_id}
    if gate is not None:
        report["gate"] = {
            "status": gate.status,
            "metric_name": gate.metric_name,
            "value": gate.value,
        }
    return report


def _rank_for_score(score: float) -> str:
    if score >= 0.8:
        return "Exemplary"
    if score >= 0.6:
        return "Good"
    if score >= 0.4:
        return "Mediocre"
    return "Fail"


def _artifact_quality_score(target_path: Path) -> tuple[float, list[str], list[str]]:
    positive: list[str] = []
    improvements: list[str] = []
    score = 0.3

    if target_path.is_dir():
        files = [item for item in target_path.rglob("*") if item.is_file()]
    else:
        files = [target_path]

    suffixes = {item.suffix.lower() for item in files}
    names = {item.name.lower() for item in files}

    if ".html" in suffixes:
        score += 0.15
        positive.append("HTML entrypoints are present for direct artifact review.")
    else:
        improvements.append("Add a concrete HTML or UI artifact entrypoint for review.")

    if ".css" in suffixes:
        score += 0.15
        positive.append("CSS assets suggest dedicated presentation work instead of raw markup only.")
    else:
        improvements.append("Add explicit CSS styling rather than relying on unstyled defaults.")

    if {".js", ".ts", ".tsx", ".jsx"} & suffixes:
        score += 0.1
        positive.append("Interactive source files are present.")
    else:
        improvements.append("Add explicit interactive behavior coverage where the experience depends on it.")

    if {"readme.md", "README.md"} & names:
        score += 0.1
        positive.append("Project metadata or usage notes are present.")
    else:
        improvements.append("Document the artifact so evaluators can understand intended behavior quickly.")

    if len(files) >= 3:
        score += 0.1
        positive.append("The target contains multiple assets, which usually indicates a more complete deliverable.")
    else:
        improvements.append("Package the target with its supporting assets rather than a single thin file.")

    if any(item.suffix.lower() in {".png", ".jpg", ".jpeg", ".svg", ".webp"} for item in files):
        score += 0.1
        positive.append("Visual assets are included for richer presentation.")
    else:
        improvements.append("Include branded or supporting visual assets to improve evaluability.")

    return min(score, 0.95), positive, improvements


def _app_evaluator_judge(case_input: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    target_path = Path(target["target_path"])
    score, positive, improvements = _artifact_quality_score(target_path)
    rank = _rank_for_score(score)
    praise = positive[0] if positive else "The artifact is present and can be evaluated."
    criticism = improvements[0] if improvements else "The artifact still needs a stronger end-to-end product signal."
    advice = " ".join(improvements[:2]) if improvements else "Raise the visual polish and make the main experience more explicit."
    return {
        "score": round(score, 2),
        "rank": rank,
        "criticism": criticism,
        "praise": praise,
        "improvement_advice": advice,
    }


async def _maybe_await_judge(judge: JudgeCallable, case_input: dict[str, Any], target: dict[str, Any]) -> Mapping[str, Any]:
    payload = judge(case_input, target)
    if inspect.isawaitable(payload):
        return await payload
    return payload


def _load_app_evaluator_skill_prompt() -> str:
    skill_path = Path(__file__).resolve().parents[2] / "aworld-skills" / "app_evaluator" / "SKILL.md"
    return skill_path.read_text(encoding="utf-8")


def _snapshot_text_for_file(path: Path, *, max_chars: int = 1600) -> str | None:
    if path.suffix.lower() not in {".html", ".css", ".js", ".ts", ".tsx", ".jsx", ".md", ".json", ".txt"}:
        return None
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:max_chars]
    except Exception:
        return None


def _build_target_snapshot(target: dict[str, Any], *, max_files: int = 6) -> dict[str, Any]:
    target_path = Path(target["target_path"])
    files = [target_path]
    if target_path.is_dir():
        files = sorted([item for item in target_path.rglob("*") if item.is_file()])[:max_files]
    snapshot_files = []
    for item in files:
        snapshot_files.append(
            {
                "path": str(item),
                "name": item.name,
                "suffix": item.suffix.lower(),
                "preview": _snapshot_text_for_file(item),
            }
        )
    return {
        "target_path": str(target_path),
        "target_kind": target.get("target_kind", "directory" if target_path.is_dir() else "file"),
        "files": snapshot_files,
    }


def _build_default_judge_prompt(case_input: dict[str, Any], target: dict[str, Any], suite: EvalSuiteDef) -> str:
    snapshot = _build_target_snapshot(target)
    target_name = Path(target["target_path"]).name
    return (
        "Evaluate the following app artifact snapshot.\n"
        f"Suite: {suite.suite_id}\n"
        f"Target: {target['target_path']}\n"
        f"Case input: {json.dumps(case_input, ensure_ascii=False)}\n"
        f"Artifact snapshot: {json.dumps(snapshot, ensure_ascii=False)}\n"
        "Return a JSON object with a `results` array containing exactly one item for "
        f"`{target_name}` and include `score`, `rank`, `criticism`, `praise`, and `improvement_advice`."
    )


def _extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    try:
        loaded = json.loads(stripped)
        if isinstance(loaded, dict):
            return loaded
    except json.JSONDecodeError:
        pass

    matches = re.findall(r"\{.*\}", stripped, re.DOTALL)
    for candidate in matches:
        try:
            loaded = json.loads(candidate)
            if isinstance(loaded, dict):
                return loaded
        except json.JSONDecodeError:
            continue
    raise ValueError("judge response does not contain a valid JSON object")


def _coerce_judge_payload(response: Mapping[str, Any] | str) -> dict[str, Any]:
    if isinstance(response, str):
        response = _extract_json_object(response)
    else:
        response = dict(response)

    if "results" in response:
        results = response.get("results") or []
        if not results:
            raise ValueError("judge response results array is empty")
        return dict(results[0])
    return dict(response)


async def _default_agent_judge_executor(prompt: str, system_prompt: str) -> str:
    from aworld.agents.llm_agent import Agent
    from aworld.config.conf import AgentConfig
    from aworld.core.context.base import Context
    from aworld.utils.run_util import exec_agent

    api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    model_name = os.getenv("LLM_MODEL_NAME")
    if not api_key or not model_name:
        raise RuntimeError("LLM_MODEL_NAME and LLM_API_KEY/OPENAI_API_KEY are required for agent judge backend")

    agent = Agent(
        name="evaluation_judge",
        conf=AgentConfig(
            llm_provider=os.getenv("LLM_PROVIDER", "openai"),
            llm_model_name=model_name,
            llm_temperature=float(os.getenv("LLM_TEMPERATURE", "0.1")),
            llm_base_url=os.getenv("LLM_BASE_URL"),
            llm_api_key=api_key,
        ),
        system_prompt=system_prompt,
    )
    response = await exec_agent(prompt, agent=agent, context=Context())
    return str(response.answer)


def get_builtin_eval_suite(name: str, judge_backend: JudgeBackend | None = None) -> EvalSuiteDef:
    if name != "app-evaluator":
        raise KeyError(name)

    return EvalSuiteDef(
        suite_id="app-evaluator",
        judge_schema=JudgeSchemaDef(
            required_fields=(
                "score",
                "rank",
                "criticism",
                "praise",
                "improvement_advice",
            )
        ),
        gate_policy=GatePolicyDef(
            metric_name="score",
            pass_threshold=0.8,
            approval_threshold=0.6,
        ),
        judge_backend=judge_backend
        or FallbackJudgeBackend(
            backend_id="app-evaluator-fallback",
            backends=(
                AgentJudgeBackend(
                    backend_id="app-evaluator-agent",
                    system_prompt=_load_app_evaluator_skill_prompt(),
                    prompt_builder=_build_default_judge_prompt,
                    timeout_seconds=float(os.getenv("AWORLD_EVALUATOR_AGENT_TIMEOUT_SECONDS", "8.0")),
                ),
                CallableJudgeBackend(
                    backend_id="app-evaluator-heuristic",
                    judge=_app_evaluator_judge,
                ),
            ),
        ),
        metadata={
            "rubric_source": "aworld-skills/app_evaluator/SKILL.md",
            "preferred_backend": "app-evaluator-agent",
        },
    )


def resolve_eval_suite(name: str | None, target: str | Path) -> EvalSuiteDef:
    target_path = Path(target)
    suite_name = name or "app-evaluator"
    suite = get_builtin_eval_suite(suite_name)
    case = EvalCaseDef(
        case_id=target_path.name or "target",
        input={
            "target_path": str(target_path),
            "target_kind": "directory" if target_path.is_dir() else "file",
        },
    )
    return suite.with_cases([case])
