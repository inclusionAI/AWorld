"""Artifact-retention orchestration for terminal self-evolve runs.

The lifecycle module owns cleanup and transaction primitives.  This controller
owns their ordering relative to durable run/report writes so the run coordinator
does not need to understand retention transaction recovery semantics.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aworld.self_evolve.lifecycle import (
    acknowledge_self_evolve_retention_transactions,
    cleanup_self_evolve_artifacts,
    read_self_evolve_retention_transactions,
)
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.types import SelfEvolveRun


@dataclass(frozen=True)
class ArtifactRetentionController:
    """Coordinates durable reports with recoverable artifact cleanup."""

    store: FilesystemSelfEvolveStore
    cleanup: Callable[..., dict[str, object]] = cleanup_self_evolve_artifacts

    def finalize_run_report(
        self,
        run_id: str,
        *,
        report: dict[str, Any],
        completed_run: SelfEvolveRun,
        previous_artifact_retention: Mapping[str, object] | None = None,
    ) -> Path:
        """Persist terminal state before reclaiming completed-run artifacts."""

        self.store.write_report(run_id, report)
        self.store.create_run(completed_run)
        report["artifact_retention"] = self.build_report(
            run_id,
            previous=previous_artifact_retention,
        )
        report_path = self.store.write_report(run_id, report)
        self.acknowledge_reported(run_id, report)
        return report_path

    def build_report(
        self,
        run_id: str,
        *,
        previous: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        recovered = self.recover_transactions()
        recovered_current = recovered.get(run_id)
        effective_previous = previous
        if recovered_current is not None:
            effective_previous = (
                merge_artifact_retention_reports(previous, recovered_current)
                if previous is not None
                else recovered_current
            )
        try:
            cleanup: dict[str, object] = self.cleanup(
                self.store.workspace_root,
                artifact_root=self.store.artifact_root,
                current_run_id=run_id,
            )
        except Exception as exc:
            current: dict[str, object] = {
                "status": "failed",
                "error": str(exc),
            }
            if effective_previous is None:
                return current
            return merge_artifact_retention_reports(effective_previous, current)
        current = {
            "status": "completed",
            **cleanup,
        }
        if effective_previous is None:
            return current
        return merge_artifact_retention_reports(effective_previous, current)

    def recover_transactions(self) -> dict[str, dict[str, object]]:
        transactions = read_self_evolve_retention_transactions(
            self.store.workspace_root,
            artifact_root=self.store.artifact_root,
        )
        recovered_by_run: dict[str, dict[str, object]] = {}
        for transaction in transactions:
            owner_run_id = transaction.get("run_id")
            transaction_id = transaction.get("transaction_id")
            recovered_result = transaction.get("result")
            if (
                not isinstance(owner_run_id, str)
                or not isinstance(transaction_id, str)
                or not isinstance(recovered_result, Mapping)
            ):
                raise ValueError("artifact retention transaction projection is invalid")
            try:
                report = self.store.read_report(owner_run_id)
            except FileNotFoundError:
                continue
            existing = report.get("artifact_retention")
            merged_retention = (
                merge_artifact_retention_reports(existing, recovered_result)
                if isinstance(existing, Mapping)
                else dict(recovered_result)
            )
            report["artifact_retention"] = merged_retention
            self.store.write_report(owner_run_id, report)
            acknowledge_self_evolve_retention_transactions(
                self.store.workspace_root,
                artifact_root=self.store.artifact_root,
                run_id=owner_run_id,
                transaction_ids=(transaction_id,),
            )
            recovered_by_run[owner_run_id] = merged_retention
        return recovered_by_run

    def acknowledge_reported(
        self,
        run_id: str,
        report: Mapping[str, object],
    ) -> None:
        retention = report.get("artifact_retention")
        if not isinstance(retention, Mapping):
            return
        transaction_ids = tuple(
            value
            for value in _retention_sequence(retention.get("transaction_ids"))
            if isinstance(value, str) and value
        )
        if not transaction_ids:
            return
        acknowledge_self_evolve_retention_transactions(
            self.store.workspace_root,
            artifact_root=self.store.artifact_root,
            run_id=run_id,
            transaction_ids=transaction_ids,
        )


def _retention_controller(
    store: FilesystemSelfEvolveStore,
    *,
    cleanup: Callable[..., dict[str, object]] = cleanup_self_evolve_artifacts,
) -> ArtifactRetentionController:
    """Build retention with an explicit legacy cleanup injection seam."""

    return ArtifactRetentionController(store=store, cleanup=cleanup)


def _artifact_retention_report(
    store: FilesystemSelfEvolveStore,
    run_id: str,
    *,
    previous: Mapping[str, object] | None = None,
    cleanup: Callable[..., dict[str, object]] = cleanup_self_evolve_artifacts,
) -> dict[str, object]:
    return _retention_controller(store, cleanup=cleanup).build_report(
        run_id,
        previous=previous,
    )


def _finalize_run_report(
    store: FilesystemSelfEvolveStore,
    run_id: str,
    *,
    report: dict[str, Any],
    completed_run: SelfEvolveRun,
    previous_artifact_retention: Mapping[str, object] | None = None,
    cleanup: Callable[..., dict[str, object]] = cleanup_self_evolve_artifacts,
) -> Path:
    return _retention_controller(store, cleanup=cleanup).finalize_run_report(
        run_id,
        report=report,
        completed_run=completed_run,
        previous_artifact_retention=previous_artifact_retention,
    )


def finalize_run_report(
    store: FilesystemSelfEvolveStore,
    run_id: str,
    *,
    report: dict[str, Any],
    completed_run: SelfEvolveRun,
    previous_artifact_retention: Mapping[str, object] | None = None,
) -> Path:
    """Compatibility function for callers migrating to the controller."""

    return _finalize_run_report(
        store,
        run_id,
        report=report,
        completed_run=completed_run,
        previous_artifact_retention=previous_artifact_retention,
    )


def artifact_retention_report(
    store: FilesystemSelfEvolveStore,
    run_id: str,
    *,
    previous: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Compatibility function for callers migrating to the controller."""

    return _artifact_retention_report(
        store,
        run_id,
        previous=previous,
    )


def recover_artifact_retention_transactions(
    store: FilesystemSelfEvolveStore,
) -> dict[str, dict[str, object]]:
    """Compatibility function for callers migrating to the controller."""

    return ArtifactRetentionController(store).recover_transactions()


def acknowledge_reported_artifact_retention(
    store: FilesystemSelfEvolveStore,
    run_id: str,
    report: Mapping[str, object],
) -> None:
    """Compatibility function for callers migrating to the controller."""

    ArtifactRetentionController(store).acknowledge_reported(run_id, report)


def merge_artifact_retention_reports(
    previous: Mapping[str, object],
    current: Mapping[str, object],
) -> dict[str, object]:
    compacted_run_ids = sorted(
        {
            str(value)
            for report in (previous, current)
            for value in _retention_compacted_run_ids(report)
            if isinstance(value, str) and value
        }
    )
    removed_run_ids = sorted(
        {
            str(value)
            for report in (previous, current)
            for value in _retention_sequence(report.get("removed_run_ids"))
            if report.get("schema_version")
            == "aworld.self_evolve.artifact_retention.v2"
            if isinstance(value, str) and value
        }
    )
    removed_paths = list(
        dict.fromkeys(
            str(value)
            for report in (previous, current)
            for value in _retention_sequence(report.get("removed_paths"))
            if isinstance(value, str) and value
        )
    )
    final_state = current if current.get("status") == "completed" else previous
    skipped_runs = [
        value
        for value in _retention_sequence(final_state.get("skipped_runs"))
        if isinstance(value, Mapping)
    ]
    protected_run_ids = sorted(
        {
            str(value)
            for value in _retention_sequence(final_state.get("protected_run_ids"))
            if isinstance(value, str) and value
        }
    )
    archived_run_ids = sorted(
        {
            str(value)
            for report in (previous, current)
            for value in _retention_sequence(report.get("archived_run_ids"))
            if isinstance(value, str) and value
        }
    )
    removed_ingestion_ids = sorted(
        {
            str(value)
            for report in (previous, current)
            for value in _retention_sequence(report.get("removed_ingestion_ids"))
            if isinstance(value, str) and value
        }
    )
    protected_ingestion_ids = sorted(
        {
            str(value)
            for value in _retention_sequence(
                final_state.get("protected_ingestion_ids")
            )
            if isinstance(value, str) and value
        }
    )
    transaction_ids = sorted(
        {
            str(value)
            for report in (previous, current)
            for value in _retention_sequence(report.get("transaction_ids"))
            if isinstance(value, str) and value
        }
    )
    uncertain_removed_paths = sorted(
        {
            str(value)
            for report in (previous, current)
            for value in _retention_sequence(report.get("uncertain_removed_paths"))
            if isinstance(value, str) and value
        }
    )
    statuses = tuple(report.get("status") for report in (previous, current))
    merged: dict[str, object] = {
        "schema_version": "aworld.self_evolve.artifact_retention.v2",
        "status": (
            "completed" if statuses == ("completed", "completed") else "failed"
        ),
        "policy": current.get("policy", previous.get("policy", {})),
        "removed_run_count": len(removed_run_ids),
        "removed_run_ids": removed_run_ids,
        "compacted_run_count": len(compacted_run_ids),
        "compacted_run_ids": compacted_run_ids,
        "archived_run_ids": archived_run_ids,
        "removed_path_count": len(removed_paths),
        "removed_paths": removed_paths,
        "skipped_runs": skipped_runs,
        "protected_run_ids": protected_run_ids,
        "removed_ingestion_ids": removed_ingestion_ids,
        "protected_ingestion_ids": protected_ingestion_ids,
        "transaction_ids": transaction_ids,
    }
    if uncertain_removed_paths:
        merged["uncertain_removed_paths"] = uncertain_removed_paths
    errors = [
        report.get("error")
        for report in (previous, current)
        if isinstance(report.get("error"), str)
    ]
    if errors:
        merged["errors"] = errors
    return merged


def _retention_compacted_run_ids(
    report: Mapping[str, object],
) -> tuple[object, ...]:
    canonical = _retention_sequence(report.get("compacted_run_ids"))
    if canonical:
        return canonical
    if report.get("schema_version") != "aworld.self_evolve.artifact_retention.v2":
        # v1 called runs "removed" when only raw paths beneath their durable
        # run directories were compacted. Preserve that history under v2.
        return _retention_sequence(report.get("removed_run_ids"))
    return ()


def _retention_sequence(value: object) -> tuple[object, ...]:
    if not isinstance(value, (list, tuple, set)):
        return ()
    return tuple(value)
