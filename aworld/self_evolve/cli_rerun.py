"""Stored-run loading, validation, and identity helpers for CLI reruns."""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any, Mapping

from aworld.self_evolve.cli_ingestion import (
    _ingestion_mode,
    _validate_frozen_semantic_runtime_admission,
)
from aworld.self_evolve.history_support import _load_json_mapping
from aworld.self_evolve.credit_assignment import TargetSelectionReport
from aworld.self_evolve.datasets import (
    SelfEvolveDataset,
    SelfEvolveEvalSourceConfig,
)
from aworld.self_evolve.dataset_snapshot import (
    CAMPAIGN_DATASET_SNAPSHOT_SCHEMA_VERSION,
    dataset_recipe_from_dict,
    load_campaign_dataset_snapshot,
    load_campaign_dataset_snapshot_manifest,
)
from aworld.self_evolve.ingestion import (
    fingerprint_json as ingestion_fingerprint_json,
)
from aworld.self_evolve.ingestion.semantic_snapshot import (
    FrozenSemanticIngestionSnapshotV2,
)
from aworld.self_evolve.provenance import (
    TargetMutationIntent,
    TargetProvenanceResolution,
    TargetProvenanceStatus,
    TargetSelectionOrigin,
    load_target_provenance_payload,
)
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.run_history import (
    _load_candidate_variant as _load_candidate_variant,
    _load_structural_edit_intent as _load_structural_edit_intent,
)
from aworld.self_evolve.types import (
    SelfEvolveTargetRef,
)


def _validate_rerun_source_runtime_admission(
    source_config: SelfEvolveEvalSourceConfig,
    *,
    apply_policy: str,
) -> None:
    """Re-apply current semantic trust policy before evaluator-only reuse."""

    snapshot = source_config.ingestion_snapshot
    if isinstance(snapshot, FrozenSemanticIngestionSnapshotV2):
        _validate_frozen_semantic_runtime_admission(
            snapshot,
            mode=_ingestion_mode(
                apply_policy=apply_policy,
                ingestion_only=False,
            ),
        )


def _resolve_stored_run_path(store: FilesystemSelfEvolveStore, from_run: str) -> Path:
    raw = Path(from_run).expanduser()
    if raw.exists():
        run_path = raw
    else:
        run_path = store.run_path(from_run)
    if not run_path.exists() or not run_path.is_dir():
        raise FileNotFoundError(f"self-evolve run not found: {from_run}")
    if not (run_path / "report.json").exists():
        raise FileNotFoundError(f"self-evolve report not found under run: {run_path}")
    return run_path


def _stored_selected_candidate_id(report: Mapping[str, Any]) -> str:
    for key in ("selected_candidate_id", "best_candidate_id"):
        value = report.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    candidate_ids = report.get("candidate_ids")
    if isinstance(candidate_ids, list):
        for value in candidate_ids:
            if isinstance(value, str) and value.strip():
                return value.strip()
    raise ValueError("stored run report does not identify a selected candidate")






def _source_config_from_stored_dataset_recipe(
    path: Path,
) -> tuple[SelfEvolveEvalSourceConfig, str]:
    payload = _load_json_mapping(path)
    source = payload.get("source")
    if not isinstance(source, Mapping):
        raise ValueError(f"dataset recipe is missing source: {path}")
    kind = str(source.get("kind") or "")
    if kind == "agentic_source":
        ingestion_id = source.get("ingestion_id")
        if not isinstance(ingestion_id, str) or not ingestion_id:
            raise ValueError("stored agentic dataset is missing ingestion_id")
        artifact_root = path.parent.parent
        store = FilesystemSelfEvolveStore(
            artifact_root.parent.parent,
            artifact_root=artifact_root,
        )
        snapshot = store.read_ingestion(ingestion_id)
        split_seed = str(
            payload.get("split_seed") or "self-evolve-default-split"
        )
        return (
            SelfEvolveEvalSourceConfig(
                kind="agentic_source",
                ingestion_snapshot=snapshot,
                max_cases=len(snapshot.normalized_cases),
            ),
            split_seed,
        )
    if kind not in {"trajectory_log", "jsonl", "session", "batch_config"}:
        raise ValueError(f"stored dataset source cannot be rebuilt for rerun: {kind}")
    task_ids_payload = source.get("task_ids")
    task_ids = tuple(
        str(item)
        for item in task_ids_payload
        if isinstance(item, str)
    ) if isinstance(task_ids_payload, list) else ()
    if not task_ids:
        auto_grouping = source.get("auto_grouping")
        selected_case_ids = (
            auto_grouping.get("selected_case_ids")
            if isinstance(auto_grouping, Mapping)
            else None
        )
        if isinstance(selected_case_ids, list):
            task_ids = tuple(
                str(item) for item in selected_case_ids if isinstance(item, str)
            )
    source_config = SelfEvolveEvalSourceConfig(
        kind=kind,
        path=(str(source.get("path")) if source.get("path") is not None else None),
        session_id=(
            str(source.get("session_id"))
            if source.get("session_id") is not None
            else None
        ),
        task_ids=task_ids,
    )
    split_seed = str(payload.get("split_seed") or "self-evolve-default-split")
    return source_config, split_seed


def _load_stored_campaign_dataset(
    *,
    store: FilesystemSelfEvolveStore,
    source_run_path: Path,
) -> SelfEvolveDataset | None:
    """Restore the immutable campaign dataset used by the source run."""

    recipe_payload = _load_json_mapping(source_run_path / "dataset_recipe.json")
    source = recipe_payload.get("source")
    if not isinstance(source, Mapping):
        raise ValueError("stored dataset recipe is missing source")
    reference = source.get("campaign_dataset_snapshot")
    if not isinstance(reference, Mapping):
        return None
    if (
        reference.get("schema_version")
        != CAMPAIGN_DATASET_SNAPSHOT_SCHEMA_VERSION
    ):
        raise ValueError("stored campaign dataset snapshot schema is unsupported")
    campaign_id = reference.get("campaign_id")
    if not isinstance(campaign_id, str) or not campaign_id:
        raise ValueError("stored campaign dataset snapshot is missing campaign_id")
    campaign = store.read_campaign(campaign_id)
    if source_run_path.name not in campaign.run_ids:
        raise ValueError("stored run is not a member of its dataset campaign")
    snapshot_path = store.campaign_path(campaign_id) / "dataset_snapshot"
    manifest = load_campaign_dataset_snapshot_manifest(
        snapshot_path,
        expected_campaign_id=campaign_id,
        expected_campaign_source_fingerprint=campaign.source_fingerprint,
    )
    expected_reference = {
        "schema_version": manifest.get("schema_version"),
        "storage_layout": manifest.get("storage_layout"),
        "campaign_id": manifest.get("campaign_id"),
        "case_count": manifest.get("case_count"),
        "cases_size_bytes": manifest.get("cases_size_bytes"),
        "snapshot_fingerprint": manifest.get("snapshot_fingerprint"),
    }
    for key, expected in expected_reference.items():
        if reference.get(key) != expected:
            raise ValueError(
                "stored run campaign dataset reference does not match snapshot: "
                f"{key}"
            )
    snapshot = load_campaign_dataset_snapshot(
        snapshot_path,
        expected_campaign_id=campaign_id,
        expected_campaign_source_fingerprint=campaign.source_fingerprint,
    )
    return SelfEvolveDataset(
        cases=snapshot.cases,
        recipe=dataset_recipe_from_dict(recipe_payload),
    )


def _validate_agentic_rerun_ingestion_ref(source_run_path: Path) -> None:
    recipe_path = source_run_path / "dataset_recipe.json"
    payload = _load_json_mapping(recipe_path)
    source = payload.get("source")
    if not isinstance(source, Mapping) or source.get("kind") != "agentic_source":
        return
    artifact_root = source_run_path.parent
    source_store = FilesystemSelfEvolveStore(
        artifact_root.parent.parent,
        artifact_root=artifact_root,
    )
    reference = source_store.read_ingestion_ref(source_run_path.name)
    snapshot = source_store.read_ingestion(str(reference["ingestion_id"]))
    expected = {
        "ingestion_id": source.get("ingestion_id"),
        "source_fingerprint": source.get("source_fingerprint"),
        "mapping_fingerprint": source.get("mapping_fingerprint"),
        "normalized_dataset_fingerprint": source.get(
            "normalized_dataset_fingerprint"
        ),
        "split_fingerprint": (
            source.get("split_fingerprint")
            or ingestion_fingerprint_json(payload.get("splits", {}))
        ),
    }
    if isinstance(snapshot, FrozenSemanticIngestionSnapshotV2):
        expected.update(
            {
                "normalization_kind": source.get(
                    "normalization_kind"
                ),
                "normalization_fingerprint": source.get(
                    "normalization_fingerprint"
                ),
                "evidence_graph_logical_fingerprint": source.get(
                    "evidence_graph_logical_fingerprint"
                ),
                "evidence_graph_provenance_fingerprint": source.get(
                    "evidence_graph_provenance_fingerprint"
                ),
                "improvement_signal_set_fingerprint": source.get(
                    "improvement_signal_set_fingerprint"
                ),
                "evaluation_plan_bundle_fingerprint": source.get(
                    "evaluation_plan_bundle_fingerprint"
                ),
                "target_evidence_bundle_fingerprint": source.get(
                    "target_evidence_bundle_fingerprint"
                ),
                "manifest_origin": source.get("manifest_origin"),
            }
        )
    for field_name, expected_value in expected.items():
        if reference.get(field_name) != expected_value:
            raise ValueError(
                "agentic evaluator rerun ingestion reference does not match "
                f"dataset recipe: {field_name}"
            )
    if (
        snapshot.split_fingerprint is not None
        and snapshot.split_fingerprint != expected["split_fingerprint"]
    ):
        raise ValueError(
            "agentic evaluator rerun split fingerprint does not match frozen "
            "snapshot"
        )


def _load_target_selection_report(path: Path) -> TargetSelectionReport | None:
    if not path.exists():
        return None
    payload = _load_json_mapping(path)
    target_payload = payload.get("selected_target")
    selected_target: SelfEvolveTargetRef | None = None
    if isinstance(target_payload, Mapping):
        selected_target = SelfEvolveTargetRef(
            target_type=str(target_payload.get("target_type") or ""),
            target_id=str(target_payload.get("target_id") or ""),
            path=(
                str(target_payload.get("path"))
                if target_payload.get("path") is not None
                else None
            ),
        )
    selection_origin_payload = payload.get("selection_origin")
    try:
        selection_origin = (
            TargetSelectionOrigin(selection_origin_payload)
            if isinstance(selection_origin_payload, str)
            else None
        )
    except ValueError:
        selection_origin = None
    target_intent_payload = payload.get("target_intent")
    invalid_target_intent = False
    try:
        target_intent = (
            TargetMutationIntent(target_intent_payload)
            if isinstance(target_intent_payload, str)
            else None
        )
    except ValueError:
        target_intent = None
        invalid_target_intent = True
    diagnostics = (
        dict(payload.get("diagnostics"))
        if isinstance(payload.get("diagnostics"), Mapping)
        else {}
    )
    if invalid_target_intent:
        diagnostics["invalid_target_intent"] = target_intent_payload
    return TargetSelectionReport(
        selected_target=selected_target,
        confidence=float(payload.get("confidence") or 0.0),
        evidence_step_ids=tuple(
            str(item)
            for item in payload.get("evidence_step_ids", ())
            if isinstance(item, str)
        ),
        failure_category=str(payload.get("failure_category") or "unknown"),
        signals=tuple(
            str(item)
            for item in payload.get("signals", ())
            if isinstance(item, str)
        ),
        no_target_reason=(
            str(payload.get("no_target_reason"))
            if payload.get("no_target_reason") is not None
            else None
        ),
        diagnostics=diagnostics or None,
        provenance_status=(
            str(payload.get("provenance_status"))
            if payload.get("provenance_status") is not None
            else None
        ),
        provenance_reason=(
            str(payload.get("provenance_reason"))
            if payload.get("provenance_reason") is not None
            else None
        ),
        selection_origin=selection_origin,
        target_intent=target_intent,
        capability_fingerprint=(
            str(payload.get("capability_fingerprint"))
            if payload.get("capability_fingerprint") is not None
            else None
        ),
    )


def _load_target_provenance(path: Path) -> TargetProvenanceResolution:
    if not path.exists():
        return TargetProvenanceResolution(
            status=TargetProvenanceStatus.UNRESOLVED,
            provenance=None,
            reason="target provenance sidecar is missing",
        )
    try:
        payload = _load_json_mapping(path)
    except ValueError as exc:
        return TargetProvenanceResolution(
            status=TargetProvenanceStatus.UNRESOLVED,
            provenance=None,
            reason=f"target provenance sidecar is unreadable: {exc}",
        )
    return load_target_provenance_payload(payload)


def _rerun_cli_run_id(source_run_id: str, candidate_id: str) -> str:
    lineage = hashlib.sha256(
        f"{source_run_id}\0{candidate_id}\0evaluator".encode("utf-8")
    ).hexdigest()[:12]
    return f"cli-rerun-{lineage}-{uuid.uuid4().hex[:8]}"
