# coding: utf-8
from __future__ import annotations

import asyncio
import base64
import json
import inspect
import os
import re
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, ClassVar, Mapping

from aworld.config.conf import EvaluationConfig
from aworld.evaluations.base import EvalDataCase, EvalDataset
from aworld.evaluations.base import NoActionEvalTarget
from aworld.runners.evaluate_runner import EvaluateRunner


JudgeCallable = Callable[[dict[str, Any], dict[str, Any]], Mapping[str, Any] | Awaitable[Mapping[str, Any]]]
JudgePrompt = str | tuple[str, list[str]]
JudgeExecutor = Callable[[JudgePrompt, str], Mapping[str, Any] | str | Awaitable[Mapping[str, Any] | str]]
EvalSuiteFactory = Callable[[dict[str, Any]], "EvalSuiteDef"]
EvalSuiteMatcher = Callable[[dict[str, Any]], bool]

_IMAGE_SUFFIX_TO_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
}

EVALUATOR_REPORT_FORMAT_ID = "aworld.evaluator.report"
EVALUATOR_REPORT_FORMAT_VERSION = 1


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
    prompt_builder: Callable[[dict[str, Any], dict[str, Any], "EvalSuiteDef"], JudgePrompt] | None = None
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


@dataclass(frozen=True)
class EvalSuiteRegistration:
    suite_id: str
    factory: EvalSuiteFactory
    matcher: EvalSuiteMatcher | None = None
    priority: int = 0

    def matches(self, target: dict[str, Any]) -> bool:
        if self.matcher is None:
            return True
        return bool(self.matcher(target))


@dataclass(frozen=True)
class EvalSuiteSelection:
    suite_id: str
    suite: EvalSuiteDef
    target: dict[str, Any]
    mode: str


_EVAL_SUITE_REGISTRY: dict[str, EvalSuiteRegistration] = {}
_LOADED_EVAL_MANIFEST_PATHS: set[str] = set()


def register_eval_suite(
    suite_id: str,
    factory: EvalSuiteFactory,
    *,
    matcher: EvalSuiteMatcher | None = None,
    priority: int = 0,
) -> None:
    _EVAL_SUITE_REGISTRY[suite_id] = EvalSuiteRegistration(
        suite_id=suite_id,
        factory=factory,
        matcher=matcher,
        priority=priority,
    )


def list_eval_suites() -> list[str]:
    return sorted(_EVAL_SUITE_REGISTRY)


def _build_declared_eval_suite(manifest: Mapping[str, Any]) -> EvalSuiteDef:
    base_suite = str(manifest.get("base_suite") or "").strip()
    if base_suite != "app-evaluator":
        raise ValueError(f"unsupported base_suite: {base_suite}")

    suite = get_builtin_eval_suite(base_suite)
    suite_id = str(manifest.get("suite_id") or "").strip()
    if not suite_id:
        raise ValueError("suite_id is required")

    gate_manifest = manifest.get("gate_policy") or {}
    if gate_manifest:
        suite = replace(
            suite,
            gate_policy=GatePolicyDef(
                metric_name=str(gate_manifest.get("metric_name") or suite.gate_policy.metric_name),
                pass_threshold=float(gate_manifest.get("pass_threshold", suite.gate_policy.pass_threshold)),
                approval_threshold=(
                    float(gate_manifest["approval_threshold"])
                    if gate_manifest.get("approval_threshold") is not None
                    else suite.gate_policy.approval_threshold
                ),
            ),
        )

    metadata = dict(suite.metadata)
    metadata.update(dict(manifest.get("metadata") or {}))
    metadata["declared_manifest"] = True
    metadata["base_suite"] = base_suite
    return replace(suite, suite_id=suite_id, metadata=metadata)


def load_declared_eval_suites(workspace: str | Path | None = None) -> list[str]:
    root = Path(workspace or Path.cwd()).expanduser().resolve()
    manifest_dir = root / ".aworld" / "evaluators"
    if not manifest_dir.exists() or not manifest_dir.is_dir():
        return []

    loaded: list[str] = []
    for manifest_path in sorted(manifest_dir.glob("*.json")):
        manifest_key = str(manifest_path.resolve())
        if manifest_key in _LOADED_EVAL_MANIFEST_PATHS:
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        suite = _build_declared_eval_suite(manifest)
        target_kinds = tuple(str(kind) for kind in (manifest.get("target_kinds") or ["file", "directory", "image"]))
        register_eval_suite(
            suite.suite_id,
            lambda target, _suite=suite: _suite,
            matcher=lambda target, _target_kinds=target_kinds: target.get("target_kind") in _target_kinds,
            priority=int(manifest.get("priority", 100)),
        )
        _LOADED_EVAL_MANIFEST_PATHS.add(manifest_key)
        loaded.append(suite.suite_id)
    return loaded


def _sorted_eval_suite_registrations(registrations: list[EvalSuiteRegistration]) -> list[EvalSuiteRegistration]:
    return sorted(registrations, key=lambda item: (-item.priority, item.suite_id))


def _is_image_path(path: Path) -> bool:
    return path.suffix.lower() in _IMAGE_SUFFIX_TO_MIME


def _infer_target_kind(path: Path) -> str:
    if path.is_dir():
        return "directory"
    if _is_image_path(path):
        return "image"
    return "file"


def describe_eval_target(target: str | Path | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(target, Mapping):
        normalized = dict(target)
        value = normalized.pop("value", None)
        if isinstance(value, Mapping):
            normalized.update(value)
        target_path = normalized.get("target_path")
        if target_path is None:
            return normalized
        path = Path(str(target_path)).expanduser()
        normalized["target_path"] = str(path)
        normalized["target_kind"] = normalized.get("target_kind") or _infer_target_kind(path)
        return normalized

    path = Path(target).expanduser().resolve()
    return {
        "target_path": str(path),
        "target_kind": _infer_target_kind(path),
    }


def _normalize_target(target: dict[str, Any]) -> dict[str, Any]:
    return describe_eval_target(target)


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


def _normalize_metric_status(status: Any) -> str | None:
    if status is None:
        return None
    return getattr(status, "name", str(status))


def _format_report_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace("+00:00", "Z")


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
    cases_with_metrics = 0
    cases_with_judge = 0
    for case_result in eval_result.eval_case_results:
        score_row = case_result.score_rows.get(compiled.suite.suite_id)
        judge_payload = {}
        case_metrics: dict[str, Any] = {}
        case_backend_id = None
        if score_row is not None:
            cases_with_metrics += 1
            for metric_name, metric_result in score_row.metric_results.items():
                if isinstance(metric_result, Mapping):
                    case_metrics[metric_name] = {}
                    if "value" in metric_result:
                        case_metrics[metric_name]["value"] = metric_result["value"]
                    status = _normalize_metric_status(metric_result.get("eval_status"))
                    if status is not None:
                        case_metrics[metric_name]["status"] = status
                    metadata = metric_result.get("metadata") or {}
                    if case_backend_id is None and isinstance(metadata, Mapping):
                        case_backend_id = metadata.get("_judge_backend")
                else:
                    case_metrics[metric_name] = {"value": metric_result}
            metric_result = score_row.metric_results.get(compiled.gate_policy.metric_name if compiled.gate_policy else "score", {})
            judge_payload = dict(metric_result.get("metadata", {}))
            report_backend_id = report_backend_id or judge_payload.pop("_judge_backend", None)
        if judge_payload:
            cases_with_judge += 1
        results.append(
            {
                "case_id": case_result.eval_case_id,
                "input": dict(case_result.input.case_data if hasattr(case_result.input, "case_data") else case_result.input),
                "metrics": case_metrics,
                "judge": judge_payload,
                "judge_backend": {"backend_id": case_backend_id} if case_backend_id is not None else None,
            }
        )

    metrics = dict(suite_summary)
    report = {
        "report_version": 1,
        "report_format": {
            "id": EVALUATOR_REPORT_FORMAT_ID,
            "version": EVALUATOR_REPORT_FORMAT_VERSION,
        },
        "generated_at": _format_report_timestamp(eval_result.create_time),
        "suite_id": compiled.suite.suite_id,
        "target": dict(compiled.target),
        "summary": eval_result.summary,
        "metrics": metrics,
        "results": results,
        "result_counts": {
            "cases_total": len(results),
            "cases_with_metrics": cases_with_metrics,
            "cases_with_judge": cases_with_judge,
        },
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

    if target_path.is_file() and _is_image_path(target_path):
        positive.append("A rendered screenshot is present for direct visual review.")
        improvements.append("Provide a few more representative screens or brief implementation context for deeper evaluation.")
        return 0.65, positive, improvements

    score = 0.3

    if target_path.is_dir():
        files = [item for item in target_path.rglob("*") if item.is_file()]
    else:
        files = [target_path]

    suffixes = {item.suffix.lower() for item in files}
    names = {item.name.lower() for item in files}
    visual_files = [item for item in files if _is_image_path(item)]

    if visual_files and not {".html", ".css", ".js", ".ts", ".tsx", ".jsx"} & suffixes:
        score = 0.55
        positive.append("Rendered screenshots are available for direct visual review.")
        if len(visual_files) >= 3:
            score += 0.1
            positive.append("Multiple screens provide broader product coverage.")
        else:
            improvements.append("Include a few more representative states to improve evaluation coverage.")
        if {"readme.md", "README.md"} & names:
            score += 0.1
            positive.append("Project metadata or usage notes are present.")
        else:
            improvements.append("Add brief context so evaluators understand what the screens are showing.")
        return min(score, 0.95), positive, improvements

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

    if visual_files:
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


def _encode_image_as_data_url(path: Path) -> str | None:
    mime_type = _IMAGE_SUFFIX_TO_MIME.get(path.suffix.lower())
    if mime_type is None:
        return None
    try:
        encoded = base64.b64encode(path.read_bytes()).decode("utf-8")
    except Exception:
        return None
    return f"data:{mime_type};base64,{encoded}"


def _collect_target_image_urls(target: dict[str, Any], *, max_images: int = 4) -> list[str]:
    target_path = Path(target["target_path"])
    image_paths: list[Path] = []

    if target_path.is_file() and _is_image_path(target_path):
        image_paths = [target_path]
    elif target_path.is_dir():
        image_paths = sorted(
            item for item in target_path.rglob("*") if item.is_file() and _is_image_path(item)
        )[:max_images]

    image_urls: list[str] = []
    for path in image_paths:
        data_url = _encode_image_as_data_url(path)
        if data_url is not None:
            image_urls.append(data_url)
    return image_urls


def _build_app_evaluator_judge_prompt(
    case_input: dict[str, Any],
    target: dict[str, Any],
    suite: EvalSuiteDef,
) -> JudgePrompt:
    snapshot = _build_target_snapshot(target)
    target_name = Path(target["target_path"]).name
    image_urls = _collect_target_image_urls(target)
    prompt = (
        "Evaluate the following app artifact.\n"
        f"Suite: {suite.suite_id}\n"
        f"Target: {target['target_path']}\n"
        f"Case input: {json.dumps(case_input, ensure_ascii=False)}\n"
        f"Artifact snapshot: {json.dumps(snapshot, ensure_ascii=False)}\n"
        f"Attached visuals: {len(image_urls)}\n"
        "Use attached visuals as the primary evidence when present. Use the artifact snapshot for filenames and implementation context.\n"
        "Return a JSON object with a `results` array containing exactly one item for "
        f"`{target_name}` and include `score`, `rank`, `criticism`, `praise`, and `improvement_advice`."
    )
    if image_urls:
        return prompt, image_urls
    return prompt


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


async def _default_agent_judge_executor(prompt: JudgePrompt, system_prompt: str) -> str:
    from aworld.agents.llm_agent import Agent
    from aworld.config.conf import AgentConfig
    from aworld.core.common import Observation
    from aworld.core.context.base import Context
    from aworld.utils.run_util import exec_agent

    api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    model_name = os.getenv("LLM_MODEL_NAME")
    if not api_key or not model_name:
        raise RuntimeError("LLM_MODEL_NAME and LLM_API_KEY/OPENAI_API_KEY are required for agent judge backend")

    prompt_text: str
    image_urls: list[str] | None
    if isinstance(prompt, tuple):
        prompt_text, image_urls = prompt
    else:
        prompt_text, image_urls = prompt, None

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
    request: str | Observation = prompt_text
    if image_urls:
        request = Observation(content=prompt_text, images=image_urls)
    response = await exec_agent(request, agent=agent, context=Context())
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
                    prompt_builder=_build_app_evaluator_judge_prompt,
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


def _build_eval_suite_case(target_info: dict[str, Any]) -> EvalCaseDef:
    return EvalCaseDef(
        case_id=Path(target_info["target_path"]).name or "target",
        input={
            "target_path": target_info["target_path"],
            "target_kind": target_info["target_kind"],
        },
    )


def list_matching_eval_suites(target: str | Path | Mapping[str, Any]) -> list[str]:
    target_info = describe_eval_target(target)
    candidates = [registration for registration in _EVAL_SUITE_REGISTRY.values() if registration.matches(target_info)]
    return [registration.suite_id for registration in _sorted_eval_suite_registrations(candidates)]


def resolve_eval_suite_selection(name: str | None, target: str | Path | Mapping[str, Any]) -> EvalSuiteSelection:
    target_info = describe_eval_target(target)
    if name is not None:
        registration = _EVAL_SUITE_REGISTRY.get(name)
        if registration is None:
            raise KeyError(name)
        if not registration.matches(target_info):
            raise ValueError(f"suite '{name}' does not support target kind '{target_info.get('target_kind')}'")
        mode = "explicit"
    else:
        candidates = [registration for registration in _EVAL_SUITE_REGISTRY.values() if registration.matches(target_info)]
        if not candidates:
            raise KeyError(f"no evaluation suite matches target {target_info.get('target_path')}")
        registration = _sorted_eval_suite_registrations(candidates)[0]
        mode = "auto"

    suite = registration.factory(target_info).with_cases([
        _build_eval_suite_case(target_info),
    ])
    return EvalSuiteSelection(
        suite_id=suite.suite_id,
        suite=suite,
        target=target_info,
        mode=mode,
    )


def resolve_eval_suite(name: str | None, target: str | Path) -> EvalSuiteDef:
    selection = resolve_eval_suite_selection(name, target)
    return selection.suite


register_eval_suite(
    "app-evaluator",
    lambda target: get_builtin_eval_suite("app-evaluator"),
    matcher=lambda target: target.get("target_kind") in {"file", "directory", "image"},
    priority=10,
)
