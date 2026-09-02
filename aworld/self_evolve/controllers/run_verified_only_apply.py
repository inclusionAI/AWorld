"""Run-owned shadow apply for the verified-only release policy."""

from __future__ import annotations

import asyncio
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from aworld.self_evolve.controllers.run_apply_transaction import (
    ApplyTransactionExecution,
    ApplyTransactionRequest,
)
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.targets import SelfEvolveTarget, SkillTextTarget
from aworld.self_evolve.types import CandidateVariant


_SAFE_VERIFIED_TARGET_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


@dataclass(frozen=True)
class VerifiedOnlyApplyRequest:
    run_id: str
    target: SelfEvolveTarget
    candidate: CandidateVariant
    expected_package_fingerprint: str | None = None
    addressed_lesson_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class VerifiedOnlyApplyPolicy:
    safe_target_id: re.Pattern[str] = _SAFE_VERIFIED_TARGET_ID


@dataclass(frozen=True)
class VerifiedOnlyApplyRuntime:
    store: FilesystemSelfEvolveStore
    transaction_factory: Callable[[SelfEvolveTarget], ApplyTransactionExecution]


@dataclass(frozen=True)
class VerifiedOnlyApplyResult:
    report: dict[str, object]


@dataclass(frozen=True)
class VerifiedOnlyApplyExecution:
    policy: VerifiedOnlyApplyPolicy
    runtime: VerifiedOnlyApplyRuntime

    async def execute(
        self,
        request: VerifiedOnlyApplyRequest,
    ) -> VerifiedOnlyApplyResult:
        return await execute_verified_only_apply(request, self.policy, self.runtime)


def _rejected(
    metrics: dict[str, object],
    *,
    verified_target_path: str | None = None,
) -> VerifiedOnlyApplyResult:
    report: dict[str, object] = {
        "status": "rejected",
        "metrics": metrics,
        "dataset_split": "post_apply",
        "backup_path": None,
        "journal_path": None,
        "release_state": "rejected",
        "published": False,
    }
    if verified_target_path is not None:
        report["verified_target_path"] = verified_target_path
    return VerifiedOnlyApplyResult(report)


async def execute_verified_only_apply(
    request: VerifiedOnlyApplyRequest,
    policy: VerifiedOnlyApplyPolicy,
    runtime: VerifiedOnlyApplyRuntime,
) -> VerifiedOnlyApplyResult:
    target = request.target
    candidate = request.candidate
    if target.identity.target_type != "skill":
        return _rejected(
            {
                "post_apply_passed": False,
                "release_state": "rejected",
                "code": "verified_only_target_type_unsupported",
                "failure_class": "candidate",
                "target_type": target.identity.target_type,
            }
        )
    try:
        source_fingerprint_before = target.fingerprint_current_content()
    except Exception as exc:
        return _rejected(
            {
                "post_apply_passed": False,
                "release_state": "rejected",
                "code": "target_snapshot_unavailable",
                "failure_class": "infrastructure",
                "error_type": type(exc).__name__,
            }
        )
    if policy.safe_target_id.fullmatch(target.identity.target_id) is None:
        return _rejected(
            {
                "post_apply_passed": False,
                "release_state": "rejected",
                "code": "verified_target_id_unsafe",
                "failure_class": "candidate",
            }
        )

    registry_root = runtime.store.run_path(request.run_id) / "verified_targets"
    package_root = registry_root / target.identity.target_id
    isolated_skill_path = package_root / "SKILL.md"
    if package_root.exists() or package_root.is_symlink():
        return _rejected(
            {
                "post_apply_passed": False,
                "release_state": "rejected",
                "code": "verified_target_collision",
                "failure_class": "infrastructure",
            },
            verified_target_path=str(isolated_skill_path),
        )
    source_skill_path = Path(target.identity.path).resolve() if target.identity.path else None
    try:
        registry_root.mkdir(parents=True, exist_ok=True)
        if source_skill_path is not None and source_skill_path.is_file():
            shutil.copytree(source_skill_path.parent, package_root, symlinks=True)
        else:
            package_root.mkdir(parents=True, exist_ok=False)
            isolated_skill_path.write_text(
                target.load_current_content(), encoding="utf-8"
            )
    except BaseException as exc:
        cleanup_error = _remove_shadow_package(package_root)
        if isinstance(exc, asyncio.CancelledError):
            if cleanup_error is not None:
                exc.add_note(f"verified-only cleanup failure: {cleanup_error}")
            raise
        reason = str(exc)
        if cleanup_error is not None:
            reason = f"{reason}; cleanup failed: {cleanup_error}"
        return _rejected(
            {
                "post_apply_passed": False,
                "release_state": "rejected",
                "code": "verified_target_materialization_failed",
                "failure_class": "infrastructure",
                "error_type": type(exc).__name__,
                "reason": reason,
            },
            verified_target_path=str(isolated_skill_path),
        )

    isolated_target = SkillTextTarget(
        isolated_skill_path,
        target_id=target.identity.target_id,
        allow_auto_apply=True,
    )
    isolated_candidate = replace(
        candidate,
        target=isolated_target.identity,
        target_fingerprint=isolated_target.fingerprint_current_content(),
    )
    try:
        transaction = runtime.transaction_factory(isolated_target)
        transaction_result = await transaction.execute(
            ApplyTransactionRequest(
                run_id=request.run_id,
                target=isolated_target,
                candidate=isolated_candidate,
                expected_package_fingerprint=request.expected_package_fingerprint,
                addressed_lesson_ids=request.addressed_lesson_ids,
            )
        )
    except asyncio.CancelledError as exc:
        cleanup_errors = _cleanup_shadow_transaction(
            isolated_target,
            package_root,
            runtime.store,
            journal_path=None,
            details={
                "post_apply_passed": False,
                "code": "verified_only_cancelled",
            },
        )
        for error in cleanup_errors:
            exc.add_note(f"verified-only cleanup failure: {error}")
        raise
    except Exception:
        _cleanup_shadow_transaction(
            isolated_target,
            package_root,
            runtime.store,
            journal_path=None,
            details={"post_apply_passed": False, "code": "verified_only_failed"},
        )
        raise
    report = transaction_result.report
    try:
        source_fingerprint_after = target.fingerprint_current_content()
    except Exception:
        source_fingerprint_after = None
    source_unchanged = source_fingerprint_after == source_fingerprint_before
    report.update(
        {
            "published": False,
            "verified_target_path": str(isolated_skill_path),
            "source_target_path": target.identity.path,
            "source_target_fingerprint_before": source_fingerprint_before,
            "source_target_fingerprint_after": source_fingerprint_after,
            "source_target_unchanged": source_unchanged,
        }
    )
    if not source_unchanged:
        journal_value = report.get("journal_path")
        cleanup_errors = _cleanup_shadow_transaction(
            isolated_target,
            package_root,
            runtime.store,
            journal_path=(
                Path(journal_value) if isinstance(journal_value, str) else None
            ),
            details={
                "post_apply_passed": False,
                "release_state": "rejected",
                "code": "source_target_changed_during_verified_only",
            },
        )
        report["status"] = "rejected"
        report["release_state"] = "rejected"
        report.pop("verified_target_path", None)
        metrics = dict(report.get("metrics") or {})
        metrics.update(
            {
                "post_apply_passed": False,
                "release_state": "rejected",
                "code": "source_target_changed_during_verified_only",
                "failure_class": "infrastructure",
            }
        )
        if cleanup_errors:
            metrics["cleanup_errors"] = list(cleanup_errors)
        report["metrics"] = metrics
    return VerifiedOnlyApplyResult(report)


def _remove_shadow_package(package_root: Path) -> str | None:
    try:
        if package_root.is_symlink() or package_root.is_file():
            package_root.unlink(missing_ok=True)
        elif package_root.exists():
            shutil.rmtree(package_root)
    except BaseException as exc:
        return f"shadow_cleanup:{type(exc).__name__}:{exc}"
    return None


def _cleanup_shadow_transaction(
    target: SelfEvolveTarget,
    package_root: Path,
    store: FilesystemSelfEvolveStore,
    *,
    journal_path: Path | None,
    details: dict[str, object],
) -> tuple[str, ...]:
    errors: list[str] = []
    try:
        target.rollback()
    except BaseException as exc:
        errors.append(f"rollback:{type(exc).__name__}:{exc}")
    shadow_error = _remove_shadow_package(package_root)
    if shadow_error is not None:
        errors.append(shadow_error)
    if journal_path is not None:
        journal_details = dict(details)
        if errors:
            journal_details["cleanup_errors"] = list(errors)
        try:
            store.update_apply_journal(
                journal_path,
                status="rolled_back",
                details=journal_details,
            )
        except BaseException as exc:
            errors.append(f"journal:{type(exc).__name__}:{exc}")
    return tuple(errors)


__all__ = [
    "VerifiedOnlyApplyExecution",
    "VerifiedOnlyApplyPolicy",
    "VerifiedOnlyApplyRequest",
    "VerifiedOnlyApplyResult",
    "VerifiedOnlyApplyRuntime",
    "execute_verified_only_apply",
]
