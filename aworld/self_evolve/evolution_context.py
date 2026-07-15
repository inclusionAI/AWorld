from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from aworld.self_evolve.candidate_protocol import CANDIDATE_OUTPUT_CONTRACT
from aworld.self_evolve.capability_contracts import (
    discover_applicable_capability_contracts,
)
from aworld.self_evolve.feedback import normalize_feedback_summary
from aworld.self_evolve.optimizers.base import OptimizerRequest
from aworld.self_evolve.sanitization import (
    sanitize_metric_value,
    sanitize_path_ref,
    sanitize_text,
)


EVOLUTION_CONTEXT_SCHEMA_VERSION = "aworld.self_evolve.evolution_context.v1"
MAX_CONTEXT_CASES = 32
MAX_CONTEXT_TRACE_PACKS = 32
MAX_CONTEXT_FEEDBACK_ITEMS = 24
MAX_CONTEXT_LESSONS = 32
MAX_CONTEXT_REQUIREMENTS = 64
MAX_CURRENT_CONTENT_CHARS = 400_000


@dataclass(frozen=True)
class EvolutionContext:
    schema_version: str
    target: Mapping[str, object]
    current_content: str
    target_package_inventory: tuple[str, ...]
    trainable_cases: tuple[Mapping[str, object], ...]
    trace_evidence: tuple[Mapping[str, object], ...]
    validation_feedback: tuple[Mapping[str, object], ...]
    lesson_records: tuple[Mapping[str, object], ...]
    observed_failures: tuple[str, ...]
    required_behaviors: tuple[str, ...]
    preserved_behaviors: tuple[str, ...]
    capability_requirements: tuple[Mapping[str, object], ...]
    capability_contracts: tuple[Mapping[str, object], ...]
    population_strategies: tuple[str, ...]
    acceptance_constraints: tuple[str, ...]
    expected_output: Mapping[str, object]

    def to_prompt_payload(self, *, candidate_index: int) -> dict[str, object]:
        if isinstance(candidate_index, bool) or candidate_index < 0:
            raise ValueError("candidate_index must be non-negative")
        strategies = self.population_strategies or ("minimal_behavior_delta",)
        strategy = strategies[candidate_index % len(strategies)]
        return {
            "schema_version": self.schema_version,
            "candidate_index": candidate_index,
            "population_strategy": strategy,
            "target": dict(self.target),
            "current_content": self.current_content,
            "target_package_inventory": list(self.target_package_inventory),
            "trainable_cases": list(self.trainable_cases),
            "trace_evidence": list(self.trace_evidence),
            "validation_feedback": list(self.validation_feedback),
            "lesson_records": list(self.lesson_records),
            "observed_failures": list(self.observed_failures),
            "required_behaviors": list(self.required_behaviors),
            "preserved_behaviors": list(self.preserved_behaviors),
            "capability_requirements": list(self.capability_requirements),
            "capability_contracts": list(self.capability_contracts),
            "acceptance_constraints": list(self.acceptance_constraints),
            "expected_output": dict(self.expected_output),
        }


def compile_evolution_context(request: OptimizerRequest) -> EvolutionContext:
    feedback = _deduplicate_feedback(
        (*request.validation_feedback, *request.prior_feedback)
    )
    contracts = discover_applicable_capability_contracts(
        request.replay_requirements
    )
    observed_failures = _feedback_string_values(feedback, "failed_gates")
    required_behaviors = _feedback_string_values(feedback, "required_behaviors")
    preserved_behaviors = _preserved_behaviors(request.lesson_records)
    return EvolutionContext(
        schema_version=EVOLUTION_CONTEXT_SCHEMA_VERSION,
        target={
            "target_type": sanitize_text(request.target.target_type, max_chars=80),
            "target_id": sanitize_text(request.target.target_id, max_chars=160),
            "path": sanitize_path_ref(request.target.path),
            "fingerprint": sanitize_text(
                request.target_fingerprint,
                max_chars=160,
            ),
        },
        current_content=sanitize_text(
            request.current_content,
            max_chars=MAX_CURRENT_CONTENT_CHARS,
        ),
        target_package_inventory=tuple(
            sanitize_path_ref(item)
            for item in request.target_package_inventory[:256]
        ),
        trainable_cases=_trainable_case_payloads(request.trainable_cases),
        trace_evidence=_trace_evidence_payloads(request.trace_packs),
        validation_feedback=feedback,
        lesson_records=_lesson_payloads(request.lesson_records),
        observed_failures=observed_failures,
        required_behaviors=required_behaviors,
        preserved_behaviors=preserved_behaviors,
        capability_requirements=_requirement_payloads(
            request.replay_requirements
        ),
        capability_contracts=contracts,
        population_strategies=_population_strategies(
            has_feedback=bool(feedback),
            has_capability_contracts=bool(contracts),
        ),
        acceptance_constraints=(
            "return_one_canonical_candidate_package",
            "preserve_unrelated_target_behavior",
            "satisfy_registered_capability_contracts",
            "pass_isolated_baseline_candidate_comparison",
            "do_not_use_reserved_evaluation_evidence_for_generation",
        ),
        expected_output=CANDIDATE_OUTPUT_CONTRACT,
    )


def _deduplicate_feedback(
    feedback_items: Sequence[object],
) -> tuple[Mapping[str, object], ...]:
    values: list[Mapping[str, object]] = []
    seen: set[str] = set()
    for feedback in feedback_items:
        normalized = sanitize_metric_value(normalize_feedback_summary(feedback))
        fingerprint = hashlib.sha256(
            json.dumps(
                normalized,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        values.append(normalized)
        if len(values) >= MAX_CONTEXT_FEEDBACK_ITEMS:
            break
    return tuple(values)


def _trainable_case_payloads(
    cases: Sequence[object],
) -> tuple[Mapping[str, object], ...]:
    payloads: list[Mapping[str, object]] = []
    for case in cases[:MAX_CONTEXT_CASES]:
        payloads.append(
            {
                "case_id": sanitize_text(case.case_id, max_chars=160),
                "input": sanitize_metric_value(case.input, max_chars=8_000),
                "expected_output": sanitize_metric_value(
                    case.expected_output,
                    max_chars=4_000,
                ),
                "metadata": sanitize_metric_value(case.metadata, max_chars=240),
            }
        )
    return tuple(payloads)


def _trace_evidence_payloads(
    trace_packs: Sequence[object],
) -> tuple[Mapping[str, object], ...]:
    return tuple(
        {
            "pack_id": sanitize_text(pack.pack_id, max_chars=160),
            "task_id": sanitize_text(pack.task_id, max_chars=160),
            "evidence_step_ids": [
                sanitize_text(step.evidence_id, max_chars=160)
                for step in pack.steps[:16]
            ],
            "final_action_excerpt": sanitize_text(
                pack.final_action_excerpt,
                max_chars=2_000,
            ),
        }
        for pack in trace_packs[:MAX_CONTEXT_TRACE_PACKS]
    )


def _lesson_payloads(
    lessons: Sequence[object],
) -> tuple[Mapping[str, object], ...]:
    return tuple(
        {
            "lesson_id": sanitize_text(lesson.lesson_id, max_chars=160),
            "lesson_type": sanitize_text(lesson.lesson_type, max_chars=80),
            "title": sanitize_text(lesson.title, max_chars=240),
            "summary": sanitize_text(lesson.summary, max_chars=1_000),
            "confidence": sanitize_text(lesson.confidence, max_chars=40),
            "evidence_refs": [
                sanitize_text(item, max_chars=160)
                for item in lesson.evidence_refs[:8]
            ],
            "metrics": sanitize_metric_value(lesson.metrics, max_chars=240),
        }
        for lesson in lessons[:MAX_CONTEXT_LESSONS]
    )


def _requirement_payloads(
    requirements: Sequence[object],
) -> tuple[Mapping[str, object], ...]:
    return tuple(
        {
            "requirement_id": sanitize_text(item.requirement_id, max_chars=160),
            "kind": sanitize_text(item.kind, max_chars=80),
            "identifier": sanitize_text(item.identifier, max_chars=240),
            "case_ids": [
                sanitize_text(case_id, max_chars=160)
                for case_id in item.case_ids[:16]
            ],
            "evidence_refs": [
                sanitize_text(reference, max_chars=160)
                for reference in item.evidence_refs[:16]
            ],
            "status": sanitize_text(item.status, max_chars=80),
            "detail": sanitize_text(item.detail, max_chars=500),
        }
        for item in requirements[:MAX_CONTEXT_REQUIREMENTS]
    )


def _feedback_string_values(
    feedback: Sequence[Mapping[str, object]],
    key: str,
) -> tuple[str, ...]:
    values: set[str] = set()
    for item in feedback:
        raw = item.get(key)
        if not isinstance(raw, list):
            continue
        values.update(
            sanitize_text(value, max_chars=160)
            for value in raw
            if str(value).strip()
        )
    return tuple(sorted(values))


def _preserved_behaviors(lessons: Sequence[object]) -> tuple[str, ...]:
    values: set[str] = set()
    for lesson in lessons:
        if lesson.lesson_type == "success_memory":
            values.add(sanitize_text(lesson.summary, max_chars=240))
        raw = lesson.metrics.get("preserved_behaviors")
        if isinstance(raw, (list, tuple)):
            values.update(
                sanitize_text(value, max_chars=160)
                for value in raw[:8]
                if str(value).strip()
            )
    return tuple(sorted(value for value in values if value))


def _population_strategies(
    *,
    has_feedback: bool,
    has_capability_contracts: bool,
) -> tuple[str, ...]:
    strategies = ["minimal_behavior_delta"]
    if has_capability_contracts:
        strategies.append("missing_capability_completion")
    if has_feedback:
        strategies.append("quality_regression_repair")
    strategies.append("efficiency_and_robustness")
    return tuple(strategies)
