"""Prior run, scheduler, package, and rejected lesson history."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from aworld.self_evolve.budget import SchedulerState
from aworld.self_evolve.candidate_package import candidate_package_fingerprint
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.types import (
    CandidateFileDelta,
    CandidateVariant,
    EvaluationSummary,
    SelfEvolveTargetRef,
    SkillStructuralEditAction,
    SkillStructuralEditIntent,
)
from aworld.self_evolve.feedback_history import (
    _feedback_from_report,
    _historical_feedback_metrics,
    _non_authoritative_candidate_rejection,
)
from aworld.self_evolve.history_support import _load_json_mapping
from aworld.self_evolve.lineage_history import _lineage_records_from_report

_SEMANTIC_DEDUP_IDENTITY_VERSION = "aworld.self_evolve.semantic_dedup.v2"


@dataclass(frozen=True)
class _SemanticLessonFingerprint:
    semantic_package_fingerprint: str
    lesson_set_fingerprint: str
    verification_contract_fingerprint: str

def _load_candidate_variant(path: Path) -> CandidateVariant:
    payload = _load_json_mapping(path)
    target_payload = payload.get("target")
    if not isinstance(target_payload, Mapping):
        raise ValueError(f"candidate JSON is missing target: {path}")
    return CandidateVariant(
        candidate_id=str(payload.get("candidate_id") or ""),
        target=SelfEvolveTargetRef(
            target_type=str(target_payload.get("target_type") or ""),
            target_id=str(target_payload.get("target_id") or ""),
            path=(
                str(target_payload.get("path"))
                if target_payload.get("path") is not None
                else None
            ),
        ),
        content=str(payload.get("content") or ""),
        rationale=str(payload.get("rationale") or ""),
        parent_candidate_ids=tuple(
            str(item)
            for item in payload.get("parent_candidate_ids", ())
            if isinstance(item, str)
        ),
        target_fingerprint=(
            str(payload.get("target_fingerprint"))
            if payload.get("target_fingerprint") is not None
            else None
        ),
        files=tuple(
            CandidateFileDelta(
                path=str(item.get("path") or ""),
                operation=str(item.get("operation") or "upsert"),
                content=(
                    str(item.get("content"))
                    if item.get("content") is not None
                    else None
                ),
                executable=item.get("executable") is True,
            )
            for item in payload.get("files", ())
            if isinstance(item, Mapping)
        ),
        structural_edit_intent=_load_structural_edit_intent(
            payload.get("structural_edit_intent")
        ),
    )


def _load_structural_edit_intent(
    value: Any,
) -> SkillStructuralEditIntent | None:
    if not isinstance(value, Mapping):
        return None
    actions = value.get("actions")
    if not isinstance(actions, list):
        return None
    try:
        return SkillStructuralEditIntent(
            schema_version=str(value.get("schema_version") or ""),
            authority=str(value.get("authority") or ""),
            authorization=str(value.get("authorization") or ""),
            reason=str(value.get("reason") or ""),
            base_content_fingerprint=str(value.get("base_content_fingerprint") or ""),
            candidate_content_fingerprint=str(
                value.get("candidate_content_fingerprint") or ""
            ),
            actions=tuple(
                SkillStructuralEditAction(
                    action=str(item.get("action") or ""),
                    section_path=tuple(
                        str(part)
                        for part in item.get("section_path", ())
                        if isinstance(part, str)
                    ),
                    base_section_fingerprint=(
                        str(item.get("base_section_fingerprint"))
                        if item.get("base_section_fingerprint") is not None
                        else None
                    ),
                    result_section_fingerprint=str(
                        item.get("result_section_fingerprint") or ""
                    ),
                )
                for item in actions
                if isinstance(item, Mapping)
            ),
        )
    except (TypeError, ValueError):
        return None


def _report_matches_target(
    report: Mapping[str, Any],
    target: SelfEvolveTargetRef,
    *,
    require_path: bool = True,
) -> bool:
    payload = report.get("target")
    if not isinstance(payload, Mapping):
        return False
    return (
        payload.get("target_type") == target.target_type
        and payload.get("target_id") == target.target_id
        and (
            not require_path
            or target.path is None
            or payload.get("path") is None
            or str(payload.get("path")) == str(target.path)
        )
    )


def _load_prior_rejected_feedback(
    store: FilesystemSelfEvolveStore,
    target: SelfEvolveTargetRef,
    *,
    current_run_id: str,
    limit: int = 12,
    allowed_run_ids: Iterable[str] | None = None,
) -> tuple[EvaluationSummary, ...]:
    root = store.artifact_root
    if not root.exists():
        return ()
    feedback: list[EvaluationSummary] = []
    report_paths = _prior_report_paths(
        store,
        current_run_id=current_run_id,
        allowed_run_ids=allowed_run_ids,
    )
    for report_path in report_paths:
        if report_path.parent.name == current_run_id:
            continue
        try:
            report = _load_json_mapping(report_path)
        except Exception:
            continue
        if not _report_matches_target(
            report,
            target,
            require_path=allowed_run_ids is None,
        ):
            continue
        for item in _feedback_from_report(report, report_path=report_path):
            feedback.append(item)
            if len(feedback) >= limit:
                return tuple(feedback)
    return tuple(feedback)


def _load_prior_scheduler_state(
    store: FilesystemSelfEvolveStore,
    target: SelfEvolveTargetRef,
    *,
    current_run_id: str,
    allowed_run_ids: Iterable[str] | None,
) -> SchedulerState:
    """Restore the latest Campaign scheduler checkpoint without heuristics."""

    if not allowed_run_ids:
        return SchedulerState()
    for report_path in _prior_report_paths(
        store,
        current_run_id=current_run_id,
        allowed_run_ids=allowed_run_ids,
    ):
        try:
            report = _load_json_mapping(report_path)
        except Exception:
            continue
        if not _report_matches_target(report, target, require_path=False):
            continue
        repair_state = report.get("repair_frontier_state")
        raw_state = (
            repair_state.get("scheduler_state")
            if isinstance(repair_state, Mapping)
            else None
        )
        if not isinstance(raw_state, Mapping):
            population = report.get("population")
            decisions = (
                population.get("scheduler_decisions")
                if isinstance(population, Mapping)
                else None
            )
            if isinstance(decisions, list) and decisions:
                latest = decisions[-1]
                raw_state = latest.get("state") if isinstance(latest, Mapping) else None
        if isinstance(raw_state, Mapping):
            try:
                return SchedulerState.from_dict(raw_state)
            except (TypeError, ValueError):
                continue
    return SchedulerState()


def _load_prior_candidate_package_index(
    store: FilesystemSelfEvolveStore,
    target: SelfEvolveTargetRef,
    *,
    current_run_id: str,
    candidate_ids: set[str],
    allowed_run_ids: Iterable[str] | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Index canonical prior packages without mutating any prior artifact."""

    package_to_candidate: dict[str, str] = {}
    package_by_candidate: dict[str, str] = {}
    if not store.artifact_root.exists() or not candidate_ids:
        return package_to_candidate, package_by_candidate
    for report_path in _prior_report_paths(
        store,
        current_run_id=current_run_id,
        allowed_run_ids=allowed_run_ids,
    ):
        if report_path.parent.name == current_run_id:
            continue
        try:
            report = _load_json_mapping(report_path)
        except Exception:
            continue
        if not _report_matches_target(
            report,
            target,
            require_path=allowed_run_ids is None,
        ):
            continue
        candidate_root = report_path.parent / "candidates"
        for candidate_id in sorted(candidate_ids):
            candidate_path = candidate_root / f"{candidate_id}.json"
            if not candidate_path.is_file() or candidate_path.is_symlink():
                continue
            try:
                candidate = _load_candidate_variant(candidate_path)
            except Exception:
                continue
            if (
                candidate.target.target_type != target.target_type
                or candidate.target.target_id != target.target_id
                or (
                    allowed_run_ids is None
                    and candidate.target.path is not None
                    and target.path is not None
                    and str(candidate.target.path) != str(target.path)
                )
            ):
                continue
            fingerprint = candidate_package_fingerprint(candidate)
            package_to_candidate.setdefault(fingerprint, candidate_id)
            package_by_candidate.setdefault(candidate_id, fingerprint)
    return package_to_candidate, package_by_candidate


def _load_prior_rejected_semantic_lesson_fingerprints(
    store: FilesystemSelfEvolveStore,
    target: SelfEvolveTargetRef,
    *,
    current_run_id: str,
    limit: int = 64,
    allowed_run_ids: Iterable[str] | None = None,
) -> set[_SemanticLessonFingerprint]:
    root = store.artifact_root
    if not root.exists():
        return set()
    fingerprints: set[_SemanticLessonFingerprint] = set()
    report_paths = _prior_report_paths(
        store,
        current_run_id=current_run_id,
        allowed_run_ids=allowed_run_ids,
    )
    for report_path in report_paths:
        if report_path.parent.name == current_run_id:
            continue
        try:
            report = _load_json_mapping(report_path)
        except Exception:
            continue
        if not _report_matches_target(
            report,
            target,
            require_path=allowed_run_ids is None,
        ):
            continue
        rejected_ids = _rejected_candidate_ids_from_report(report)
        if not rejected_ids and str(report.get("status")) != "rejected":
            continue
        for lineage in _lineage_records_from_report(
            report,
            report_path=report_path,
            import_missing=True,
        ):
            candidate_id = lineage.get("candidate_id")
            if rejected_ids and candidate_id not in rejected_ids:
                continue
            identity_version = lineage.get("semantic_identity_version")
            semantic_package = lineage.get("semantic_package_fingerprint")
            lesson_set = lineage.get("lesson_set_fingerprint")
            verification_contract = lineage.get("verification_contract_fingerprint")
            # Legacy two-field lineage remains importable for audit and lesson
            # extraction, but it cannot prove that candidate-owned files or the
            # active verifier contract are equivalent and therefore cannot hard
            # filter a new candidate.
            if (
                identity_version == _SEMANTIC_DEDUP_IDENTITY_VERSION
                and isinstance(semantic_package, str)
                and semantic_package
                and isinstance(lesson_set, str)
                and lesson_set
                and isinstance(verification_contract, str)
                and verification_contract
            ):
                fingerprints.add(
                    _SemanticLessonFingerprint(
                        semantic_package_fingerprint=semantic_package,
                        lesson_set_fingerprint=lesson_set,
                        verification_contract_fingerprint=(verification_contract),
                    )
                )
                if len(fingerprints) >= limit:
                    return fingerprints
    return fingerprints


def _rejected_candidate_ids_from_report(report: Mapping[str, Any]) -> set[str]:
    rejected: set[str] = set()
    retryable_infra_rejections: set[str] = set()
    iterations = report.get("iterations")
    if isinstance(iterations, list):
        for item in iterations:
            if not isinstance(item, Mapping):
                continue
            if item.get("status") != "rejected":
                continue
            candidate_id = item.get("candidate_id")
            if isinstance(candidate_id, str) and candidate_id:
                if _non_authoritative_candidate_rejection(
                    _historical_feedback_metrics(item)
                ):
                    retryable_infra_rejections.add(candidate_id)
                    continue
                rejected.add(candidate_id)
    selected = report.get("selected_candidate_id")
    if (
        str(report.get("status")) == "rejected"
        and isinstance(selected, str)
        and selected
        and selected not in retryable_infra_rejections
    ):
        rejected.add(selected)
    return rejected


def _prior_report_paths(
    store: FilesystemSelfEvolveStore,
    *,
    current_run_id: str,
    allowed_run_ids: Iterable[str] | None,
) -> list[Path]:
    if allowed_run_ids is None:
        return sorted(
            store.artifact_root.glob("*/report.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    paths: list[Path] = []
    for run_id in reversed(tuple(dict.fromkeys(str(item) for item in allowed_run_ids))):
        if run_id == current_run_id:
            continue
        try:
            path = store.run_path(run_id) / "report.json"
        except ValueError:
            continue
        if path.is_file() and not path.is_symlink():
            paths.append(path)
    return paths
