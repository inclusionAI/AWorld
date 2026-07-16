from __future__ import annotations

import hashlib
import inspect
import json
import re
import time
from typing import Any, Awaitable, Callable, Mapping, Sequence

from aworld.self_evolve.candidate_package import (
    candidate_package_fingerprint,
    validate_candidate_files,
)
from aworld.self_evolve.candidate_generation import (
    CandidateGenerationInfrastructureError,
)
from aworld.self_evolve.concurrency import (
    CandidatePopulationResult,
    SelfEvolveConcurrencyPolicy,
)
from aworld.self_evolve.evolution_context import compile_evolution_context
from aworld.self_evolve.feedback import normalize_feedback_summary
from aworld.self_evolve.optimizers.base import OptimizerRequest, OptimizerResult
from aworld.self_evolve.patch_intent import apply_skill_patch_intent
from aworld.self_evolve.types import CandidateFileDelta, CandidateVariant, OptimizerLineage


MutateTextCallable = Callable[[str], Any]
CandidatePopulationCallable = Callable[
    [Sequence[str], int],
    Awaitable[CandidatePopulationResult],
]


class TraceReflectiveLLMMutator:
    optimizer_name = "trace-reflective-llm-mutator"
    optimizer_version = "0"

    def __init__(
        self,
        *,
        mutate_text: MutateTextCallable,
        population_callable: CandidatePopulationCallable | None = None,
        concurrency_policy: SelfEvolveConcurrencyPolicy | None = None,
    ) -> None:
        self.mutate_text = mutate_text
        self.population_callable = population_callable
        self.concurrency_policy = concurrency_policy or SelfEvolveConcurrencyPolicy()

    async def propose(self, request: OptimizerRequest) -> OptimizerResult:
        if not _has_lesson_backed_delta_signal(request):
            return OptimizerResult(
                candidates=(),
                lineage=(),
                diagnostics={
                    "filtered_noop_candidates": 0,
                    "filtered_high_baseline_regression_candidates": 0,
                    "filtered_duplicate_candidates": 0,
                    "candidate_strategies": (),
                    "no_op_recommended": True,
                    "no_op_reason": "no_lesson_backed_safe_delta",
                },
            )
        candidates: list[CandidateVariant] = []
        lineage: list[OptimizerLineage] = []
        filtered_noop_count = 0
        filtered_high_baseline_regression_count = 0
        filtered_duplicate_count = 0
        filtered_invalid_patch_count = 0
        seen_content_fingerprints: set[str] = set()
        require_targeted_delta = _request_has_high_baseline_regression(request)
        candidate_strategy_records: list[dict[str, Any]] = []
        candidate_generation_failure: dict[str, str] | None = None
        candidate_protocol_invalid_count = 0
        candidate_outputs: list[tuple[int, Any]] = []
        population_diagnostics: dict[str, Any]
        population_started_at = time.monotonic()

        if self.population_callable is not None:
            prompts = tuple(
                _build_mutation_prompt(request, candidate_index=index)
                for index in range(request.max_candidates)
            )
            population = await self.population_callable(
                prompts,
                self.concurrency_policy.effective_limit(
                    "candidate_generation",
                    item_count=len(prompts),
                ),
            )
            population_diagnostics = dict(population.diagnostics)
            for slot in population.slots:
                if slot.status == "succeeded":
                    candidate_outputs.append((slot.index, slot.output))
                elif slot.status == "failed" and candidate_generation_failure is None:
                    candidate_generation_failure = dict(slot.failure or {})
                elif slot.status == "protocol_invalid":
                    candidate_protocol_invalid_count += 1
        else:
            statuses = ["discarded"] * request.max_candidates
            failure_cutoff: int | None = None
            for index in range(request.max_candidates):
                prompt = _build_mutation_prompt(request, candidate_index=index)
                try:
                    output = self.mutate_text(prompt)
                    if inspect.isawaitable(output):
                        output = await output
                except CandidateGenerationInfrastructureError as exc:
                    candidate_generation_failure = exc.to_diagnostic()
                    statuses[index] = "failed"
                    failure_cutoff = index
                    break
                statuses[index] = "succeeded"
                candidate_outputs.append((index, output))
            population_diagnostics = {
                "mode": "custom_serial",
                "item_count": request.max_candidates,
                "configured_concurrency": 1,
                "effective_concurrency": min(1, request.max_candidates),
                "max_observed_concurrency": min(
                    1,
                    len(candidate_outputs) + (1 if failure_cutoff is not None else 0),
                ),
                "failure_cutoff_index": failure_cutoff,
                "statuses": statuses,
                "repair_count": 0,
                "resource_serialized_count": 0,
                "queue_wait_seconds": 0.0,
                "execution_seconds": time.monotonic() - population_started_at,
                "elapsed_seconds": time.monotonic() - population_started_at,
            }

        for index, output in candidate_outputs:
            strategy_record = _candidate_strategy_record(request, candidate_index=index)
            try:
                content, rationale, materialization, files = _materialize_mutator_output(
                    output,
                    request=request,
                )
            except ValueError:
                filtered_invalid_patch_count += 1
                continue
            if content == request.current_content and not files:
                filtered_noop_count += 1
                continue
            candidate = CandidateVariant(
                candidate_id="pending",
                target=request.target,
                content=content,
                rationale=rationale,
                target_fingerprint=request.target_fingerprint,
                files=files,
            )
            content_fingerprint = candidate_package_fingerprint(candidate)
            if content_fingerprint in seen_content_fingerprints:
                filtered_duplicate_count += 1
                continue
            if require_targeted_delta and _is_weak_high_baseline_regression_candidate(
                content,
                current_content=request.current_content,
                request=request,
            ):
                filtered_high_baseline_regression_count += 1
                continue
            seen_content_fingerprints.add(content_fingerprint)

            candidate_id = _candidate_id(
                request,
                content,
                files=files,
                index=index,
            )
            candidate = CandidateVariant(
                candidate_id=candidate_id,
                target=request.target,
                content=content,
                rationale=rationale,
                target_fingerprint=request.target_fingerprint,
                files=files,
            )
            candidates.append(candidate)
            candidate_strategy_records.append(
                {
                    "candidate_id": candidate_id,
                    "materialization": materialization,
                    **strategy_record,
                }
            )
            lineage.append(
                OptimizerLineage(
                    candidate_id=candidate_id,
                    optimizer_name=self.optimizer_name,
                    optimizer_version=self.optimizer_version,
                    trainable_case_ids=tuple(case.case_id for case in request.trainable_cases),
                    content_fingerprint=content_fingerprint,
                    semantic_fingerprint=_semantic_fingerprint(content),
                    lesson_set_fingerprint=_lesson_set_fingerprint(request),
                    addressed_lesson_ids=_addressed_lesson_ids(request),
                    rationale=rationale,
                )
            )

        diagnostics: dict[str, object] = {
            "filtered_noop_candidates": filtered_noop_count,
            "filtered_high_baseline_regression_candidates": (
                filtered_high_baseline_regression_count
            ),
            "filtered_duplicate_candidates": filtered_duplicate_count,
            "filtered_invalid_patch_candidates": filtered_invalid_patch_count,
            "candidate_strategies": candidate_strategy_records,
            "candidate_population_execution": population_diagnostics,
            "candidate_protocol_invalid_count": candidate_protocol_invalid_count,
        }
        if candidate_generation_failure is not None:
            diagnostics["candidate_generation_failure"] = candidate_generation_failure

        return OptimizerResult(
            candidates=tuple(candidates),
            lineage=tuple(lineage),
            diagnostics=diagnostics,
        )


def _build_mutation_prompt(request: OptimizerRequest, *, candidate_index: int) -> str:
    context = request.evolution_context or compile_evolution_context(request)
    payload = context.to_prompt_payload(candidate_index=candidate_index)
    return (
        "Generate one candidate package from this bounded EvolutionContext. "
        "Follow its population_strategy, required_behaviors, preserved_behaviors, "
        "capability_contracts, and acceptance_constraints. Prefer the smallest reusable "
        "behavior delta and keep domain implementations inside candidate-owned files. "
        "Use trace step summaries to infer reusable behavior, never to hard-code dataset "
        "task IDs, case IDs, original endpoints, or environment-specific paths. "
        "Replay harness files must accompany, not substitute for, a reusable task-execution "
        "improvement in the primary target content. Reconstruct fixture-derived task data "
        "for observed interactions; do not stop at control-plane handshakes, placeholder "
        "tokens, or empty protocol schemas. "
        "When validation_feedback contains repair_candidate_package, repair that bounded "
        "candidate source directly and preserve its already-correct package behavior instead "
        "of rebuilding the capability from scratch. "
        "When repair_focus is present, use it as the base candidate source package for this "
        "population member: satisfy its observed failure and acceptance direction before "
        "addressing other historical feedback. When repair_support is present, it is one "
        "bounded complementary source package: transplant only the compatible branches "
        "that already satisfy a regression constraint, without replacing the focused base "
        "or reintroducing the support candidate's failed gate. Treat every other current validation "
        "diagnostic as a regression constraint on that repair: combine compatible fixes "
        "into the focused source, preserve behaviors that already advanced farther in the "
        "interaction, and do not mistake transport liveness for asynchronous completion. "
        "For a bounded change to a large current target, use patch_intent so all unrelated "
        "content remains byte-for-byte preserved; do not reconstruct a shortened full copy. "
        "Return the value of expected_output as exactly one JSON object; do not wrap it, "
        "and use exactly one of content or patch_intent.\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True)
    )


def _has_lesson_backed_delta_signal(request: OptimizerRequest) -> bool:
    if request.lesson_records or request.validation_feedback or request.prior_feedback:
        return True
    if request.trainable_cases:
        return True
    return any(pack.steps for pack in request.trace_packs)


def _candidate_strategy_record(
    request: OptimizerRequest,
    *,
    candidate_index: int,
) -> dict[str, Any]:
    population_strategy = _population_strategy(request, candidate_index)
    addressed_lessons = _addressed_lesson_ids(request)
    preserved_success_behaviors = _preserved_success_behaviors(request)
    risk_notes = _risk_notes(request)
    strategy_hints = _strategy_hints(request)
    return {
        "strategy_id": f"{population_strategy['name']}:{candidate_index}",
        "candidate_family": population_strategy["name"],
        "intended_behavior_delta": population_strategy["instruction"],
        "addressed_lessons": list(addressed_lessons),
        "harness_diagnostics_considered": list(_harness_diagnostic_ids(request)),
        "preserved_success_behaviors": preserved_success_behaviors,
        "risk_notes": risk_notes,
        "strategy_hints": strategy_hints,
        "replay_priority": _replay_priority(
            addressed_lessons=addressed_lessons,
            preserved_success_behaviors=preserved_success_behaviors,
            risk_notes=risk_notes,
        ),
    }


def _population_strategy(
    request: OptimizerRequest,
    candidate_index: int,
) -> dict[str, str]:
    context = request.evolution_context or compile_evolution_context(request)
    names = context.population_strategies or ("minimal_behavior_delta",)
    name = names[candidate_index % len(names)]
    instructions = {
        "minimal_behavior_delta": (
            "preserve existing strengths and add the smallest behavior change that "
            "satisfies the typed acceptance constraints"
        ),
        "missing_capability_completion": (
            "publish candidate-owned files that satisfy every applicable capability "
            "authoring contract"
        ),
        "quality_regression_repair": (
            "repair the typed failed gates and required behaviors without unrelated scope"
        ),
        "efficiency_and_robustness": (
            "improve reliability and resource economy while preserving required quality"
        ),
    }
    return {
        "name": name,
        "instruction": instructions[name],
    }


def _preserved_success_behaviors(request: OptimizerRequest) -> list[str]:
    behaviors: list[str] = []
    for lesson in request.lesson_records:
        if lesson.lesson_type not in {"lean_solution_path", "trajectory_success_memory", "success_memory"}:
            continue
        if lesson.summary:
            behaviors.append(lesson.summary)
        tool_names = lesson.metrics.get("tool_names") if isinstance(lesson.metrics, Mapping) else None
        if isinstance(tool_names, list) and tool_names:
            behaviors.append("preserve tool path: " + ", ".join(str(item) for item in tool_names[:4]))
    return list(dict.fromkeys(behaviors))[:6]


def _risk_notes(request: OptimizerRequest) -> list[str]:
    notes: list[str] = []
    for lesson in request.lesson_records:
        if lesson.lesson_type in {"failure_memory", "trajectory_failure_memory", "harness_diagnostic"}:
            notes.append(lesson.summary)
        failed_gates = lesson.metrics.get("failed_gates") if isinstance(lesson.metrics, Mapping) else None
        if isinstance(failed_gates, list):
            notes.extend(str(item) for item in failed_gates[:4])
    return list(dict.fromkeys(note for note in notes if note))[:6]


def _harness_diagnostic_ids(request: OptimizerRequest) -> tuple[str, ...]:
    return tuple(
        lesson.lesson_id
        for lesson in request.lesson_records
        if lesson.lesson_type == "harness_diagnostic" and lesson.lesson_id
    )


def _strategy_hints(request: OptimizerRequest) -> list[str]:
    hints: list[str] = []
    for lesson in request.lesson_records:
        if lesson.lesson_type != "harness_diagnostic":
            continue
        metrics = lesson.metrics if isinstance(lesson.metrics, Mapping) else {}
        diagnostic_kind = str(metrics.get("diagnostic_kind") or "").strip()
        if diagnostic_kind == "artifact_lifecycle":
            hints.append("improve artifact lifecycle handling without copying diagnostic labels into runtime instructions")
        elif diagnostic_kind == "workflow":
            hints.append("stabilize replay workflow before adding runtime behavior")
        elif diagnostic_kind == "evaluation":
            hints.append("make evaluator-facing evidence easier to verify without changing task-specific behavior")
        elif diagnostic_kind:
            hints.append(f"consider {diagnostic_kind} as a framework diagnostic, not runtime wording")
    return list(dict.fromkeys(hints))[:6]


def _replay_priority(
    *,
    addressed_lessons: tuple[str, ...],
    preserved_success_behaviors: list[str],
    risk_notes: list[str],
) -> str:
    if addressed_lessons and preserved_success_behaviors:
        return "high"
    if addressed_lessons or risk_notes:
        return "medium"
    return "low"


def _materialize_mutator_output(
    output: Any,
    *,
    request: OptimizerRequest,
) -> tuple[str, str, str, tuple[CandidateFileDelta, ...]]:
    if isinstance(output, Mapping):
        content = output.get("content")
        patch_intent = output.get("patch_intent")
        rationale = output.get("rationale", "")
        raw_files = output.get("files", ())
    else:
        content = output
        patch_intent = None
        rationale = ""
        raw_files = ()
    if isinstance(patch_intent, Mapping):
        content = apply_skill_patch_intent(request.current_content, patch_intent)
        materialization = "patch_intent"
    else:
        materialization = "full_content"
    if not isinstance(content, str) or not content:
        raise ValueError("mutator output must include non-empty content")
    if not isinstance(rationale, str):
        rationale = ""
    if not isinstance(raw_files, (list, tuple)):
        raise ValueError("mutator files must be a list")
    files = validate_candidate_files(
        CandidateFileDelta(
            path=str(item.get("path") or ""),
            operation=str(item.get("operation") or "upsert"),
            content=(
                item.get("content")
                if isinstance(item.get("content"), str)
                else None
            ),
            executable=bool(item.get("executable", False)),
        )
        for item in raw_files
        if isinstance(item, Mapping)
    )
    return content, rationale, materialization, files


def _candidate_id(
    request: OptimizerRequest,
    content: str,
    *,
    files: tuple[CandidateFileDelta, ...] = (),
    index: int,
) -> str:
    file_payload = [
        (item.path, item.operation, item.content, item.executable)
        for item in validate_candidate_files(files)
    ]
    digest = hashlib.sha256(
        json.dumps(
            {
                "target_type": request.target.target_type,
                "target_id": request.target.target_id,
                "index": index,
                "content": content,
                "files": file_payload,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:12]
    return f"llm-mutator-{digest}"


def _content_fingerprint(content: str) -> str:
    normalized = "\n".join(line.rstrip() for line in content.strip().splitlines())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _semantic_fingerprint(content: str) -> str:
    semantic_lines = [
        re.sub(r"\s+", " ", line.strip().lower())
        for line in content.splitlines()
        if line.strip() and line.strip() != "---"
    ]
    return hashlib.sha256("\n".join(semantic_lines).encode("utf-8")).hexdigest()


def _lesson_set_fingerprint(request: OptimizerRequest) -> str | None:
    lesson_ids = _addressed_lesson_ids(request)
    if not lesson_ids:
        return None
    return hashlib.sha256("\n".join(lesson_ids).encode("utf-8")).hexdigest()


def _addressed_lesson_ids(request: OptimizerRequest) -> tuple[str, ...]:
    lesson_ids: list[str] = [
        lesson.lesson_id
        for lesson in request.lesson_records
        if lesson.lesson_id
    ]
    for feedback in (*request.validation_feedback, *request.prior_feedback):
        summary = normalize_feedback_summary(feedback)
        metrics = summary.get("metrics")
        if not isinstance(metrics, Mapping):
            continue
        lesson_id = metrics.get("lesson_id")
        if isinstance(lesson_id, str) and lesson_id:
            lesson_ids.append(lesson_id)
    return tuple(dict.fromkeys(lesson_ids))


def _request_has_high_baseline_regression(request: OptimizerRequest) -> bool:
    for feedback in (*request.validation_feedback, *request.prior_feedback):
        summary = normalize_feedback_summary(feedback)
        metrics = summary.get("metrics")
        metrics = metrics if isinstance(metrics, Mapping) else {}
        baseline_score = _metric_float(metrics.get("baseline_score"))
        candidate_score = _metric_float(metrics.get("candidate_score"))
        score_delta = _metric_float(metrics.get("score_delta"))
        if baseline_score is None or baseline_score < 85.0:
            continue
        if score_delta is not None and score_delta <= 0:
            return True
        if candidate_score is not None and candidate_score <= baseline_score:
            return True

        required_behaviors = _string_set(summary.get("required_behaviors"))
        if required_behaviors & {
            "differentiate_from_high_scoring_baseline",
            "preserve_baseline_strengths",
            "define_behavior_delta_before_tools",
            "prefer_targeted_changes_over_broad_rewrites",
        }:
            return True

        repair_plan = summary.get("repair_plan")
        if isinstance(repair_plan, Mapping) and _string_set(repair_plan.get("actions")) & {
            "preserve_high_scoring_baseline_strengths",
            "define_candidate_behavior_delta",
            "prefer_targeted_change_over_broad_rewrite",
        }:
            return True
    return False


def _is_weak_high_baseline_regression_candidate(
    content: str,
    *,
    current_content: str,
    request: OptimizerRequest | None = None,
) -> bool:
    retained_delta = _retained_baseline_delta(
        content,
        current_content=current_content,
    )
    text = (retained_delta if retained_delta is not None else content).lower()
    has_preserve = bool(
        re.search(
            r"\b(preserve|keep|unchanged|baseline strengths|baseline behavior|保留|保持|不变)\b",
            text,
        )
    )
    has_behavior_delta = bool(
        re.search(
            r"\b(behavior delta|delta|change only|only add|only change|small targeted|"
            r"targeted change|行为增量|执行行为|只改变|仅新增)\b",
            text,
        )
    )
    has_acceptance_check = bool(
        re.search(
            r"\b(acceptance check|acceptance criteria|must beat|must pass|verify|"
            r"verification|no worse than|验收|准入|验证|检查)\b",
            text,
        )
    )
    if has_preserve and has_behavior_delta and has_acceptance_check:
        return not (
            retained_delta is not None
            or _preserves_lean_solution_path(text, request)
        )

    growth_ratio = len(content) / max(len(current_content), 1)
    broad_terms = (
        "comprehensive",
        "broader",
        "more evidence",
        "collect more",
        "expand",
        "always",
        "all claims",
        "全面",
        "更多证据",
        "扩大",
    )
    has_broad_guidance = _has_unnegated_guidance(text, broad_terms)
    if retained_delta is not None:
        max_delta_chars = min(4_000, max(1_200, int(len(current_content) * 0.4)))
        return has_broad_guidance or len(retained_delta) > max_delta_chars
    return (
        has_broad_guidance
        or growth_ratio > 1.4
        or not _preserves_lean_solution_path(text, request)
    )


def _retained_baseline_delta(
    content: str,
    *,
    current_content: str,
) -> str | None:
    baseline = current_content.rstrip()
    if not baseline or not content.startswith(baseline):
        return None
    return content[len(baseline) :].strip()


def _has_unnegated_guidance(text: str, terms: tuple[str, ...]) -> bool:
    for term in terms:
        for match in re.finditer(re.escape(term), text):
            clause_start = max(
                text.rfind(delimiter, 0, match.start())
                for delimiter in ("\n", ".", ";", "!", "?", "。", "；", "！", "？")
            )
            prefix = text[clause_start + 1 : match.start()]
            if re.search(
                r"(?:\b(?:do not|don't|never|avoid|without|not)\b|"
                r"不要|不得|避免|禁止|无需|不再)",
                prefix,
            ):
                continue
            return True
    return False


def _preserves_lean_solution_path(
    lowered_content: str,
    request: OptimizerRequest | None,
) -> bool:
    if request is None:
        return True
    lean_lessons = [
        lesson
        for lesson in request.lesson_records
        if lesson.lesson_type == "lean_solution_path"
    ]
    if not lean_lessons:
        return True
    generic_lean_markers = (
        "lean path",
        "lean successful path",
        "shortest path",
        "single artifact",
        "one artifact",
        "preserve successful",
        "preserve lean",
    )
    if any(marker in lowered_content for marker in generic_lean_markers):
        return True
    tool_names = {
        str(tool_name).strip().lower()
        for lesson in lean_lessons
        if isinstance(lesson.metrics, Mapping)
        for tool_name in (
            lesson.metrics.get("tool_names")
            if isinstance(lesson.metrics.get("tool_names"), list)
            else []
        )
        if str(tool_name).strip()
    }
    return bool(tool_names and any(tool_name in lowered_content for tool_name in tool_names))


def _metric_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    return float(value) if isinstance(value, (int, float)) else None


def _string_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item).strip() for item in value if str(item).strip()}
