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
from aworld.self_evolve.lessons import aggregate_lesson_records
from aworld.self_evolve.optimizers.base import OptimizerRequest
from aworld.self_evolve.repair_conformance import (
    compile_repair_conformance_contract,
    merge_repair_conformance_constraint_context,
)
from aworld.self_evolve.recovery_trace import (
    validate_public_constraint_recovery_trace,
    validate_public_recovery_trace,
)
from aworld.self_evolve.sanitization import (
    public_diagnostic_projection,
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
MAX_CONTEXT_TRACE_CHARS = 12_000
MAX_TRACE_STEPS_PER_PACK = 8
MAX_TRACE_TOOL_CALLS_PER_STEP = 2
MAX_REPAIR_PROMPT_SOURCE_CHARS = 40_000
MAX_PROMPT_FEEDBACK_CHARS = 16_000


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

    def repair_focus_for_candidate(
        self,
        *,
        candidate_index: int,
    ) -> Mapping[str, object] | None:
        if isinstance(candidate_index, bool) or candidate_index < 0:
            raise ValueError("candidate_index must be non-negative")
        _, repair_focus, _ = _focused_validation_feedback(
            self.validation_feedback,
            candidate_index=candidate_index,
        )
        return repair_focus

    def to_prompt_payload(self, *, candidate_index: int) -> dict[str, object]:
        if isinstance(candidate_index, bool) or candidate_index < 0:
            raise ValueError("candidate_index must be non-negative")
        strategies = self.population_strategies or ("minimal_behavior_delta",)
        strategy = strategies[candidate_index % len(strategies)]
        feedback, repair_focus, repair_support = _focused_validation_feedback(
            self.validation_feedback,
            candidate_index=candidate_index,
        )
        focused_repair = repair_focus is not None
        repair_conformance = (
            compile_repair_conformance_contract(repair_focus)
            if (
                repair_focus is not None
                and not _repair_feedback_reached_judged_task_output(repair_focus)
            )
            else None
        )
        focused_capability_contracts = bool(
            focused_repair
            and repair_conformance is not None
            and (
                repair_conformance.schema_field_constraints
                or any(
                    "capability" in code or "compile" in code
                    for code in repair_conformance.failure_codes
                )
            )
        )
        prompt_repair_focus = (
            _bounded_repair_focus_for_prompt(
                repair_focus,
                required_branch_paths=(
                    repair_conformance.required_branch_paths
                    if repair_conformance is not None
                    else ()
                ),
            )
            if repair_focus is not None
            else None
        )
        prompt_feedback = (
            tuple(_without_repair_candidate_package(item) for item in feedback)
            if focused_repair
            else feedback
        )
        prompt_feedback = _budget_prompt_feedback(prompt_feedback)
        payload: dict[str, object] = {
            "schema_version": self.schema_version,
            "candidate_index": candidate_index,
            "population_strategy": strategy,
            "target": dict(self.target),
            "current_content": "" if focused_repair else self.current_content,
            "target_package_inventory": (
                [] if focused_repair else list(self.target_package_inventory)
            ),
            # Once a concrete candidate package and a machine-readable failure
            # are available, they are the authoritative repair context. Repeating
            # the original trajectory and lessons makes the source delta harder to
            # attend to and can duplicate the same package in the prompt.
            "trainable_cases": [] if focused_repair else list(self.trainable_cases),
            "trace_evidence": [] if focused_repair else list(self.trace_evidence),
            "validation_feedback": list(prompt_feedback),
            "lesson_records": [] if focused_repair else list(self.lesson_records),
            "observed_failures": list(self.observed_failures),
            "required_behaviors": list(self.required_behaviors),
            "preserved_behaviors": list(self.preserved_behaviors),
            "capability_requirements": (
                [] if focused_repair else list(self.capability_requirements)
            ),
            "capability_contracts": (
                list(self.capability_contracts)
                if not focused_repair or focused_capability_contracts
                else []
            ),
            "acceptance_constraints": list(self.acceptance_constraints),
            "expected_output": dict(self.expected_output),
        }
        if prompt_repair_focus is not None:
            payload["repair_context_mode"] = "focused_candidate_delta"
            payload["repair_focus"] = prompt_repair_focus
            payload["repair_prompt_budget"] = {
                "source_chars": MAX_REPAIR_PROMPT_SOURCE_CHARS,
                "omitted_current_content_chars": len(self.current_content),
                "omitted_target_package_inventory_items": len(
                    self.target_package_inventory
                ),
                "omitted_capability_requirements": len(
                    self.capability_requirements
                ),
                "omitted_capability_contracts": (
                    0
                    if focused_capability_contracts
                    else len(self.capability_contracts)
                ),
            }
            if repair_conformance is not None:
                payload["repair_conformance"] = (
                    repair_conformance.to_public_dict()
                )
        if repair_support is not None:
            payload["repair_support"] = _repair_support_prompt_summary(
                repair_support
            )
        return payload


def _without_repair_candidate_package(
    feedback: Mapping[str, object],
) -> Mapping[str, object]:
    """Keep diagnostics in the prompt while source lives only in repair_focus."""

    return _public_repair_value({
        key: value
        for key, value in feedback.items()
        if key != "repair_candidate_package"
    })


_PRIVATE_REPAIR_VALUE_KEYS = frozenset(
    {
        "expected_preview",
        "response_preview",
        "expected_response",
        "response_contains",
        "fixture_bytes",
        "fixture_content",
    }
)


def _public_repair_value(value: object) -> object:
    """Project diagnostic context without payload-bearing assertion values."""

    if isinstance(value, Mapping):
        projected: dict[str, object] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            if key in _PRIVATE_REPAIR_VALUE_KEYS and isinstance(item, (str, bytes)):
                encoded = item.encode("utf-8") if isinstance(item, str) else item
                projected[f"{key}_fingerprint"] = (
                    "sha256:" + hashlib.sha256(encoded).hexdigest()
                )
                projected[f"{key}_shape"] = {
                    "kind": "text" if isinstance(item, str) else "bytes",
                    "size_bucket": max(1, len(encoded)).bit_length(),
                }
                continue
            projected[key] = _public_repair_value(item)
        return projected
    if isinstance(value, (list, tuple)):
        return [_public_repair_value(item) for item in value]
    return value


def _budget_prompt_feedback(
    feedback: Sequence[Mapping[str, object]],
) -> tuple[Mapping[str, object], ...]:
    if _serialized_size(feedback) <= MAX_PROMPT_FEEDBACK_CHARS:
        return tuple(feedback)

    selected: list[Mapping[str, object]] = []
    omitted = 0
    for item in feedback:
        compact = _compact_prompt_feedback_item(item)
        if _serialized_size((*selected, compact)) > MAX_PROMPT_FEEDBACK_CHARS:
            omitted += 1
            continue
        selected.append(compact)
    if omitted:
        marker = {
            "feedback_items_omitted": omitted,
            "reason": "prompt_feedback_char_budget",
        }
        if _serialized_size((*selected, marker)) <= MAX_PROMPT_FEEDBACK_CHARS:
            selected.append(marker)
    return tuple(selected)


def _compact_prompt_feedback_item(
    item: Mapping[str, object],
) -> Mapping[str, object]:
    compact: dict[str, object] = {
        "variant_id": sanitize_text(item.get("variant_id"), max_chars=160),
        "dataset_split": sanitize_text(
            item.get("dataset_split"),
            max_chars=80,
        ),
        "feedback_compacted": True,
    }
    for key, limit in (("failed_gates", 8), ("required_behaviors", 4)):
        raw = item.get(key)
        if isinstance(raw, list):
            compact[key] = [
                sanitize_text(value, max_chars=160)
                for value in raw[:limit]
                if str(value).strip()
            ]
    metrics = item.get("metrics")
    if isinstance(metrics, Mapping):
        compact["metrics"] = sanitize_metric_value(metrics, max_chars=120)
    diagnostics = item.get("candidate_validation_diagnostics")
    if isinstance(diagnostics, list):
        compact["candidate_validation_diagnostics"] = [
            {
                key: sanitize_metric_value(diagnostic.get(key), max_chars=160)
                for key in ("code", "stage", "reason", "field_path")
                if diagnostic.get(key) is not None
            }
            for diagnostic in diagnostics[:4]
            if isinstance(diagnostic, Mapping)
        ]
    recovery_trace = validate_public_recovery_trace(item.get("recovery_trace"))
    if recovery_trace is not None:
        compact["recovery_trace"] = recovery_trace
    constraint_recovery_trace = validate_public_constraint_recovery_trace(
        item.get("constraint_recovery_trace")
    )
    if constraint_recovery_trace is not None:
        compact["constraint_recovery_trace"] = constraint_recovery_trace
    repair_plan = item.get("repair_plan")
    if isinstance(repair_plan, Mapping):
        compact["repair_plan"] = {
            key: [
                sanitize_text(value, max_chars=160)
                for value in raw[:4]
                if str(value).strip()
            ]
            for key in ("issues", "actions", "acceptance_criteria")
            if isinstance((raw := repair_plan.get(key)), list)
        }
    return compact


def _bounded_repair_focus_for_prompt(
    repair_focus: Mapping[str, object],
    *,
    required_branch_paths: Sequence[str],
) -> Mapping[str, object]:
    """Keep complete high-value source files and inventory omitted overlay files."""

    package = repair_focus.get("repair_candidate_package")
    if not isinstance(package, Mapping):
        return repair_focus
    raw_files = package.get("files")
    if not isinstance(raw_files, list):
        return repair_focus
    required = frozenset(required_branch_paths)
    ranked_files = sorted(
        (
            (index, item)
            for index, item in enumerate(raw_files)
            if isinstance(item, Mapping)
        ),
        key=lambda value: (
            0
            if str(value[1].get("path") or "") in required
            else (
                1
                if str(value[1].get("path") or "").startswith("replay/")
                else 2
            ),
            -len(
                value[1].get("content")
                if isinstance(value[1].get("content"), str)
                else ""
            ),
            value[0],
        ),
    )
    included_paths: set[str] = set()
    remaining_chars = MAX_REPAIR_PROMPT_SOURCE_CHARS
    for _, item in ranked_files:
        path = str(item.get("path") or "")
        content = item.get("content")
        if (
            not path
            or not isinstance(content, str)
            or len(content) > remaining_chars
        ):
            continue
        included_paths.add(path)
        remaining_chars -= len(content)

    prompt_files: list[dict[str, object]] = []
    for raw_file in raw_files:
        if not isinstance(raw_file, Mapping):
            continue
        item = dict(raw_file)
        path = str(item.get("path") or "")
        content = item.get("content")
        if isinstance(content, str) and path not in included_paths:
            item.pop("content", None)
            item["content_omitted"] = True
            item["content_chars"] = len(content)
            item["content_sha256"] = hashlib.sha256(
                content.encode("utf-8")
            ).hexdigest()
        prompt_files.append(item)
    prompt_package = dict(package)
    prompt_package["files"] = prompt_files
    prompt_focus = _public_repair_value(repair_focus)
    assert isinstance(prompt_focus, dict)
    prompt_focus["repair_candidate_package"] = prompt_package
    return prompt_focus


def _repair_support_prompt_summary(
    repair_support: Mapping[str, object],
) -> Mapping[str, object]:
    """Expose complementary diagnostics without duplicating rejected source."""

    summary = dict(_compact_prompt_feedback_item(repair_support))
    package = repair_support.get("repair_candidate_package")
    if isinstance(package, Mapping):
        summary["repair_candidate_id"] = sanitize_text(
            package.get("candidate_id"),
            max_chars=160,
        )
        summary["repair_candidate_source_omitted"] = True
    return summary


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
    if (
        candidate_index == 0
        or len(ranked_repairs) == 1
        or _repair_feedback_reached_task_plane(primary[1])
    ):
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
        (
            item
            for _, item in ranked_repairs
            if item is not focus
            and _repair_support_is_complementary(focus, item)
        ),
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


def _repair_support_is_complementary(
    focus: Mapping[str, object],
    support: Mapping[str, object],
) -> bool:
    """Do not transplant source that failed the same machine-checked repair.

    A support package is useful only when it contributes behavior from a
    different verified frontier. Reusing another package rejected by the same
    conformance code amplifies a known-bad branch and needlessly expands the
    model context.
    """

    if _repair_feedback_reached_judged_task_output(focus):
        # The focused package has already completed authoritative replay and
        # exposed task output to a judge. Transplanting source from a lower
        # replay/conformance frontier can only regress that verified runtime.
        return False
    focus_codes = _specific_repair_conformance_failure_codes(focus)
    if focus_codes:
        # A machine-checked conformance failure has an exact focused source and
        # executable repair recipe. No rejected sibling is verified to satisfy
        # that recipe, so transplanting one can only reintroduce an unproven
        # branch. Let the focused repair advance past conformance first.
        return False
    return True


def _specific_repair_conformance_failure_codes(
    feedback: Mapping[str, object],
) -> frozenset[str]:
    diagnostics = feedback.get("candidate_validation_diagnostics", ())
    pending: list[object] = [diagnostics]
    codes: set[str] = set()
    visited = 0
    while pending and visited < 256:
        current = pending.pop()
        visited += 1
        if isinstance(current, Mapping):
            stage = str(current.get("stage", "")).strip().casefold()
            code = current.get("code")
            if (
                "repair_conformance" in stage
                and isinstance(code, str)
                and code
                and code != "failed_gate"
            ):
                if code in {
                    "exact_repair_probe_not_recorded",
                    "late_fixture_probe_not_recorded",
                    "late_fixture_probe_outside_recorded_payload",
                }:
                    codes.add("fixture_probe_derivation")
                else:
                    codes.add(code)
            pending.extend(current.values())
        elif isinstance(current, (list, tuple)):
            pending.extend(current)
    return frozenset(codes)


def _repair_feedback_reached_task_plane(
    feedback: Mapping[str, object],
) -> bool:
    if _repair_feedback_reached_judged_task_output(feedback):
        return True
    diagnostic_text = json.dumps(
        feedback.get("candidate_validation_diagnostics", ()),
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    ).lower()
    return (
        "finalize_after_successful_endpoint_interaction" in diagnostic_text
        or "implement_observed_endpoint_interactions" in diagnostic_text
        or "failed to deserialize" in diagnostic_text
        or "missing field" in diagnostic_text
        or '"requires_fixture_derived_probe": true' in diagnostic_text
    )


def _repair_feedback_reached_judged_task_output(
    feedback: Mapping[str, object],
) -> bool:
    """Return whether feedback was produced after a judge saw task output.

    Judge-stage feedback is a deeper verified frontier than replay or protocol
    diagnostics: the candidate has already completed the data plane and exposed
    an answer for groundedness/completeness review.  Keep the check structural so
    it applies to any target or dataset rather than matching task text.
    """

    metrics = feedback.get("metrics")
    evidence = feedback.get("evidence")
    failed_gates = feedback.get("failed_gates")
    if not isinstance(metrics, Mapping):
        metrics = {}
    if not isinstance(evidence, Mapping):
        evidence = {}
    gate_names = (
        {str(value) for value in failed_gates}
        if isinstance(failed_gates, (list, tuple))
        else set()
    )
    has_judge_metrics = any(
        key in metrics
        for key in (
            "score",
            "A1_groundedness",
            "A2_completeness",
            "veto_triggered",
        )
    ) or any(
        key in evidence
        for key in (
            "evidence_incomplete",
            "evidence_compacted",
            "evidence_block_count",
            "veto_triggered",
        )
    )
    if not has_judge_metrics:
        return False
    return (
        str(feedback.get("dataset_split") or "")
        in {"validation", "held_out", "single_case_replay"}
        or bool(
            gate_names
            & {
                "evidence_quality",
                "held_out_verification",
                "required_verification",
                "score_improvement",
            }
        )
    )


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
    recovery_trace = validate_public_recovery_trace(
        feedback.get("recovery_trace")
    )
    recovery_frontier = 0
    if recovery_trace is not None:
        recovered_count = recovery_trace.get("recovered_member_count")
        candidate_success_rate = recovery_trace.get("candidate_success_rate")
        if isinstance(recovered_count, (int, float)) and not isinstance(
            recovered_count, bool
        ):
            recovery_frontier += min(64, max(0, int(recovered_count))) * 10
        if isinstance(candidate_success_rate, (int, float)) and not isinstance(
            candidate_success_rate, bool
        ):
            recovery_frontier += min(
                99,
                max(0, int(float(candidate_success_rate) * 99)),
            )
    constraint_recovery_trace = validate_public_constraint_recovery_trace(
        feedback.get("constraint_recovery_trace")
    )
    if constraint_recovery_trace is not None:
        recovered_constraints = constraint_recovery_trace.get(
            "recovered_constraint_count"
        )
        regressed_constraints = constraint_recovery_trace.get(
            "regressed_constraint_count"
        )
        repeated_constraints = constraint_recovery_trace.get(
            "repeated_violation_count"
        )
        if isinstance(recovered_constraints, int):
            recovery_frontier += min(64, recovered_constraints) * 10
        if isinstance(regressed_constraints, int):
            recovery_frontier -= min(64, regressed_constraints) * 10
        if isinstance(repeated_constraints, int):
            # Repeated failures contain the strongest strategy-switch signal
            # and should remain visible to the focused repair prompt.
            recovery_frontier += min(16, repeated_constraints)
    frontier_progress = recovery_frontier + interaction_progress
    diagnostic_text = json.dumps(
        feedback.get("candidate_validation_diagnostics", ()),
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    ).lower()
    inherited_task_plane_frontier = (
        '"requires_fixture_derived_probe": true' in diagnostic_text
        or (
            '"repair_conformance"' in diagnostic_text
            and "implement_observed_endpoint_interactions" in diagnostic_text
        )
    )
    if _repair_feedback_reached_judged_task_output(feedback):
        # Once a judge has scored a completed task answer, repair the answer and
        # its evidence contract before revisiting older replay/conformance
        # branches. Held-out feedback wins ties with validation from the same
        # evaluated package, while recency still breaks ties within each split.
        split_offset = 5_000 if feedback.get("dataset_split") == "held_out" else 0
        return 200_000 + split_offset + frontier_progress
    if (
        isinstance(metrics, Mapping)
        and metrics.get("authoritative_replay_failure") is True
    ):
        return 150_000 + frontier_progress
    if "finalize_after_successful_endpoint_interaction" in diagnostic_text:
        return 145_000 + frontier_progress
    if "repair_conformance" in diagnostic_text:
        # Keep repair search on the deepest verified frontier. A candidate that
        # inherited a real task-plane failure remains ahead of transport-only
        # branches. Within the same frontier, a candidate that reached the
        # isolated dynamic probe contains more working behavior than one rejected
        # by source or declaration checks, even when the shallower failure is
        # newer in accumulated feedback.
        frontier_offset = 40_000 if inherited_task_plane_frontier else 0
        if "repair_probe_execution_failed" in diagnostic_text:
            return 89_000 + frontier_offset + frontier_progress
        if (
            "late_fixture_probe_not_recorded" in diagnostic_text
            or "late_fixture_probe_outside_recorded_payload" in diagnostic_text
            or "exact_repair_probe_not_recorded" in diagnostic_text
        ):
            return 88_000 + frontier_offset + frontier_progress
        if (
            "late_fixture_probe_missing" in diagnostic_text
            or "exact_repair_probe_missing" in diagnostic_text
        ):
            return 87_000 + frontier_offset + frontier_progress
        if (
            "repair_capability_compile_failed" in diagnostic_text
            or "repair_capability_missing" in diagnostic_text
        ):
            return 86_000 + frontier_offset + frontier_progress
        if "repair_branch_unchanged" in diagnostic_text:
            return 81_000 + frontier_offset + frontier_progress
        return 84_000 + frontier_offset + frontier_progress
    if "preserve_protocol_routing_continuity" in diagnostic_text:
        return 38_000 + frontier_progress
    if "implement_async_endpoint_completion" in diagnostic_text:
        return 35_000 + frontier_progress
    if "diagnose_protocol_handler_abort" in diagnostic_text:
        return 32_000 + frontier_progress
    if (
        "implement_observed_endpoint_interactions" in diagnostic_text
        or "failed to deserialize" in diagnostic_text
        or "missing field" in diagnostic_text
    ):
        # A real task rollout that reached the data plane is a deeper verified
        # frontier than any transport-only conformance preflight. Do not let a
        # newer shallow branch discard its late-operation contract.
        return 140_000 + frontier_progress
    if "verify_declared_protocol_probe_branch" in diagnostic_text:
        return 34_000 + frontier_progress
    if (
        "invalid_replay_capability_compile" in diagnostic_text
        or "capability_compile" in diagnostic_text
    ):
        return 10_000 + frontier_progress
    return frontier_progress


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
    feedback = _merge_typed_repair_constraints_across_feedback(feedback)
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


def _merge_typed_repair_constraints_across_feedback(
    feedback: tuple[Mapping[str, object], ...],
) -> tuple[Mapping[str, object], ...]:
    """Make every focused lineage honor the cumulative typed repair contract."""

    merged = merge_repair_conformance_constraint_context(None, *feedback)
    if merged is None:
        return feedback
    constraint_context = {
        key: value
        for key, value in merged.items()
        if key in {"fixture_probe_constraints", "schema_field_constraints"}
    }
    if not constraint_context:
        return feedback
    result: list[Mapping[str, object]] = []
    for item in feedback:
        if not isinstance(item.get("repair_candidate_package"), Mapping):
            result.append(item)
            continue
        updated = dict(item)
        raw_diagnostics = updated.get("candidate_validation_diagnostics")
        diagnostics = (
            [dict(value) for value in raw_diagnostics if isinstance(value, Mapping)]
            if isinstance(raw_diagnostics, (list, tuple))
            else []
        )
        diagnostics.append(
            {
                "code": "inherited_typed_repair_constraints",
                "stage": "typed_causal_feedback",
                **constraint_context,
            }
        )
        updated["candidate_validation_diagnostics"] = diagnostics
        result.append(updated)
    return tuple(result)


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
        256,
        min(8_000, MAX_CONTEXT_TRACE_CHARS // len(selected_packs)),
    )
    payloads: list[Mapping[str, object]] = []
    for pack in selected_packs:
        identifier_chars = min(
            80,
            max(24, per_pack_budget // 6),
        )
        representative_steps = _representative_trace_steps(
            pack.steps,
            limit=MAX_TRACE_STEPS_PER_PACK,
        )
        payload: dict[str, object] = {
            "pack_id": sanitize_text(
                pack.pack_id,
                max_chars=identifier_chars,
            ),
            "task_id": sanitize_text(
                pack.task_id,
                max_chars=identifier_chars,
            ),
            "evidence_step_ids": [
                sanitize_text(
                    step.evidence_id,
                    max_chars=identifier_chars,
                )
                for step in (
                    representative_steps
                    if len(representative_steps) <= 2
                    else (representative_steps[0], representative_steps[-1])
                )
            ],
            "final_action_excerpt": sanitize_text(
                pack.final_action_excerpt,
                max_chars=min(
                    800,
                    max(32, per_pack_budget // 5),
                ),
            ),
        }
        step_budget = max(
            0,
            per_pack_budget - _serialized_size(payload) - 16,
        )
        payload["steps"] = _bounded_trace_step_payloads(
            representative_steps,
            max_chars=step_budget,
        )
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
    unique_lessons = aggregate_lesson_records(
        tuple(lesson for lesson in lessons if hasattr(lesson, "lesson_id"))
    )
    ranked_lessons = sorted(unique_lessons, key=_lesson_value_key)
    return tuple(
        {
            "lesson_id": sanitize_text(lesson.lesson_id, max_chars=160),
            "lesson_type": sanitize_text(lesson.lesson_type, max_chars=80),
            "title": sanitize_text(lesson.title, max_chars=240),
            "summary": sanitize_text(lesson.summary, max_chars=1_000),
            "confidence": sanitize_text(lesson.confidence, max_chars=40),
            "evidence_refs": (
                []
                if lesson.lesson_type == "causal_failure_memory"
                else [
                    sanitize_text(item, max_chars=160)
                    for item in lesson.evidence_refs[:8]
                ]
            ),
            "metrics": public_diagnostic_projection(
                lesson.metrics,
                max_chars=240,
            ),
            "occurrence_count": max(1, int(lesson.occurrence_count)),
            "distinct_source_count": max(0, int(lesson.distinct_source_count)),
            "source_run_ids": [
                sanitize_text(item, max_chars=160)
                for item in lesson.source_run_ids[:8]
            ],
            "source_task_ids": [
                sanitize_text(item, max_chars=160)
                for item in lesson.source_task_ids[:8]
            ],
            "source_candidate_ids": [
                sanitize_text(item, max_chars=160)
                for item in lesson.source_candidate_ids[:8]
            ],
            "affected_case_ids": [
                sanitize_text(item, max_chars=160)
                for item in lesson.affected_case_ids[:16]
            ],
            "affected_case_count": max(0, int(lesson.affected_case_count)),
        }
        for lesson in ranked_lessons[:MAX_CONTEXT_LESSONS]
    )


def _lesson_value_key(lesson: object) -> tuple[int, int, int, str]:
    metrics = lesson.metrics if isinstance(lesson.metrics, Mapping) else {}
    causal_repair = (
        lesson.lesson_type == "causal_failure_memory"
        and metrics.get("repairable") is True
    )
    required_runtime = lesson.lesson_type == "required_runtime_behavior"
    recurrent = int(getattr(lesson, "distinct_source_count", 0)) > 1
    success_memory = "success" in str(lesson.lesson_type)
    if causal_repair:
        priority = 0
    elif required_runtime and lesson.confidence == "high":
        priority = 1
    elif recurrent:
        priority = 2
    elif required_runtime:
        priority = 3
    elif success_memory:
        priority = 4
    else:
        priority = 3
    confidence = {"high": 2, "medium": 1, "low": 0}.get(lesson.confidence, 0)
    # Occurrence count is intentionally absent: copies emitted by one
    # iteration cannot outrank evidence recurring across distinct sources.
    return (
        priority,
        -int(getattr(lesson, "distinct_source_count", 0)),
        -confidence,
        str(lesson.lesson_id),
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
    return tuple(sorted(values)[:MAX_CONTEXT_REQUIREMENTS])


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
