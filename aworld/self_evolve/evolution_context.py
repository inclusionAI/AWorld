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
MAX_CONTEXT_TRACE_CHARS = 64_000
MAX_TRACE_STEPS_PER_PACK = 8
MAX_TRACE_TOOL_CALLS_PER_STEP = 2


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
        feedback, repair_focus, repair_support = _focused_validation_feedback(
            self.validation_feedback,
            candidate_index=candidate_index,
        )
        payload: dict[str, object] = {
            "schema_version": self.schema_version,
            "candidate_index": candidate_index,
            "population_strategy": strategy,
            "target": dict(self.target),
            "current_content": self.current_content,
            "target_package_inventory": list(self.target_package_inventory),
            "trainable_cases": list(self.trainable_cases),
            "trace_evidence": list(self.trace_evidence),
            "validation_feedback": list(feedback),
            "lesson_records": list(self.lesson_records),
            "observed_failures": list(self.observed_failures),
            "required_behaviors": list(self.required_behaviors),
            "preserved_behaviors": list(self.preserved_behaviors),
            "capability_requirements": list(self.capability_requirements),
            "capability_contracts": list(self.capability_contracts),
            "acceptance_constraints": list(self.acceptance_constraints),
            "expected_output": dict(self.expected_output),
        }
        if repair_focus is not None:
            payload["repair_focus"] = repair_focus
        if repair_support is not None:
            payload["repair_support"] = repair_support
        return payload


def _focused_validation_feedback(
    feedback: Sequence[Mapping[str, object]],
    *,
    candidate_index: int,
) -> tuple[
    tuple[Mapping[str, object], ...],
    Mapping[str, object] | None,
    Mapping[str, object] | None,
]:
    repair_items = [
        (index, item)
        for index, item in enumerate(feedback)
        if isinstance(item.get("repair_candidate_package"), Mapping)
    ]
    if not repair_items:
        return tuple(feedback), None, None

    # The first population member exploits the deepest observed failure. The
    # remaining members preserve recency diversity so a newly-progressing
    # lineage is not crowded out by several older failures from the same
    # priority tier.
    ranked_repairs = sorted(
        repair_items,
        key=lambda value: (_repair_feedback_priority(value[1]), value[0]),
        reverse=True,
    )
    primary = ranked_repairs[0]
    if candidate_index == 0 or len(ranked_repairs) == 1:
        focus = primary[1]
    else:
        recent_alternatives = [
            item
            for item in reversed(repair_items)
            if item[0] != primary[0]
        ]
        focus = recent_alternatives[
            (candidate_index - 1) % len(recent_alternatives)
        ][1]
    support = next(
        (item for _, item in ranked_repairs if item is not focus),
        None,
    )
    contextual_feedback: list[Mapping[str, object]] = [focus]
    for item in feedback:
        if item is focus:
            continue
        contextual_feedback.append(
            {
                key: value
                for key, value in item.items()
                if key != "repair_candidate_package"
            }
        )
    return tuple(contextual_feedback), focus, support


def _repair_feedback_priority(feedback: Mapping[str, object]) -> int:
    metrics = feedback.get("metrics")
    interaction_progress = 0
    if isinstance(metrics, Mapping):
        raw_progress = metrics.get("interaction_progress")
        if (
            not isinstance(raw_progress, bool)
            and isinstance(raw_progress, (int, float))
        ):
            interaction_progress = min(999, max(0, int(raw_progress)))
    if (
        isinstance(metrics, Mapping)
        and metrics.get("authoritative_replay_failure") is True
    ):
        return 50_000 + interaction_progress
    diagnostic_text = json.dumps(
        feedback.get("candidate_validation_diagnostics", ()),
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    ).lower()
    if "preserve_protocol_routing_continuity" in diagnostic_text:
        return 38_000 + interaction_progress
    if "implement_async_endpoint_completion" in diagnostic_text:
        return 35_000 + interaction_progress
    if "diagnose_protocol_handler_abort" in diagnostic_text:
        return 32_000 + interaction_progress
    if (
        "implement_observed_endpoint_interactions" in diagnostic_text
        or "failed to deserialize" in diagnostic_text
        or "missing field" in diagnostic_text
    ):
        return 42_000 + interaction_progress
    if "verify_declared_protocol_probe_branch" in diagnostic_text:
        return 34_000 + interaction_progress
    if (
        "invalid_replay_capability_compile" in diagnostic_text
        or "capability_compile" in diagnostic_text
    ):
        return 10_000 + interaction_progress
    return interaction_progress


def compile_evolution_context(request: OptimizerRequest) -> EvolutionContext:
    current_feedback = _deduplicate_feedback(request.validation_feedback)
    prior_feedback = _deduplicate_feedback(request.prior_feedback)
    if any(
        isinstance(item.get("repair_candidate_package"), Mapping)
        for item in current_feedback
    ):
        prior_feedback = tuple(
            {
                key: value
                for key, value in item.items()
                if key != "repair_candidate_package"
            }
            for item in prior_feedback
        )
    feedback = _deduplicate_feedback_payloads(
        (*current_feedback, *prior_feedback)
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
            has_current_validation_feedback=bool(request.validation_feedback),
            has_capability_contracts=bool(contracts),
        ),
        acceptance_constraints=(
            "return_one_canonical_candidate_package",
            "preserve_unrelated_target_behavior",
            "satisfy_registered_capability_contracts",
            "improve_target_behavior_separately_from_replay_harness_files",
            "pass_isolated_baseline_candidate_comparison",
            "reconstruct_fixture_derived_task_data_plane",
            "do_not_use_reserved_evaluation_evidence_for_generation",
            "do_not_embed_dataset_specific_identifiers",
        ),
        expected_output=CANDIDATE_OUTPUT_CONTRACT,
    )


def _deduplicate_feedback(
    feedback_items: Sequence[object],
) -> tuple[Mapping[str, object], ...]:
    values: list[Mapping[str, object]] = []
    seen: set[str] = set()
    for feedback in feedback_items:
        normalized = _bounded_feedback_summary(
            normalize_feedback_summary(feedback)
        )
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


def _deduplicate_feedback_payloads(
    feedback_items: Sequence[Mapping[str, object]],
) -> tuple[Mapping[str, object], ...]:
    values: list[Mapping[str, object]] = []
    seen: set[str] = set()
    for item in feedback_items:
        fingerprint = hashlib.sha256(
            json.dumps(
                item,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        values.append(item)
        if len(values) >= MAX_CONTEXT_FEEDBACK_ITEMS:
            break
    return tuple(values)


def _bounded_feedback_summary(
    summary: Mapping[str, Any],
) -> Mapping[str, object]:
    normalized = dict(sanitize_metric_value(summary))
    for key, limit in (("failed_gates", 16), ("required_behaviors", 32)):
        raw = summary.get(key)
        if isinstance(raw, list):
            normalized[key] = [
                sanitize_text(item, max_chars=160)
                for item in raw[:limit]
                if str(item).strip()
            ]
    repair_plan = summary.get("repair_plan")
    if isinstance(repair_plan, Mapping):
        normalized_plan = dict(sanitize_metric_value(repair_plan))
        for key in ("issues", "actions", "acceptance_criteria"):
            raw = repair_plan.get(key)
            if isinstance(raw, list):
                normalized_plan[key] = [
                    sanitize_text(item, max_chars=200)
                    for item in raw[:24]
                    if str(item).strip()
                ]
        normalized["repair_plan"] = normalized_plan
    repair_candidate_package = summary.get("repair_candidate_package")
    if isinstance(repair_candidate_package, Mapping):
        # normalize_feedback_summary already applies the bounded, source-aware
        # sanitizer. Running the generic metric sanitizer again would replace
        # executable assignments such as ``token = expression``.
        normalized["repair_candidate_package"] = repair_candidate_package
    return normalized


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
    selected_packs = trace_packs[:MAX_CONTEXT_TRACE_PACKS]
    if not selected_packs:
        return ()
    per_pack_budget = max(
        2_000,
        min(16_000, MAX_CONTEXT_TRACE_CHARS // len(selected_packs)),
    )
    payloads: list[Mapping[str, object]] = []
    for pack in selected_packs:
        representative_steps = _representative_trace_steps(
            pack.steps,
            limit=MAX_TRACE_STEPS_PER_PACK,
        )
        payload = {
            "pack_id": sanitize_text(pack.pack_id, max_chars=160),
            "task_id": sanitize_text(pack.task_id, max_chars=160),
            "evidence_step_ids": [
                sanitize_text(step.evidence_id, max_chars=160)
                for step in representative_steps
            ],
            "final_action_excerpt": sanitize_text(
                pack.final_action_excerpt,
                max_chars=2_000,
            ),
            "steps": _bounded_trace_step_payloads(
                representative_steps,
                max_chars=per_pack_budget,
            ),
        }
        payloads.append(payload)
    return tuple(payloads)


def _representative_trace_steps(
    steps: Sequence[object],
    *,
    limit: int,
) -> tuple[object, ...]:
    """Sample a bounded trajectory across its full temporal span."""

    if limit <= 0 or not steps:
        return ()
    if len(steps) <= limit:
        return tuple(steps)
    if limit == 1:
        return (steps[-1],)
    indexes = tuple(
        round(index * (len(steps) - 1) / (limit - 1))
        for index in range(limit)
    )
    return tuple(steps[index] for index in indexes)


def _bounded_trace_step_payloads(
    steps: Sequence[object],
    *,
    max_chars: int,
) -> list[Mapping[str, object]]:
    payloads = [_trace_step_payload(step) for step in steps]
    if _serialized_size(payloads) <= max_chars:
        return payloads

    selected_indexes: list[int] = []
    boundary_order: list[int] = []
    left, right = 0, len(payloads) - 1
    while left <= right:
        boundary_order.append(left)
        if right != left:
            boundary_order.append(right)
        left += 1
        right -= 1
    for index in boundary_order:
        candidate_indexes = sorted((*selected_indexes, index))
        candidate = [payloads[item] for item in candidate_indexes]
        if _serialized_size(candidate) > max_chars:
            continue
        selected_indexes.append(index)
    return [payloads[index] for index in sorted(selected_indexes)]


def _trace_step_payload(step: object) -> Mapping[str, object]:
    state = step.state if isinstance(step.state, Mapping) else {}
    action = step.action if isinstance(step.action, Mapping) else {}
    payload: dict[str, object] = {
        "evidence_id": sanitize_text(step.evidence_id, max_chars=160),
        "source_index": step.source_index,
    }
    if step.agent_id:
        payload["agent_id"] = sanitize_text(step.agent_id, max_chars=120)
    payload.update(
        {
            "tool_names": [
                sanitize_text(name, max_chars=120)
                for name in step.tool_names[:8]
            ],
            "input_excerpt": _trace_value_excerpt(
                _first_trace_value(
                    state,
                    ("input", "task_input", "query", "messages"),
                ),
                max_chars=400,
            ),
            "action_excerpt": _trace_value_excerpt(
                action.get("content"),
                max_chars=600,
            ),
            "observation_excerpt": _trace_observation_excerpt(
                state,
                step.reward,
            ),
            "tool_call_summaries": _tool_call_summaries(action),
            "reward_summary": _bounded_reward_summary(step.reward),
        }
    )
    return payload


def _trace_observation_excerpt(
    state: Mapping[str, Any],
    reward: Any,
) -> str:
    messages = state.get("messages")
    if isinstance(messages, list):
        for message in reversed(messages):
            if not isinstance(message, Mapping) or message.get("role") != "tool":
                continue
            if message.get("content") is not None:
                return _trace_value_excerpt(message["content"], max_chars=800)
    if isinstance(reward, Mapping):
        tool_outputs = reward.get("tool_outputs")
        if isinstance(tool_outputs, list) and tool_outputs:
            return _trace_value_excerpt(tool_outputs[-1], max_chars=800)
    return ""


def _first_trace_value(
    values: Mapping[str, Any],
    keys: Sequence[str],
) -> Any:
    for key in keys:
        if key in values and values[key] is not None:
            return values[key]
    return None


def _trace_value_excerpt(value: Any, *, max_chars: int) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return sanitize_text(value, max_chars=max_chars)
    bounded = sanitize_metric_value(value, max_chars=min(max_chars, 240))
    try:
        serialized = json.dumps(
            bounded,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    except (TypeError, ValueError):
        serialized = str(bounded)
    return sanitize_text(serialized, max_chars=max_chars)


def _tool_call_summaries(
    action: Mapping[str, Any],
) -> list[Mapping[str, str]]:
    raw_calls = action.get("tool_calls")
    if not isinstance(raw_calls, list):
        return []
    summaries: list[Mapping[str, str]] = []
    for raw_call in raw_calls[:MAX_TRACE_TOOL_CALLS_PER_STEP]:
        if not isinstance(raw_call, Mapping):
            continue
        function = raw_call.get("function")
        if not isinstance(function, Mapping):
            continue
        summaries.append(
            {
                "name": sanitize_text(function.get("name"), max_chars=120),
                "arguments_excerpt": _trace_value_excerpt(
                    function.get("arguments"),
                    max_chars=600,
                ),
            }
        )
    return summaries


def _bounded_reward_summary(value: Any) -> Any:
    bounded = sanitize_metric_value(value, max_chars=160)
    if _serialized_size(bounded) <= 800:
        return bounded
    return {"excerpt": _trace_value_excerpt(bounded, max_chars=760)}


def _serialized_size(value: Any) -> int:
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
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
        if lesson.lesson_type in {
            "lean_solution_path",
            "trajectory_success_memory",
            "success_memory",
        }:
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
    has_current_validation_feedback: bool,
    has_capability_contracts: bool,
) -> tuple[str, ...]:
    strategies = (
        ["quality_regression_repair"]
        if has_current_validation_feedback
        else ["minimal_behavior_delta"]
    )
    if has_capability_contracts:
        strategies.append("missing_capability_completion")
    if has_feedback and not has_current_validation_feedback:
        strategies.append("quality_regression_repair")
    if has_current_validation_feedback:
        strategies.append("minimal_behavior_delta")
    strategies.append("efficiency_and_robustness")
    return tuple(strategies)
