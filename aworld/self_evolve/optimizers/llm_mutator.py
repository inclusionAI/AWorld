from __future__ import annotations

import hashlib
import inspect
import json
import re
from typing import Any, Callable, Mapping

from aworld.self_evolve.feedback import normalize_feedback_summary
from aworld.self_evolve.optimizers.base import OptimizerRequest, OptimizerResult
from aworld.self_evolve.types import CandidateVariant, OptimizerLineage


MutateTextCallable = Callable[[str], Any]


class TraceReflectiveLLMMutator:
    optimizer_name = "trace-reflective-llm-mutator"
    optimizer_version = "0"

    def __init__(self, *, mutate_text: MutateTextCallable) -> None:
        self.mutate_text = mutate_text

    async def propose(self, request: OptimizerRequest) -> OptimizerResult:
        candidates: list[CandidateVariant] = []
        lineage: list[OptimizerLineage] = []
        filtered_noop_count = 0
        filtered_high_baseline_regression_count = 0
        filtered_duplicate_count = 0
        seen_content_fingerprints: set[str] = set()
        require_targeted_delta = _request_has_high_baseline_regression(request)

        for index in range(request.max_candidates):
            prompt = _build_mutation_prompt(request, candidate_index=index)
            output = self.mutate_text(prompt)
            if inspect.isawaitable(output):
                output = await output
            content, rationale = _parse_mutator_output(output)
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
            lineage.append(
                OptimizerLineage(
                    candidate_id=candidate_id,
                    optimizer_name=self.optimizer_name,
                    optimizer_version=self.optimizer_version,
                    trainable_case_ids=tuple(case.case_id for case in request.trainable_cases),
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
            },
        )


def _build_mutation_prompt(request: OptimizerRequest, *, candidate_index: int) -> str:
    population_strategy = _population_strategy(candidate_index)
    payload = {
        "candidate_index": candidate_index,
        "population_strategy": population_strategy,
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
        "treat compacted/truncated outputs as unusable evidence, keep an evidence ledger, "
        "and require a claim-by-claim non-compacted evidence check before final answers. "
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
        + json.dumps(payload, ensure_ascii=False, sort_keys=True)
    )


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


def _parse_mutator_output(output: Any) -> tuple[str, str]:
    if isinstance(output, Mapping):
        content = output.get("content")
        rationale = output.get("rationale", "")
    else:
        content = output
        rationale = ""
    if not isinstance(content, str) or not content:
        raise ValueError("mutator output must include non-empty content")
    if not isinstance(rationale, str):
        rationale = ""
    return content, rationale


def _candidate_id(request: OptimizerRequest, content: str, *, index: int) -> str:
    digest = hashlib.sha256(
        f"{request.target.target_type}:{request.target.target_id}:{index}:{content}".encode("utf-8")
    ).hexdigest()[:12]
    return f"llm-mutator-{digest}"


def _content_fingerprint(content: str) -> str:
    normalized = "\n".join(line.rstrip() for line in content.strip().splitlines())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


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
) -> bool:
    text = content.lower()
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
        return False

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
    has_broad_guidance = any(term in text for term in broad_terms)
    return has_broad_guidance or growth_ratio > 1.4


def _metric_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    return float(value) if isinstance(value, (int, float)) else None


def _string_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item).strip() for item in value if str(item).strip()}
