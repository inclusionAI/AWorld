from __future__ import annotations

import hashlib
import inspect
import json
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

        for index in range(request.max_candidates):
            prompt = _build_mutation_prompt(request, candidate_index=index)
            output = self.mutate_text(prompt)
            if inspect.isawaitable(output):
                output = await output
            content, rationale = _parse_mutator_output(output)
            if content == request.current_content:
                filtered_noop_count += 1
                continue

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
            diagnostics={"filtered_noop_candidates": filtered_noop_count},
        )


def _build_mutation_prompt(request: OptimizerRequest, *, candidate_index: int) -> str:
    payload = {
        "candidate_index": candidate_index,
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
        "If feedback shows a high-scoring baseline with candidate_score <= baseline_score, "
        "do not propose broad extra guidance. Preserve the baseline strengths and encode a "
        "small explicit behavior delta: what execution behavior should change, what behavior "
        "should stay unchanged, and what acceptance check proves the candidate beats the "
        "baseline on score, compliance, efficiency, or robustness.\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True)
    )


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
