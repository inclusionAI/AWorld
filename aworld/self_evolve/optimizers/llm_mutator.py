from __future__ import annotations

import hashlib
import inspect
import json
import re
from typing import Any, Callable, Mapping

from aworld.self_evolve.feedback import normalize_feedback_summary
from aworld.self_evolve.optimizers.base import OptimizerRequest, OptimizerResult
from aworld.self_evolve.patch_intent import apply_skill_patch_intent
from aworld.self_evolve.types import CandidateVariant, OptimizerLineage


MutateTextCallable = Callable[[str], Any]


class TraceReflectiveLLMMutator:
    optimizer_name = "trace-reflective-llm-mutator"
    optimizer_version = "0"

    def __init__(self, *, mutate_text: MutateTextCallable) -> None:
        self.mutate_text = mutate_text

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

        for index in range(request.max_candidates):
            prompt = _build_mutation_prompt(request, candidate_index=index)
            strategy_record = _candidate_strategy_record(request, candidate_index=index)
            output = self.mutate_text(prompt)
            if inspect.isawaitable(output):
                output = await output
            try:
                content, rationale, materialization = _materialize_mutator_output(
                    output,
                    request=request,
                )
            except ValueError:
                filtered_invalid_patch_count += 1
                continue
            if content == request.current_content:
                filtered_noop_count += 1
                continue
            content_fingerprint = _content_fingerprint(content)
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

            candidate_id = _candidate_id(request, content, index=index)
            candidate = CandidateVariant(
                candidate_id=candidate_id,
                target=request.target,
                content=content,
                rationale=rationale,
                target_fingerprint=request.target_fingerprint,
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

        return OptimizerResult(
            candidates=tuple(candidates),
            lineage=tuple(lineage),
            diagnostics={
                "filtered_noop_candidates": filtered_noop_count,
                "filtered_high_baseline_regression_candidates": (
                    filtered_high_baseline_regression_count
                ),
                "filtered_duplicate_candidates": filtered_duplicate_count,
                "filtered_invalid_patch_candidates": filtered_invalid_patch_count,
                "candidate_strategies": candidate_strategy_records,
            },
        )


def _build_mutation_prompt(request: OptimizerRequest, *, candidate_index: int) -> str:
    population_strategy = _population_strategy(candidate_index)
    candidate_strategy = _candidate_strategy_record(request, candidate_index=candidate_index)
    payload = {
        "candidate_index": candidate_index,
        "population_strategy": population_strategy,
        "candidate_strategy": candidate_strategy,
        "target": {
            "target_type": request.target.target_type,
            "target_id": request.target.target_id,
            "path": request.target.path,
            "fingerprint": request.target_fingerprint,
        },
        "current_content": request.current_content,
        "trace_evidence": [
            {
                "pack_id": pack.pack_id,
                "task_id": pack.task_id,
                "evidence_step_ids": [step.evidence_id for step in pack.steps],
                "final_action_excerpt": pack.final_action_excerpt,
            }
            for pack in request.trace_packs
        ],
        "validation_feedback": [
            {"feedback_summary": normalize_feedback_summary(feedback)}
            for feedback in request.validation_feedback
        ],
        "prior_feedback": [
            {"feedback_summary": normalize_feedback_summary(feedback)}
            for feedback in request.prior_feedback
        ],
        "lesson_records": [
            {
                "lesson_id": lesson.lesson_id,
                "lesson_type": lesson.lesson_type,
                "title": lesson.title,
                "summary": lesson.summary,
                "confidence": lesson.confidence,
                "evidence_refs": list(lesson.evidence_refs[:8]),
                "metrics": lesson.metrics,
            }
            for lesson in request.lesson_records
        ],
        "trainable_cases": [
            {
                "case_id": case.case_id,
                "input": case.input,
                "expected_output": case.expected_output,
                "metadata": case.metadata,
            }
            for case in request.trainable_cases
        ],
    }
    return (
        "Propose one concise text-only self-evolve candidate. "
        "Use trace evidence and trainable cases only; do not assume held-out data. "
        "Prefer the smallest useful change: encode a minimal behavior delta that directly "
        "addresses the observed failure, include a preserve list naming behavior that must "
        "stay unchanged, and include an acceptance check that would prove the delta helped. "
        f"Use this candidate population strategy: {population_strategy['name']} - "
        f"{population_strategy['instruction']} "
        "Do not rewrite the whole target when a local addition or replacement is enough. "
        "Use trace-driven reflective optimization: identify why the prior run lost score, "
        "then encode reusable procedural guidance that can improve task quality, tool economy, "
        "latency, and completion reliability. "
        "If validation_feedback or prior_feedback mentions evidence_quality, "
        "evidence_compacted, or evidence_incomplete, the candidate must include general "
        "evidence-preservation guidance. Make it actionable and tool-agnostic: avoid "
        "large raw tool outputs, persist raw evidence to files or artifacts first, emit "
        "only bounded structured summaries with source locations and short excerpts, "
        "treat compacted/truncated raw outputs without artifact-backed extracts as "
        "unusable evidence, keep an evidence ledger, and require a claim-by-claim "
        "artifact-backed or bounded-extract evidence check before final answers. "
        "If feedback mentions invalid manifest entries, veto_triggered, low A1_groundedness, "
        "or required_behaviors such as manifest_schema_compliance, pre_final_veto_check, "
        "support_every_claim_with_artifact_reference, or raise_groundedness_before_breadth, "
        "the candidate must include generic evidence-repair guidance: validate artifact "
        "manifest entries before final answers, preserve artifact reference integrity, "
        "run a pre-final veto check, support every factual claim with an artifact reference "
        "or minimal source span, remove or qualify unsupported claims, and improve "
        "groundedness before expanding answer breadth. "
        "If feedback shows more evidence blocks, higher evidence_incomplete, or longer "
        "latency without score improvement, the candidate must reduce scope instead of "
        "broadening synthesis: prefer fewer verified claims over broad synthesis, do not "
        "expand answer breadth until each claim is verifiable, optimize verifiability per "
        "evidence block, avoid collecting more evidence without a verifiability gain, and "
        "cap evidence acquisition and summarization cost. "
        "If feedback mentions score_improvement, B2_efficiency, or required_behaviors such as "
        "plan_before_tools, prefer_direct_structured_extraction, minimize_failed_attempts, "
        "avoid_repeated_paths, or stop_after_sufficient_evidence, the candidate must include "
        "general efficiency-improvement guidance: plan the shortest viable evidence path before "
        "tool calls, prefer direct structured extraction over broad exploration, minimize failed "
        "attempts, avoid repeated paths after one unsuccessful try, stop after sufficient evidence "
        "is captured, and compare against the baseline on quality, tool economy, latency, and "
        "completion reliability. "
        "If feedback mentions compacted tool arguments, compacted_string_field, invalid tool "
        "arguments, or required_behaviors such as avoid_compacted_tool_arguments, "
        "regenerate_schema_valid_tool_arguments, stop_repeating_invalid_tool_calls, or "
        "switch_to_artifact_read_after_invalid_tool_argument, the candidate must include generic "
        "tool-argument hygiene: never execute replay placeholders as real tool inputs, regenerate "
        "the smallest schema-valid tool arguments from the current task context, read saved "
        "artifacts when the original argument was compacted, and do not repeat the same invalid "
        "tool call after one schema failure. "
        "If feedback shows a high-scoring baseline with candidate_score <= baseline_score, "
        "do not propose broad extra guidance. Preserve the baseline strengths and encode a "
        "small explicit behavior delta: what execution behavior should change, what behavior "
        "should stay unchanged, and what acceptance check proves the candidate beats the "
        "baseline on score, compliance, efficiency, or robustness.\n"
        "If feedback mentions high_baseline_without_efficiency_gain, "
        "replace_broad_validation_with_efficiency_delta, "
        "candidate_uses_no_more_steps_than_baseline, or "
        "use_efficiency_delta_for_high_baseline, the candidate must improve by doing less "
        "at the same quality: preserve the baseline claim set, answer structure, and source "
        "references; do not add new verification loops, new evidence paths, or new external "
        "claims; reduce or cap tool/evidence steps and pass only if groundedness and "
        "completeness stay no worse than baseline.\n"
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
    population_strategy = _population_strategy(candidate_index)
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


def _population_strategy(candidate_index: int) -> dict[str, str]:
    strategies = (
        {
            "name": "conservative_preserve_then_delta",
            "instruction": (
                "keep high-scoring baseline behavior explicit, then add only one targeted "
                "behavior delta with a no-worse-than-baseline acceptance check"
            ),
        },
        {
            "name": "evidence_integrity_delta",
            "instruction": (
                "focus on evidence fidelity and verification behavior while preserving "
                "answer breadth, groundedness, and completeness"
            ),
        },
        {
            "name": "score_dimension_repair_delta",
            "instruction": (
                "repair the lowest regressed scoring dimensions from feedback, especially "
                "A1/A2/B2 deltas, without adding broad unrelated guidance"
            ),
        },
    )
    return strategies[candidate_index % len(strategies)]


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
) -> tuple[str, str, str]:
    if isinstance(output, Mapping):
        content = output.get("content")
        patch_intent = output.get("patch_intent")
        rationale = output.get("rationale", "")
    else:
        content = output
        patch_intent = None
        rationale = ""
    if isinstance(patch_intent, Mapping):
        content = apply_skill_patch_intent(request.current_content, patch_intent)
        materialization = "patch_intent"
    else:
        materialization = "full_content"
    if not isinstance(content, str) or not content:
        raise ValueError("mutator output must include non-empty content")
    if not isinstance(rationale, str):
        rationale = ""
    return content, rationale, materialization


def _candidate_id(request: OptimizerRequest, content: str, *, index: int) -> str:
    digest = hashlib.sha256(
        f"{request.target.target_type}:{request.target.target_id}:{index}:{content}".encode("utf-8")
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
