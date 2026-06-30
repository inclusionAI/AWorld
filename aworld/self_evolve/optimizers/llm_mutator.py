from __future__ import annotations

import hashlib
import inspect
import json
from typing import Any, Callable, Mapping

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
            {
                "variant_id": feedback.variant_id,
                "metrics": feedback.metrics,
                "dataset_split": feedback.dataset_split,
            }
            for feedback in request.validation_feedback
        ],
        "prior_feedback": [
            {
                "variant_id": feedback.variant_id,
                "metrics": feedback.metrics,
                "dataset_split": feedback.dataset_split,
            }
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
        "Use trace evidence and trainable cases only; do not assume held-out data.\n"
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
