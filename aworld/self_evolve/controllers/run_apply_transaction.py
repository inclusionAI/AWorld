"""Verified publish transaction with drift checks, rollback, and activation."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from aworld.self_evolve.lineage_history import _with_release_lesson_mapping
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.controllers.run_observers import safe_emit_progress
from aworld.self_evolve.targets import SelfEvolveTarget, TargetSnapshotStaleError
from aworld.self_evolve.target_package import _target_runtime_skill_path
from aworld.self_evolve.types import CandidateVariant, EvaluationSummary
from aworld.skills.release import normalize_verified_skill_release


class ApplyEffectCompensator(Protocol):
    """Restore one runtime effect using the result returned by its forward call."""

    def __call__(
        self,
        candidate: CandidateVariant,
        effect_result: object | None,
    ) -> Any: ...


@dataclass(frozen=True)
class ApplyTransactionRequest:
    run_id: str
    target: SelfEvolveTarget
    candidate: CandidateVariant
    expected_package_fingerprint: str | None = None
    addressed_lesson_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ApplyTransactionPolicy:
    release_state: str = "verified"
    published: bool = True


@dataclass(frozen=True)
class ApplyTransactionRuntime:
    store: FilesystemSelfEvolveStore
    post_apply_evaluator: Callable[[CandidateVariant], Any]
    runtime_skill_activator: Callable[[CandidateVariant], Any] | None = None
    runtime_registry_refresher: Callable[[CandidateVariant], Any] | None = None
    progress_callback: Callable[[str, str], Any] | None = None
    runtime_skill_compensator: ApplyEffectCompensator | None = None
    runtime_registry_compensator: ApplyEffectCompensator | None = None


@dataclass(frozen=True)
class ApplyTransactionResult:
    report: dict[str, object]


@dataclass(frozen=True)
class ApplyTransactionExecution:
    policy: ApplyTransactionPolicy
    runtime: ApplyTransactionRuntime

    async def execute(
        self,
        request: ApplyTransactionRequest,
    ) -> ApplyTransactionResult:
        return await execute_apply_transaction(request, self.policy, self.runtime)


@dataclass
class _RuntimeSideEffectState:
    registry_began: bool = False
    registry_succeeded: bool = False
    registry_compensated: bool = False
    activation_began: bool = False
    activation_succeeded: bool = False
    activation_compensated: bool = False
    registry_result: object | None = None
    activation_result: object | None = None

    def to_dict(self) -> dict[str, bool]:
        return {
            "registry_began": self.registry_began,
            "registry_succeeded": self.registry_succeeded,
            "registry_compensated": self.registry_compensated,
            "activation_began": self.activation_began,
            "activation_succeeded": self.activation_succeeded,
            "activation_compensated": self.activation_compensated,
        }


def _rejected(
    metrics: Mapping[str, object],
    *,
    backup_path: str | None = None,
    journal_path: str | None = None,
) -> ApplyTransactionResult:
    return ApplyTransactionResult(
        {
            "status": "rejected",
            "metrics": dict(metrics),
            "dataset_split": "post_apply",
            "backup_path": backup_path,
            "journal_path": journal_path,
            "release_state": "rejected",
        }
    )


_runtime_skill_path = _target_runtime_skill_path

async def execute_apply_transaction(
    request: ApplyTransactionRequest,
    policy: ApplyTransactionPolicy,
    runtime: ApplyTransactionRuntime,
) -> ApplyTransactionResult:
    target = request.target
    candidate = request.candidate
    if candidate.target != target.identity:
        return _rejected(
            {
                "post_apply_passed": False,
                "release_state": "rejected",
                "code": "candidate_target_mismatch",
                "failure_class": "candidate",
                "failure_owner": "framework",
                "failure_scope": "candidate",
                "repairable": False,
                "candidate_target": {
                    "target_type": candidate.target.target_type,
                    "target_id": candidate.target.target_id,
                    "path": candidate.target.path,
                },
                "request_target": {
                    "target_type": target.identity.target_type,
                    "target_id": target.identity.target_id,
                    "path": target.identity.path,
                },
            }
        )
    evaluated_fingerprint = candidate.target_fingerprint
    try:
        apply_fingerprint = target.fingerprint_current_content()
    except Exception as exc:
        return _rejected(
            {
                "post_apply_passed": False,
                "release_state": "rejected",
                "code": "target_snapshot_unavailable",
                "failure_class": "infrastructure",
                "failure_owner": "framework",
                "failure_scope": "shared_run",
                "repairable": False,
                "error_type": type(exc).__name__,
            }
        )
    if not evaluated_fingerprint or apply_fingerprint != evaluated_fingerprint:
        return _rejected(
            {
                "post_apply_passed": False,
                "release_state": "rejected",
                "code": "target_snapshot_stale",
                "failure_class": "infrastructure",
                "failure_owner": "framework",
                "failure_scope": "shared_run",
                "repairable": False,
                "evaluated_target_fingerprint": evaluated_fingerprint,
                "apply_target_fingerprint": apply_fingerprint,
            }
        )

    original_content = target.load_current_content()
    target_path = _runtime_skill_path(target)
    backup_path, journal_path = runtime.store.write_apply_backup(
        request.run_id,
        candidate=candidate,
        original_content=original_content,
        target_path=str(target_path) if target_path is not None else target.identity.path,
    )
    try:
        runtime.store.update_apply_journal(
            journal_path,
            status="applying",
            details={
                "candidate_id": candidate.candidate_id,
                "verified_candidate_package_fingerprint": (
                    request.expected_package_fingerprint
                ),
            },
        )
    except asyncio.CancelledError as exc:
        _annotate_primary_exception(
            exc,
            _rollback_and_terminalize(
                target,
                runtime.store,
                journal_path,
                details={
                    "post_apply_passed": False,
                    "code": "apply_cancelled",
                    "cancelled_stage": "journal_applying",
                },
            ),
        )
        raise
    applied_candidate = candidate
    normalization_metrics: Mapping[str, Any] = {}
    if target.identity.target_type == "skill":
        safe_emit_progress(
            runtime.progress_callback,
            "release_normalization",
            "Normalizing verified skill content before apply",
        )
        normalized_content, normalization_metrics = normalize_verified_skill_release(
            candidate.content,
            run_id=request.run_id,
            candidate_id=candidate.candidate_id,
            original_content=original_content,
            structural_edit_intent=candidate.structural_edit_intent,
            require_exact_deletion_intent=True,
        )
        normalization_metrics = _with_release_lesson_mapping(
            normalization_metrics,
            addressed_lesson_ids=request.addressed_lesson_ids,
        )
        if not normalization_metrics.get("normalization_equivalence_passed"):
            metrics = {"post_apply_passed": False, **dict(normalization_metrics)}
            cleanup_errors = _terminalize_journal(
                runtime.store,
                journal_path,
                status="rejected",
                details={
                    "post_apply_passed": False,
                    "release_state": "rejected",
                    **dict(normalization_metrics),
                },
            )
            if cleanup_errors:
                metrics["cleanup_errors"] = list(cleanup_errors)
            return _rejected(
                metrics,
                backup_path=str(backup_path),
                journal_path=str(journal_path),
            )
        applied_candidate = replace(candidate, content=normalized_content)

    try:
        latest_fingerprint = target.fingerprint_current_content()
    except Exception as exc:
        latest_fingerprint = None
        fingerprint_error_type = type(exc).__name__
    else:
        fingerprint_error_type = None
    if latest_fingerprint != evaluated_fingerprint:
        metrics = {
            "post_apply_passed": False,
            "release_state": "rejected",
            "code": "target_snapshot_stale",
            "failure_class": "infrastructure",
            "failure_owner": "framework",
            "failure_scope": "shared_run",
            "repairable": False,
            "evaluated_target_fingerprint": evaluated_fingerprint,
            "apply_target_fingerprint": latest_fingerprint,
            "fingerprint_error_type": fingerprint_error_type,
            **dict(normalization_metrics),
        }
        cleanup_errors = _terminalize_journal(
            runtime.store,
            journal_path,
            status="rejected",
            details=metrics,
        )
        if cleanup_errors:
            metrics["cleanup_errors"] = list(cleanup_errors)
        return _rejected(
            metrics,
            backup_path=str(backup_path),
            journal_path=str(journal_path),
        )

    try:
        if applied_candidate.target.target_type == "skill" and hasattr(
            target, "apply_candidate_variant"
        ):
            target.apply_candidate_variant(
                applied_candidate,
                expected_package_fingerprint=request.expected_package_fingerprint,
                verified_content=candidate.content,
                expected_target_fingerprint=evaluated_fingerprint,
            )
        else:
            if target.fingerprint_current_content() != evaluated_fingerprint:
                raise TargetSnapshotStaleError(
                    "target snapshot changed before candidate mutation"
                )
            target.apply_candidate(applied_candidate.content)
    except TargetSnapshotStaleError:
        metrics = {
            "post_apply_passed": False,
            "release_state": "rejected",
            "code": "target_snapshot_stale",
            "failure_class": "infrastructure",
            "failure_owner": "framework",
            "failure_scope": "shared_run",
            "repairable": False,
            "evaluated_target_fingerprint": evaluated_fingerprint,
        }
        cleanup_errors = _terminalize_journal(
            runtime.store,
            journal_path,
            status="rejected",
            details=metrics,
        )
        if cleanup_errors:
            metrics["cleanup_errors"] = list(cleanup_errors)
        return _rejected(
            metrics,
            backup_path=str(backup_path),
            journal_path=str(journal_path),
        )
    except asyncio.CancelledError as exc:
        _annotate_primary_exception(
            exc,
            _rollback_and_terminalize(
                target,
                runtime.store,
                journal_path,
                details={
                    "post_apply_passed": False,
                    "code": "apply_cancelled",
                    "cancelled_stage": "candidate_mutation",
                },
            ),
        )
        raise
    except Exception as exc:
        details: dict[str, object] = {
            "post_apply_passed": False,
            "apply_error": str(exc),
        }
        cleanup_errors = _rollback_and_terminalize(
            target,
            runtime.store,
            journal_path,
            details=details,
        )
        if cleanup_errors:
            details["cleanup_errors"] = list(cleanup_errors)
        return ApplyTransactionResult(
            {
                "status": "rolled_back",
                "metrics": details,
                "dataset_split": "post_apply",
                "backup_path": str(backup_path),
                "journal_path": str(journal_path),
            }
        )

    try:
        summary = runtime.post_apply_evaluator(applied_candidate)
        if inspect.isawaitable(summary):
            summary = await summary
        if not isinstance(summary, EvaluationSummary):
            raise ValueError("post_apply_evaluator must return EvaluationSummary")
    except asyncio.CancelledError as exc:
        _annotate_primary_exception(
            exc,
            _rollback_and_terminalize(
                target,
                runtime.store,
                journal_path,
                details={
                    "post_apply_passed": False,
                    "code": "apply_cancelled",
                    "cancelled_stage": "post_apply_evaluation",
                },
            ),
        )
        raise
    except Exception as exc:
        metrics: dict[str, object] = {
            "post_apply_passed": False,
            "post_apply_error": str(exc),
        }
        cleanup_errors = _rollback_and_terminalize(
            target,
            runtime.store,
            journal_path,
            details=metrics,
        )
        if cleanup_errors:
            metrics["cleanup_errors"] = list(cleanup_errors)
        return ApplyTransactionResult(
            {
                "status": "rolled_back",
                "metrics": metrics,
                "dataset_split": "post_apply",
                "backup_path": str(backup_path),
                "journal_path": str(journal_path),
            }
        )
    if summary.metrics.get("post_apply_passed") is not True:
        metrics = dict(summary.metrics)
        cleanup_errors = _rollback_and_terminalize(
            target,
            runtime.store,
            journal_path,
            details={"post_apply_passed": False},
        )
        if cleanup_errors:
            metrics["cleanup_errors"] = list(cleanup_errors)
        return ApplyTransactionResult(
            {
                "status": "rolled_back",
                "metrics": metrics,
                "dataset_split": summary.dataset_split,
                "backup_path": str(backup_path),
                "journal_path": str(journal_path),
            }
        )

    side_effects = _RuntimeSideEffectState()
    # Refresh first: a refresh failure must never follow an irreversible activation.
    refresh_result = await _run_post_apply_effect(
        target,
        backup_path,
        journal_path,
        summary,
        applied_candidate,
        runtime.runtime_registry_refresher,
        runtime,
        side_effects,
        failure_key="registry_refresh",
    )
    if refresh_result[0] is not None:
        return ApplyTransactionResult(refresh_result[0])
    activation_result = await _run_post_apply_effect(
        target,
        backup_path,
        journal_path,
        summary,
        applied_candidate,
        runtime.runtime_skill_activator,
        runtime,
        side_effects,
        failure_key="activation",
    )
    if activation_result[0] is not None:
        return ApplyTransactionResult(activation_result[0])
    try:
        runtime.store.update_apply_journal(
            journal_path,
            status="accepted",
            details={
                "post_apply_passed": True,
                "release_state": policy.release_state,
                "published": policy.published,
            },
        )
    except BaseException as exc:
        _annotate_primary_exception(
            exc,
            await _rollback_compensate_and_terminalize(
                target,
                runtime,
                journal_path,
                applied_candidate,
                side_effects,
                details={
                    "post_apply_passed": True,
                    "code": "apply_commit_failed",
                    "commit_stage": "journal_acceptance",
                    "error_type": type(exc).__name__,
                },
            ),
        )
        raise

    package_cleanup_error: str | None = None
    if hasattr(target, "commit_candidate_variant"):
        try:
            target.commit_candidate_variant()
        except asyncio.CancelledError as exc:
            _annotate_primary_exception(
                exc,
                await _rollback_compensate_and_terminalize(
                    target,
                    runtime,
                    journal_path,
                    applied_candidate,
                    side_effects,
                    details={
                        "post_apply_passed": True,
                        "code": "apply_cancelled",
                        "cancelled_stage": "package_commit",
                    },
                ),
            )
            raise
        except Exception as exc:
            package_cleanup_error = str(exc)
    report: dict[str, object] = {
        "status": "accepted",
        "metrics": {**dict(summary.metrics), **dict(normalization_metrics)},
        "dataset_split": summary.dataset_split,
        "backup_path": str(backup_path),
        "journal_path": str(journal_path),
        "release_state": policy.release_state,
        "published": policy.published,
    }
    if package_cleanup_error is not None:
        report["package_cleanup_error"] = package_cleanup_error
    if activation_result[1] is not None:
        report["activation"] = _effect_report(activation_result[1])
    if refresh_result[1] is not None:
        report["refresh"] = _effect_report(refresh_result[1])
    return ApplyTransactionResult(report)


async def _run_post_apply_effect(
    target: SelfEvolveTarget,
    backup_path: Path,
    journal_path: Path,
    summary: EvaluationSummary,
    candidate: CandidateVariant,
    callback: Callable[[CandidateVariant], Any] | None,
    runtime: ApplyTransactionRuntime,
    side_effects: _RuntimeSideEffectState,
    *,
    failure_key: str,
) -> tuple[dict[str, object] | None, object | None]:
    if callback is None:
        return None, None
    _mark_effect_began(side_effects, failure_key)
    try:
        result = callback(candidate)
        if inspect.isawaitable(result):
            result = await result
        _mark_effect_succeeded(side_effects, failure_key, result)
        return None, result
    except asyncio.CancelledError as exc:
        _annotate_primary_exception(
            exc,
            await _rollback_compensate_and_terminalize(
                target,
                runtime,
                journal_path,
                candidate,
                side_effects,
                details={
                    "post_apply_passed": True,
                    "code": "apply_cancelled",
                    "cancelled_stage": failure_key,
                },
            ),
        )
        raise
    except Exception as exc:
        passed_key = f"{failure_key}_passed"
        error_key = f"{failure_key}_error"
        details: dict[str, object] = {
            "post_apply_passed": True,
            passed_key: False,
            error_key: str(exc),
        }
        cleanup_errors = await _rollback_compensate_and_terminalize(
            target,
            runtime,
            journal_path,
            candidate,
            side_effects,
            details=details,
        )
        metrics = dict(summary.metrics)
        metrics.update({passed_key: False, error_key: str(exc)})
        if cleanup_errors:
            metrics["cleanup_errors"] = list(cleanup_errors)
        return (
            {
                "status": "rolled_back",
                "metrics": metrics,
                "dataset_split": summary.dataset_split,
                "backup_path": str(backup_path),
                "journal_path": str(journal_path),
            },
            None,
        )


def _mark_effect_began(
    side_effects: _RuntimeSideEffectState,
    failure_key: str,
) -> None:
    if failure_key == "registry_refresh":
        side_effects.registry_began = True
    else:
        side_effects.activation_began = True


def _mark_effect_succeeded(
    side_effects: _RuntimeSideEffectState,
    failure_key: str,
    result: object,
) -> None:
    if failure_key == "registry_refresh":
        side_effects.registry_succeeded = True
        side_effects.registry_result = result
    else:
        side_effects.activation_succeeded = True
        side_effects.activation_result = result


async def _rollback_compensate_and_terminalize(
    target: SelfEvolveTarget,
    runtime: ApplyTransactionRuntime,
    journal_path: Path,
    candidate: CandidateVariant,
    side_effects: _RuntimeSideEffectState,
    *,
    details: Mapping[str, object],
) -> tuple[str, ...]:
    cleanup_errors: list[str] = []
    try:
        target.rollback()
    except BaseException as exc:
        cleanup_errors.append(f"rollback:{type(exc).__name__}:{exc}")

    compensation_steps = (
        (
            "activation",
            side_effects.activation_began,
            side_effects.activation_compensated,
            runtime.runtime_skill_compensator,
            side_effects.activation_result,
        ),
        (
            "registry",
            side_effects.registry_began,
            side_effects.registry_compensated,
            runtime.runtime_registry_compensator,
            side_effects.registry_result,
        ),
    )
    for effect_name, began, compensated, callback, effect_result in compensation_steps:
        if not began or compensated:
            continue
        if callback is None:
            cleanup_errors.append(f"{effect_name}_compensation:missing")
            continue
        try:
            result = callback(candidate, effect_result)
            if inspect.isawaitable(result):
                await result
        except BaseException as exc:
            cleanup_errors.append(
                f"{effect_name}_compensation:{type(exc).__name__}:{exc}"
            )
        else:
            if effect_name == "registry":
                side_effects.registry_compensated = True
            else:
                side_effects.activation_compensated = True

    terminal_details = dict(details)
    terminal_details["runtime_side_effects"] = side_effects.to_dict()
    if cleanup_errors:
        terminal_details["cleanup_errors"] = list(cleanup_errors)
    cleanup_errors.extend(
        _terminalize_journal(
            runtime.store,
            journal_path,
            status="rolled_back",
            details=terminal_details,
        )
    )
    return tuple(cleanup_errors)


def _rollback_and_terminalize(
    target: SelfEvolveTarget,
    store: FilesystemSelfEvolveStore,
    journal_path: Path,
    *,
    details: Mapping[str, object],
) -> tuple[str, ...]:
    cleanup_errors: list[str] = []
    try:
        target.rollback()
    except BaseException as exc:
        cleanup_errors.append(f"rollback:{type(exc).__name__}:{exc}")
    terminal_details = dict(details)
    if cleanup_errors:
        terminal_details["cleanup_errors"] = list(cleanup_errors)
    cleanup_errors.extend(
        _terminalize_journal(
            store,
            journal_path,
            status="rolled_back",
            details=terminal_details,
        )
    )
    return tuple(cleanup_errors)


def _terminalize_journal(
    store: FilesystemSelfEvolveStore,
    journal_path: Path,
    *,
    status: str,
    details: Mapping[str, object],
) -> tuple[str, ...]:
    try:
        store.update_apply_journal(journal_path, status=status, details=details)
    except BaseException as exc:
        return (f"journal:{type(exc).__name__}:{exc}",)
    return ()


def _annotate_primary_exception(
    primary: BaseException,
    cleanup_errors: tuple[str, ...],
) -> None:
    for error in cleanup_errors:
        try:
            primary.add_note(f"apply cleanup failure: {error}")
        except Exception:
            return


def _effect_report(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {"result": value}


__all__ = [
    "ApplyEffectCompensator",
    "ApplyTransactionExecution",
    "ApplyTransactionPolicy",
    "ApplyTransactionRequest",
    "ApplyTransactionResult",
    "ApplyTransactionRuntime",
    "execute_apply_transaction",
]
