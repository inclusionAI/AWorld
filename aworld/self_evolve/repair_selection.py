"""Candidate-bound, judge-backed checkpoints used only to select repair source."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
import hashlib
import json
import math
import re

from aworld.self_evolve.candidate_package import candidate_package_fingerprint
from aworld.self_evolve.types import (
    CandidateFileDelta,
    CandidateVariant,
    EvaluationSummary,
    GateResult,
    SelfEvolveTargetRef,
)
from aworld.skills.structure_types import skill_structural_edit_intent_from_dict

_SCHEMA = "aworld.self_evolve.repair_selection.v1"
_SPLITS = {"validation", "held_out"}
_CHECKPOINT_GATES = {
    "score_improvement",
    "evidence_quality",
    "held_out_verification",
    "required_verification",
    "judge_only_signal",
    "cost_latency_regression",
    "evaluation_comparability",
    "replay_stability",
}


def _identifier(value: object) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= 160
        and not any(ord(c) < 32 for c in value)
    )


def _fingerprint(value: object) -> bool:
    return (
        isinstance(value, str)
        and re.fullmatch(r"sha256:[0-9a-f]{64}", value) is not None
    )


def selection_candidate_from_payload(value: object) -> CandidateVariant | None:
    if not isinstance(value, Mapping):
        return None
    if not isinstance(value.get("parent_candidate_ids", ()), (list, tuple)):
        return None
    try:
        return CandidateVariant(
            candidate_id=value["candidate_id"],
            target=SelfEvolveTargetRef(**value["target"]),
            content=value["content"],
            rationale=value.get("rationale", ""),
            files=tuple(CandidateFileDelta(**item) for item in value["files"]),
            parent_candidate_ids=tuple(value.get("parent_candidate_ids", ())),
            target_fingerprint=value.get("target_fingerprint"),
            structural_edit_intent=skill_structural_edit_intent_from_dict(
                value.get("structural_edit_intent")
            ),
        )
    except (KeyError, TypeError, ValueError):
        return None


def bounded_repair_selection(value: object, *, candidate_id: object) -> dict | None:
    if (
        not isinstance(value, Mapping)
        or value.get("schema_version") != _SCHEMA
        or value.get("candidate_id") != candidate_id
    ):
        return None
    parents, checkpoints = value.get("parent_candidate_ids"), value.get("checkpoints")
    if (
        not _identifier(candidate_id)
        or not _fingerprint(value.get("package_fingerprint"))
        or not _fingerprint(value.get("source_fingerprint"))
        or not _fingerprint(value.get("target_fingerprint"))
        or not isinstance(parents, (list, tuple))
        or len(parents) > 8
        or any(not _identifier(p) or p == candidate_id for p in parents)
        or len(set(parents)) != len(parents)
        or not isinstance(checkpoints, Mapping)
        or not checkpoints
        or not set(checkpoints).issubset(_SPLITS)
    ):
        return None
    bounded = {}
    for split, checkpoint in checkpoints.items():
        if not isinstance(checkpoint, Mapping):
            return None
        passed = checkpoint.get("passed_gates")
        fields = (
            "evaluation_fingerprint",
            "backend_fingerprint",
            "case_panel_fingerprint",
        )
        if (
            any(not _fingerprint(checkpoint.get(key)) for key in fields)
            or not isinstance(passed, (list, tuple))
            or len(passed) > 16
            or any(
                not isinstance(gate, str) or gate not in _CHECKPOINT_GATES
                for gate in passed
            )
        ):
            return None
        bounded[split] = {
            **{key: checkpoint[key] for key in fields},
            "passed_gates": sorted(set(passed)),
        }
    return {
        "schema_version": _SCHEMA,
        "candidate_id": candidate_id,
        "package_fingerprint": value["package_fingerprint"],
        "source_fingerprint": value["source_fingerprint"],
        "target_fingerprint": value["target_fingerprint"],
        "parent_candidate_ids": list(parents),
        "checkpoints": bounded,
    }


def repair_source_fingerprint(package: object) -> str | None:
    if not isinstance(package, Mapping):
        return None
    source = {
        key: package.get(key)
        for key in ("candidate_id", "content", "files", "structural_edit_intent")
    }
    try:
        return (
            "sha256:"
            + hashlib.sha256(
                json.dumps(
                    source, sort_keys=True, ensure_ascii=False, separators=(",", ":")
                ).encode()
            ).hexdigest()
        )
    except (TypeError, ValueError):
        return None


def with_repair_selection(
    feedback: Sequence[EvaluationSummary],
    *,
    candidate: CandidateVariant,
    summaries: Mapping[str, EvaluationSummary],
    split_gates: Mapping[str, Sequence[GateResult]],
) -> tuple[EvaluationSummary, ...]:
    """Keep explicit passed checkpoints separate from unchanged gate/metric data."""
    try:
        package_fingerprint = candidate_package_fingerprint(candidate)
    except (TypeError, ValueError):
        return tuple(feedback)
    checkpoints = {}
    for split, summary in summaries.items():
        if (
            split not in _SPLITS
            or summary.variant_id != candidate.candidate_id
            or summary.dataset_split != split
        ):
            continue
        metrics = summary.metrics
        identity = metrics.get("evaluation_identity")
        successes, cases = (
            metrics.get("judge_success_count"),
            metrics.get("comparison_case_ids"),
        )
        if (
            metrics.get("evaluation_agent_signal") is not True
            or metrics.get("evaluation_fresh_execution") is not True
            or isinstance(successes, bool)
            or not isinstance(successes, (int, float))
            or not math.isfinite(successes)
            or successes <= 0
            or any(
                isinstance(metrics.get(key), bool) or metrics.get(key) != 0
                for key in ("judge_failure_count", "judge_timeout_count")
            )
            or not isinstance(identity, Mapping)
            or identity.get("role") != "candidate"
            or identity.get("schema_version")
            != "aworld.self_evolve.evaluation_identity.v1"
            or identity.get("dataset_split") != split
            or identity.get("variant_fingerprint") != package_fingerprint
            or identity.get("fingerprint")
            != metrics.get("evaluation_identity_fingerprint")
            or not isinstance(cases, list)
            or not cases
            or len(cases) > 1024
            or any(not _identifier(case) for case in cases)
            or len(set(cases)) != len(cases)
        ):
            continue
        gates = split_gates.get(split, ())
        failed = {gate.gate_name for gate in gates if gate.passed is False}
        passed = (
            {gate.gate_name for gate in gates if gate.passed is True} - failed
        ) & _CHECKPOINT_GATES
        checkpoints[split] = {
            "evaluation_fingerprint": identity.get("fingerprint"),
            "backend_fingerprint": identity.get("backend_fingerprint"),
            "case_panel_fingerprint": "sha256:"
            + hashlib.sha256(
                json.dumps(cases, separators=(",", ":")).encode()
            ).hexdigest(),
            "passed_gates": sorted(passed),
        }
    context = {
        "schema_version": _SCHEMA,
        "candidate_id": candidate.candidate_id,
        "package_fingerprint": package_fingerprint,
        "target_fingerprint": candidate.target_fingerprint,
        "parent_candidate_ids": candidate.parent_candidate_ids,
        "checkpoints": checkpoints,
    }
    result = []
    for item in feedback:
        package = item.metrics.get("repair_candidate_package")
        selection = bounded_repair_selection(
            {**context, "source_fingerprint": repair_source_fingerprint(package)},
            candidate_id=candidate.candidate_id,
        )
        if (
            selection is not None
            and item.variant_id == candidate.candidate_id
            and item.dataset_split in checkpoints
            and isinstance(package, Mapping)
            and package.get("candidate_id") == candidate.candidate_id
        ):
            item = replace(
                item, metrics={**item.metrics, "repair_selection": selection}
            )
        result.append(item)
    return tuple(result)


def superseded_repair_indexes(repairs: Sequence[tuple[int, Mapping]]) -> set[int]:
    """Prefer a direct child only when it has passed the ancestor's checkpoint.

    Ordinary split checkpoints never supersede an independent regression
    frontier. Unrelated candidates retain the existing depth/recency ordering.
    """
    contexts = {}
    eligible = {}
    ambiguous = set()
    for index, item in repairs:
        candidate_id = item.get("variant_id")
        if not _identifier(candidate_id):
            continue
        package = item.get("repair_candidate_package")
        context = bounded_repair_selection(
            item.get("repair_selection"), candidate_id=candidate_id
        )
        if (
            context is None
            or not isinstance(package, Mapping)
            or package.get("candidate_id") != candidate_id
            or context.get("source_fingerprint") != repair_source_fingerprint(package)
        ):
            ambiguous.add(candidate_id)
            continue
        if candidate_id in contexts and contexts[candidate_id] != context:
            ambiguous.add(candidate_id)
        contexts[candidate_id] = context
        eligible[index] = context
    superseded = set()

    def cyclic(child_id, ancestor_id):
        pending, seen = [ancestor_id], set()
        while pending and len(seen) <= len(contexts):
            current = pending.pop()
            if current == child_id:
                return True
            if current not in seen:
                seen.add(current)
                pending.extend(
                    contexts.get(current, {}).get("parent_candidate_ids", ())
                )
        return bool(pending)

    for index, ancestor in repairs:
        ancestor_id, split, failures = (
            ancestor.get("variant_id"),
            ancestor.get("dataset_split"),
            ancestor.get("failed_gates"),
        )
        if not _identifier(ancestor_id):
            continue
        context = eligible.get(index)
        if (
            not context
            or ancestor_id in ambiguous
            or split not in context["checkpoints"]
            or not isinstance(failures, (list, tuple))
            or not failures
            or any(
                not isinstance(gate, str) or gate not in _CHECKPOINT_GATES
                for gate in failures
            )
        ):
            continue
        old_checkpoint = context["checkpoints"][split]
        for child_index, child in repairs:
            child_id = child.get("variant_id")
            if not _identifier(child_id):
                continue
            descendant = eligible.get(child_index)
            if (
                not descendant
                or child_id in ambiguous
                or ancestor_id not in descendant["parent_candidate_ids"]
                or cyclic(child_id, ancestor_id)
                or descendant["target_fingerprint"] != context["target_fingerprint"]
                or child.get("dataset_split") not in descendant["checkpoints"]
            ):
                continue
            checkpoint = descendant["checkpoints"].get(split)
            if (
                checkpoint
                and all(
                    checkpoint[key] == old_checkpoint[key]
                    for key in ("backend_fingerprint", "case_panel_fingerprint")
                )
                and set(failures).issubset(checkpoint["passed_gates"])
            ):
                superseded.add(index)
                break
    return superseded
