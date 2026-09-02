"""Generation scheduling, policy, materialization, and population helpers.

This module is deliberately independent of :mod:`aworld.self_evolve.runner` so
the explicit-run controller can own candidate generation without a reverse
coordinator dependency.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from decimal import Decimal
from typing import Any, Callable, Iterable, Mapping

from aworld.self_evolve.budget import RepairFrontier, SchedulerDecision, SchedulerState
from aworld.self_evolve.candidate_package import (
    CandidateMutationKind,
    candidate_content_semantic_fingerprint,
    candidate_file_semantic_fingerprint,
    candidate_semantic_package_fingerprint,
    classify_candidate_mutation,
    validate_candidate_files,
)
from aworld.self_evolve.controllers.run_telemetry import _decimal_metric
from aworld.self_evolve.failure_events import (
    FailureOwner,
    FailureScope,
    FailureStage,
    ReplayFailureEvent,
    _typed_causal_feedback_event,
)
from aworld.self_evolve.feedback_diagnostics import _failure_signature_values
from aworld.self_evolve.optimizers.base import (
    CandidateOptimizer,
    OptimizerResult,
)
from aworld.self_evolve.recovery_trace import RECOVERY_TRACE_SCHEMA_VERSION
from aworld.self_evolve.population_projection import _candidate_strategy_records
from aworld.self_evolve.run_history import (
    _SEMANTIC_DEDUP_IDENTITY_VERSION,
    _SemanticLessonFingerprint,
)
from aworld.self_evolve.repair_conformance import RepairConformanceContract
from aworld.self_evolve.types import (
    CandidateFileDelta,
    CandidateVariant,
    EvaluationSummary,
    GateResult,
    OptimizerLineage,
)

_MAX_CONSECUTIVE_DUPLICATE_POPULATION_STALLS = 1
_MAX_CONSECUTIVE_MATERIALIZATION_STALLS = 2
_VERIFICATION_CONTRACT_VERSION = "aworld.self_evolve.verification_contract.v2"


def _candidate_attempt_placeholder(iteration: int, slot: int) -> str:
    return f"candidate-placeholder-{iteration + 1}-{slot + 1}"


def _candidate_generation_actual_usage(
    telemetry: object,
) -> tuple[int | None, Decimal | None, str]:
    """Read raw generation telemetry without double-counting token aliases."""

    if not isinstance(telemetry, Mapping):
        return None, None, "reserved_fallback_missing_telemetry"
    token_telemetry = telemetry.get("token_usage")
    if not isinstance(token_telemetry, Mapping):
        token_telemetry = telemetry
    tokens: int | None = None
    source = "reserved_fallback_missing_tokens"
    total = token_telemetry.get("total_tokens")
    if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
        tokens = total
        source = "telemetry_total_tokens"
    else:
        for input_key, output_key, pair_source in (
            ("input_tokens", "output_tokens", "telemetry_input_output_tokens"),
            (
                "prompt_tokens",
                "completion_tokens",
                "telemetry_prompt_completion_tokens",
            ),
        ):
            input_tokens = token_telemetry.get(input_key)
            output_tokens = token_telemetry.get(output_key)
            if all(
                isinstance(value, int) and not isinstance(value, bool) and value >= 0
                for value in (input_tokens, output_tokens)
            ):
                tokens = int(input_tokens) + int(output_tokens)
                source = pair_source
                break
    wall = None
    for key in ("wall_seconds", "elapsed_seconds", "execution_seconds"):
        wall = _decimal_metric(telemetry.get(key))
        if wall is not None:
            source += f"+telemetry_{key}"
            break
    return tokens, wall, source


def _typed_repair_frontiers(
    feedback: Iterable[EvaluationSummary],
) -> tuple[RepairFrontier, ...]:
    """Build scheduler input solely from typed causal failure envelopes."""

    frontiers: dict[str, RepairFrontier] = {}
    for summary in feedback:
        raw_events = summary.metrics.get("causal_failure_events")
        event_payloads = raw_events if isinstance(raw_events, (list, tuple)) else ()
        for payload in event_payloads:
            if not isinstance(payload, Mapping):
                continue
            try:
                event = _typed_causal_feedback_event(payload)
            except (TypeError, ValueError):
                continue
            if not _causal_event_drives_repair_frontier(
                code=event.code,
                category=event.category,
            ):
                continue
            frontier = RepairFrontier(
                semantic_key=event.semantic_key,
                progress=max(
                    event.occurrence_count,
                    event.affected_member_count,
                    event.distinct_source_count,
                ),
                owner=event.owner,
                scope=event.scope,
                repairable=event.repairable,
            )
            previous = frontiers.get(frontier.semantic_key)
            if previous is None or frontier.progress > previous.progress:
                frontiers[frontier.semantic_key] = frontier
        # Lesson memory intentionally stores a bounded scalar projection of a
        # causal aggregate instead of duplicating its full envelope.  Restore
        # the scheduler frontier from that typed projection so Campaign
        # continuation does not lose the exact repair signal that justified a
        # fresh-cycle budget.  Only causal_failure_memory lessons are eligible;
        # free-form lessons remain generation context, not scheduler control.
        if (
            event_payloads
            or summary.metrics.get("lesson_type") != "causal_failure_memory"
        ):
            continue
        semantic_key = summary.metrics.get("causal_semantic_key")
        causal_code = summary.metrics.get("causal_code")
        causal_category = summary.metrics.get("causal_category")
        raw_owner = summary.metrics.get("causal_owner")
        raw_scope = summary.metrics.get("causal_scope")
        repairable = summary.metrics.get("repairable")
        if (
            not isinstance(semantic_key, str)
            or not semantic_key
            or not isinstance(repairable, bool)
            or not _causal_event_drives_repair_frontier(
                code=(str(causal_code) if causal_code is not None else ""),
                category=(str(causal_category) if causal_category is not None else ""),
            )
        ):
            continue
        try:
            owner = FailureOwner(str(raw_owner))
            scope = FailureScope(str(raw_scope))
        except ValueError:
            continue
        frontier = RepairFrontier(
            semantic_key=semantic_key,
            progress=max(
                _positive_int_or_default(
                    summary.metrics.get("occurrence_count"), default=1
                ),
                _nonnegative_int_or_default(
                    summary.metrics.get("distinct_source_count"), default=0
                ),
                len(_string_list(summary.metrics.get("affected_case_ids"))),
            ),
            owner=owner,
            scope=scope,
            repairable=repairable,
        )
        previous = frontiers.get(frontier.semantic_key)
        if previous is None or frontier.progress > previous.progress:
            frontiers[frontier.semantic_key] = frontier
    # Preserve causal feedback order. The scheduler uses the most recently
    # discovered eligible frontier as the tie-breaker, rather than an opaque
    # semantic-hash ordering.
    return tuple(frontiers.values())


def _causal_event_drives_repair_frontier(*, code: str, category: str) -> bool:
    """Keep propagation and verification summaries out of repair scheduling.

    These events describe why downstream work did not run or why evidence could
    not be consumed.  Their affected-member counts are useful diagnostics, but
    treating them as physical repair progress lets one failed member masquerade
    as progress across every subsequently blocked member.
    """

    return bool(
        category != "authoritative_early_stop"
        and code
        not in {
            "authoritative_candidate_frontier_unreachable",
            "replay_confidence",
        }
    )


def _optimizer_stored_candidate_admission_reason(
    optimizer: CandidateOptimizer,
) -> str | None:
    declaration = getattr(
        optimizer,
        "stored_candidate_admission_reason",
        None,
    )
    if not callable(declaration):
        return None
    try:
        reason = declaration()
    except (TypeError, ValueError):
        return None
    return reason if isinstance(reason, str) and reason.strip() else None


def _optimizer_opens_repair_frontier_after_stored_candidate(
    optimizer: CandidateOptimizer,
) -> bool:
    """Whether a stored-candidate optimizer may mutate after its first attempt."""

    declaration = getattr(
        optimizer,
        "opens_repair_frontier_after_stored_candidate",
        None,
    )
    if not callable(declaration):
        return True
    try:
        return declaration() is True
    except (TypeError, ValueError):
        return False




def _candidate_materialization_stall_signature(
    failures: Iterable[Mapping[str, object]],
) -> str | None:
    """Identify repeated typed generation failures without candidate identity."""

    shapes: list[dict[str, object]] = []
    for failure in failures:
        details = failure.get("details")
        typed_values = [
            (key, value)
            for key, value in _failure_signature_values(details)
            if key
            in {
                "code",
                "failure_fingerprint",
                "proof_fingerprint",
                "stage",
            }
        ]
        shapes.append(
            {
                "code": failure.get("code"),
                "stage": failure.get("stage"),
                "representation": failure.get("representation"),
                "field_path": failure.get("field_path"),
                "contract_fingerprint": failure.get("contract_fingerprint"),
                "typed_values": typed_values,
            }
        )
    if not shapes:
        return None
    return hashlib.sha256(
        json.dumps(
            sorted(shapes, key=lambda item: json.dumps(item, sort_keys=True)),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()












def _scheduler_state_with_mutation_families(
    state: SchedulerState,
    *,
    decision: SchedulerDecision,
    optimizer_diagnostics: Mapping[str, object],
) -> SchedulerState:
    focused_keys = tuple(
        dict.fromkeys(
            slot.semantic_key
            for slot in decision.slots
            if slot.semantic_key is not None
        )
    )
    raw_strategies = optimizer_diagnostics.get("candidate_strategies")
    if not focused_keys or not isinstance(raw_strategies, (list, tuple)):
        return state
    families = {
        str(strategy.get("candidate_family"))
        for strategy in raw_strategies
        if isinstance(strategy, Mapping)
        and isinstance(strategy.get("candidate_family"), str)
        and str(strategy.get("candidate_family")).strip()
    }
    if not families:
        return state
    family_map = {
        key: tuple(values) for key, values in state.frontier_mutation_families.items()
    }
    for semantic_key in focused_keys:
        family_map[semantic_key] = tuple(
            sorted({*family_map.get(semantic_key, ()), *families})
        )
    return replace(state, frontier_mutation_families=family_map)


def _positive_int_or_default(value: Any, *, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return max(1, int(value))


def _nonnegative_int_or_default(value: Any, *, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return max(0, int(value))


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item]


def _canonicalize_verified_prerequisite_files(
    candidate: CandidateVariant,
    feedback_items: Iterable[EvaluationSummary],
) -> tuple[CandidateVariant, GateResult | None, int]:
    """Freeze a verified support surface while composing target behavior.

    Formatting-only transport differences are restored to the exact verified
    package so replay evidence remains reusable. Material support changes are
    rejected unless a later typed failure opens a non-prerequisite repair
    frontier for those files.
    """

    expected_files = _verified_prerequisite_files(candidate, feedback_items)
    if expected_files is None:
        return candidate, None, 0
    actual_files = validate_candidate_files(candidate.files)
    expected_by_path = {item.path: item for item in expected_files}
    actual_by_path = {item.path: item for item in actual_files}
    changed_paths = sorted(set(expected_by_path) ^ set(actual_by_path))
    for path in sorted(set(expected_by_path) & set(actual_by_path)):
        if candidate_file_semantic_fingerprint(
            expected_by_path[path]
        ) != candidate_file_semantic_fingerprint(actual_by_path[path]):
            changed_paths.append(path)
    if changed_paths:
        event = ReplayFailureEvent(
            code="verified_prerequisite_support_mutation",
            owner=FailureOwner.CANDIDATE,
            stage=FailureStage.CANDIDATE_GENERATION,
            scope=FailureScope.CANDIDATE,
            repairable=True,
            category="candidate_composition",
            summary=("target-behavior composition changed a verified support surface"),
            diagnostics={
                "changed_file_count": len(changed_paths),
                "changed_paths": changed_paths[:16],
            },
        )
        return (
            candidate,
            GateResult(
                gate_name="verified_prerequisite_fidelity",
                passed=False,
                reason=(
                    "target-behavior composition must preserve verified "
                    "candidate-owned support files"
                ),
                details={
                    "failure_class": "candidate",
                    "failure_owner": FailureOwner.CANDIDATE.value,
                    "failure_scope": FailureScope.CANDIDATE.value,
                    "repairable": True,
                    "code": event.code,
                    "changed_paths": changed_paths[:16],
                    "failure_event": event.to_dict(),
                    "causal_failure_events": [event.to_dict()],
                },
            ),
            0,
        )
    canonicalized_count = sum(
        expected_by_path[path] != actual_by_path[path] for path in expected_by_path
    )
    if not canonicalized_count and actual_files == expected_files:
        return candidate, None, 0
    return replace(candidate, files=expected_files), None, canonicalized_count


def _verified_prerequisite_files(
    candidate: CandidateVariant,
    feedback_items: Iterable[EvaluationSummary],
) -> tuple[CandidateFileDelta, ...] | None:
    parent_ids = set(candidate.parent_candidate_ids)
    if not parent_ids:
        return None
    for feedback in reversed(tuple(feedback_items)):
        metrics = feedback.metrics
        if metrics.get("candidate_status") != "prerequisite":
            continue
        package = metrics.get("repair_candidate_package")
        if not isinstance(package, Mapping):
            continue
        candidate_id = package.get("candidate_id")
        if not isinstance(candidate_id, str) or candidate_id not in parent_ids:
            continue
        raw_files = package.get("files")
        if not isinstance(raw_files, list):
            continue
        try:
            return validate_candidate_files(
                CandidateFileDelta(
                    path=str(item.get("path") or ""),
                    operation=str(item.get("operation") or "upsert"),
                    content=(
                        item.get("content")
                        if isinstance(item.get("content"), str)
                        else None
                    ),
                    executable=item.get("executable") is True,
                )
                for item in raw_files
                if isinstance(item, Mapping)
            )
        except ValueError:
            return None
    return None




def _rank_candidate_population(
    candidates: tuple[CandidateVariant, ...],
    *,
    optimizer_diagnostics: Mapping[str, object],
    current_content: str,
) -> tuple[CandidateVariant, ...]:
    if len(candidates) <= 1:
        return candidates
    strategy_by_candidate = {
        str(record.get("candidate_id")): record
        for record in _candidate_strategy_records(
            ({"diagnostics": optimizer_diagnostics},)
        )
        if isinstance(record.get("candidate_id"), str)
    }
    if not strategy_by_candidate:
        return tuple(
            candidate
            for _, candidate in sorted(
                enumerate(candidates),
                key=lambda item: (
                    _candidate_mutation_rank(
                        item[1],
                        current_content=current_content,
                    ),
                    item[0],
                ),
            )
        )
    return tuple(
        sorted(
            candidates,
            key=lambda candidate: _candidate_population_rank_key(
                candidate,
                strategy=strategy_by_candidate.get(candidate.candidate_id) or {},
                current_content=current_content,
            ),
        )
    )


def _candidate_population_rank_key(
    candidate: CandidateVariant,
    *,
    strategy: Mapping[str, object],
    current_content: str,
) -> tuple[int, int, int, int, int, int, str]:
    mutation_rank = _candidate_mutation_rank(
        candidate,
        current_content=current_content,
    )
    priority_rank = {"high": 0, "medium": 1, "low": 2}.get(
        str(strategy.get("replay_priority") or "low"),
        2,
    )
    policy_assessment = strategy.get("policy_assessment")
    policy_risk_rank = (
        1
        if isinstance(policy_assessment, Mapping)
        and policy_assessment.get("enforcement") == "heuristic"
        else 0
    )
    addressed_count = _sequence_length(strategy.get("addressed_lessons"))
    preserve_count = _sequence_length(strategy.get("preserved_success_behaviors"))
    char_growth = max(0, len(candidate.content) - len(current_content))
    line_growth = max(
        0,
        len(candidate.content.splitlines()) - len(current_content.splitlines()),
    )
    # Prefer candidates that explicitly address lessons and preserve successful
    # behavior, then keep replay cost bounded by favoring smaller deltas.
    return (
        policy_risk_rank,
        mutation_rank,
        priority_rank,
        -addressed_count,
        -preserve_count,
        char_growth + (line_growth * 80),
        candidate.candidate_id,
    )


def _candidate_mutation_rank(
    candidate: CandidateVariant,
    *,
    current_content: str,
) -> int:
    mutation = classify_candidate_mutation(
        candidate,
        current_content=current_content,
    )
    if mutation.target_behavior_changed:
        return 0
    if mutation.kind is CandidateMutationKind.EVALUATION_SUPPORT:
        return 1
    return 2


def _sequence_length(value: object) -> int:
    if isinstance(value, (list, tuple)):
        return len(value)
    return 0


def _known_duplicate_candidate_count(
    candidates: tuple[CandidateVariant, ...],
    *,
    rejected_candidate_ids: set[str],
    accepted_candidate_ids: set[str],
) -> int:
    return sum(
        1
        for candidate in candidates
        if candidate.candidate_id in rejected_candidate_ids
        or candidate.candidate_id in accepted_candidate_ids
    )


def _verification_contract_fingerprint(
    *,
    target_fingerprint: str,
    replay_preflight_fingerprint: str,
    apply_policy: str,
    verification_settings: Mapping[str, object],
    repair_contract: RepairConformanceContract | None,
    verification_contract_version: str = _VERIFICATION_CONTRACT_VERSION,
) -> str:
    payload = {
        "schema_version": verification_contract_version,
        "recovery_trace_schema_version": RECOVERY_TRACE_SCHEMA_VERSION,
        "target_fingerprint": target_fingerprint,
        "replay_preflight_fingerprint": replay_preflight_fingerprint,
        "apply_policy": apply_policy,
        "verification_settings": dict(verification_settings),
        "repair_conformance": (
            repair_contract.to_public_dict() if repair_contract is not None else None
        ),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _with_versioned_semantic_lineage(
    optimizer_result: OptimizerResult,
    *,
    target_fingerprint: str,
    replay_preflight_fingerprint: str,
    apply_policy: str,
    verification_settings: Mapping[str, object],
    verification_contract_fingerprint: Callable[..., str] = (
        _verification_contract_fingerprint
    ),
) -> OptimizerResult:
    """Attach the complete, versioned identity used for historical filtering."""

    candidates = {
        candidate.candidate_id: candidate for candidate in optimizer_result.candidates
    }
    contracts = {
        candidate_id: contract
        for candidate_id, contract in optimizer_result.private_context.items()
        if isinstance(contract, RepairConformanceContract)
    }
    enriched: list[OptimizerLineage] = []
    for lineage in optimizer_result.lineage:
        candidate = candidates.get(lineage.candidate_id)
        if candidate is None:
            enriched.append(lineage)
            continue
        semantic_content = (
            lineage.semantic_fingerprint
            or candidate_content_semantic_fingerprint(candidate.content)
        )
        enriched.append(
            replace(
                lineage,
                semantic_fingerprint=semantic_content,
                semantic_identity_version=_SEMANTIC_DEDUP_IDENTITY_VERSION,
                semantic_package_fingerprint=(
                    candidate_semantic_package_fingerprint(
                        candidate,
                        content_semantic_fingerprint=semantic_content,
                    )
                ),
                verification_contract_fingerprint=(
                    verification_contract_fingerprint(
                        target_fingerprint=target_fingerprint,
                        replay_preflight_fingerprint=(replay_preflight_fingerprint),
                        apply_policy=apply_policy,
                        verification_settings=verification_settings,
                        repair_contract=contracts.get(lineage.candidate_id),
                    )
                ),
            )
        )
    return replace(optimizer_result, lineage=tuple(enriched))


def _lineage_semantic_lesson_fingerprints(
    lineage_items: tuple[OptimizerLineage, ...],
) -> dict[str, _SemanticLessonFingerprint]:
    fingerprints: dict[str, _SemanticLessonFingerprint] = {}
    for lineage in lineage_items:
        if (
            lineage.semantic_identity_version == _SEMANTIC_DEDUP_IDENTITY_VERSION
            and lineage.semantic_package_fingerprint
            and lineage.lesson_set_fingerprint
            and lineage.verification_contract_fingerprint
        ):
            fingerprints[lineage.candidate_id] = _SemanticLessonFingerprint(
                semantic_package_fingerprint=(lineage.semantic_package_fingerprint),
                lesson_set_fingerprint=lineage.lesson_set_fingerprint,
                verification_contract_fingerprint=(
                    lineage.verification_contract_fingerprint
                ),
            )
    return fingerprints


def _semantic_lesson_duplicate_count(
    candidates: tuple[CandidateVariant, ...],
    *,
    lineage_fingerprints: Mapping[str, _SemanticLessonFingerprint],
    rejected_semantic_lesson_fingerprints: set[_SemanticLessonFingerprint],
) -> int:
    return sum(
        1
        for candidate in candidates
        if _is_semantic_lesson_duplicate(
            candidate.candidate_id,
            lineage_fingerprints=lineage_fingerprints,
            rejected_semantic_lesson_fingerprints=rejected_semantic_lesson_fingerprints,
        )
    )


def _is_semantic_lesson_duplicate(
    candidate_id: str,
    *,
    lineage_fingerprints: Mapping[str, _SemanticLessonFingerprint],
    rejected_semantic_lesson_fingerprints: set[_SemanticLessonFingerprint],
) -> bool:
    fingerprint = lineage_fingerprints.get(candidate_id)
    return (
        fingerprint is not None and fingerprint in rejected_semantic_lesson_fingerprints
    )


def _semantic_lesson_duplicate_feedback(
    candidate: CandidateVariant,
    *,
    fingerprint: _SemanticLessonFingerprint,
) -> EvaluationSummary:
    event = ReplayFailureEvent(
        code="duplicate_semantic_lesson",
        owner=FailureOwner.CANDIDATE,
        stage=FailureStage.ADAPTATION,
        scope=FailureScope.CANDIDATE,
        repairable=True,
        category="candidate_generation_dedup",
        summary=(
            "candidate repeats a historically rejected complete semantic package "
            "under the same lesson set and verification contract"
        ),
        contract_fingerprint=fingerprint.verification_contract_fingerprint,
        diagnostics={
            "semantic_identity_version": _SEMANTIC_DEDUP_IDENTITY_VERSION,
            "semantic_package_fingerprint": (fingerprint.semantic_package_fingerprint),
            "lesson_set_fingerprint": fingerprint.lesson_set_fingerprint,
            "required_delta": (
                "change target behavior or candidate-owned files materially; "
                "renaming, reformatting, or repeating the same package is insufficient"
            ),
        },
    )
    event_payload = event.to_dict()
    return EvaluationSummary(
        variant_id=candidate.candidate_id,
        dataset_split="validation",
        metrics={
            "failed_gates": ["duplicate_semantic_lesson"],
            "candidate_status": "rejected",
            "failure_class": "candidate",
            "failure_owner": FailureOwner.CANDIDATE.value,
            "failure_scope": FailureScope.CANDIDATE.value,
            "repairable": True,
            "code": "duplicate_semantic_lesson",
            "semantic_identity_version": _SEMANTIC_DEDUP_IDENTITY_VERSION,
            "semantic_package_fingerprint": (fingerprint.semantic_package_fingerprint),
            "lesson_set_fingerprint": fingerprint.lesson_set_fingerprint,
            "verification_contract_fingerprint": (
                fingerprint.verification_contract_fingerprint
            ),
            "required_behaviors": [
                "produce a materially different complete candidate package"
            ],
            "failure_event": event_payload,
            "causal_failure_events": [event_payload],
        },
    )
