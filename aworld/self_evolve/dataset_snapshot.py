from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping

from aworld.self_evolve.datasets import EvalCase, SelfEvolveDataset
from aworld.self_evolve.trace_pack import TraceEvidenceStep, TracePack
from aworld.self_evolve.trajectory_context import (
    TRAJECTORY_CONTEXT_SCHEMA_VERSION,
    TrajectoryContextSnapshot,
    TrajectoryContextTurn,
)
from aworld.self_evolve.types import DatasetRecipe, to_json_dict


CAMPAIGN_DATASET_SNAPSHOT_SCHEMA_VERSION = (
    "aworld.self_evolve.campaign_dataset_snapshot.v1"
)
CAMPAIGN_DATASET_SNAPSHOT_SOURCE_KINDS = frozenset(
    {
        "batch_config",
        "jsonl",
        "session",
        "trajectory_log",
        "trajectory_set",
    }
)
_MANIFEST_NAME = "manifest.json"
_CASES_NAME = "cases.jsonl"


def campaign_dataset_snapshot_supported(source_kind: str) -> bool:
    return source_kind in CAMPAIGN_DATASET_SNAPSHOT_SOURCE_KINDS


def write_campaign_dataset_snapshot(
    path: str | Path,
    dataset: SelfEvolveDataset,
    *,
    campaign_id: str,
    campaign_source_fingerprint: str,
) -> Mapping[str, Any]:
    """Atomically freeze a dataset without building a second in-memory copy."""

    snapshot_path = Path(path)
    if snapshot_path.is_symlink():
        raise ValueError("campaign dataset snapshot cannot be a symlink")
    if snapshot_path.exists():
        return load_campaign_dataset_snapshot_manifest(
            snapshot_path,
            expected_campaign_id=campaign_id,
            expected_campaign_source_fingerprint=campaign_source_fingerprint,
        )
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(
            prefix=".dataset-snapshot-",
            dir=snapshot_path.parent,
        )
    )
    try:
        cases_path = temporary / _CASES_NAME
        cases_digest = hashlib.sha256()
        cases_size_bytes = 0
        with cases_path.open("wb") as handle:
            for case in dataset.cases:
                encoded = _canonical_json_bytes(to_json_dict(case)) + b"\n"
                handle.write(encoded)
                cases_digest.update(encoded)
                cases_size_bytes += len(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        manifest_without_fingerprint: dict[str, Any] = {
            "schema_version": CAMPAIGN_DATASET_SNAPSHOT_SCHEMA_VERSION,
            "storage_layout": "jsonl_case_stream",
            "campaign_id": campaign_id,
            "campaign_source_fingerprint": campaign_source_fingerprint,
            "case_count": len(dataset.cases),
            "case_ids": [case.case_id for case in dataset.cases],
            "cases_sha256": "sha256:" + cases_digest.hexdigest(),
            "cases_size_bytes": cases_size_bytes,
            "recipe": to_json_dict(dataset.recipe),
        }
        manifest = {
            **manifest_without_fingerprint,
            "snapshot_fingerprint": _fingerprint(manifest_without_fingerprint),
        }
        manifest_path = temporary / _MANIFEST_NAME
        with manifest_path.open("wb") as handle:
            handle.write(_canonical_json_bytes(manifest) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.replace(temporary, snapshot_path)
        except OSError:
            if not snapshot_path.is_dir():
                raise
            existing = load_campaign_dataset_snapshot_manifest(
                snapshot_path,
                expected_campaign_id=campaign_id,
                expected_campaign_source_fingerprint=(
                    campaign_source_fingerprint
                ),
            )
            if existing.get("snapshot_fingerprint") != manifest.get(
                "snapshot_fingerprint"
            ):
                raise ValueError(
                    "campaign dataset snapshot was concurrently replaced"
                )
            return existing
        return manifest
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def load_campaign_dataset_snapshot(
    path: str | Path,
    *,
    expected_campaign_id: str,
    expected_campaign_source_fingerprint: str,
) -> SelfEvolveDataset:
    snapshot_path = Path(path)
    manifest = load_campaign_dataset_snapshot_manifest(
        snapshot_path,
        expected_campaign_id=expected_campaign_id,
        expected_campaign_source_fingerprint=(
            expected_campaign_source_fingerprint
        ),
    )
    cases_path = _regular_snapshot_file(snapshot_path / _CASES_NAME)
    expected_count = _non_negative_int(manifest.get("case_count"), "case_count")
    expected_size = _non_negative_int(
        manifest.get("cases_size_bytes"),
        "cases_size_bytes",
    )
    expected_digest = _sha256_value(manifest.get("cases_sha256"), "cases_sha256")
    cases: list[EvalCase] = []
    digest = hashlib.sha256()
    size_bytes = 0
    with cases_path.open("rb") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            digest.update(raw_line)
            size_bytes += len(raw_line)
            if not raw_line.strip():
                raise ValueError(
                    f"campaign dataset snapshot case line {line_number} is empty"
                )
            try:
                payload = json.loads(raw_line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(
                    f"campaign dataset snapshot case line {line_number} is invalid"
                ) from exc
            if not isinstance(payload, Mapping):
                raise ValueError("campaign dataset snapshot case must be an object")
            cases.append(_eval_case_from_dict(payload))
    actual_digest = "sha256:" + digest.hexdigest()
    if size_bytes != expected_size or actual_digest != expected_digest:
        raise ValueError("campaign dataset snapshot case stream fingerprint changed")
    if len(cases) != expected_count:
        raise ValueError("campaign dataset snapshot case count changed")
    expected_case_ids = manifest.get("case_ids")
    if not isinstance(expected_case_ids, list) or expected_case_ids != [
        case.case_id for case in cases
    ]:
        raise ValueError("campaign dataset snapshot case identities changed")
    recipe = dataset_recipe_from_dict(manifest.get("recipe"))
    return SelfEvolveDataset(cases=tuple(cases), recipe=recipe)


def load_campaign_dataset_snapshot_manifest(
    path: str | Path,
    *,
    expected_campaign_id: str,
    expected_campaign_source_fingerprint: str,
) -> Mapping[str, Any]:
    snapshot_path = Path(path)
    if not snapshot_path.is_dir() or snapshot_path.is_symlink():
        raise FileNotFoundError(
            f"campaign dataset snapshot is unavailable: {snapshot_path}"
        )
    manifest_path = _regular_snapshot_file(snapshot_path / _MANIFEST_NAME)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("campaign dataset snapshot manifest is invalid") from exc
    if not isinstance(manifest, Mapping):
        raise ValueError("campaign dataset snapshot manifest must be an object")
    if manifest.get("schema_version") != CAMPAIGN_DATASET_SNAPSHOT_SCHEMA_VERSION:
        raise ValueError("campaign dataset snapshot schema is unsupported")
    if manifest.get("storage_layout") != "jsonl_case_stream":
        raise ValueError("campaign dataset snapshot storage layout is unsupported")
    if manifest.get("campaign_id") != expected_campaign_id:
        raise ValueError("campaign dataset snapshot campaign identity changed")
    if (
        manifest.get("campaign_source_fingerprint")
        != expected_campaign_source_fingerprint
    ):
        raise ValueError("campaign dataset snapshot source fingerprint changed")
    snapshot_fingerprint = _sha256_value(
        manifest.get("snapshot_fingerprint"),
        "snapshot_fingerprint",
    )
    unsigned = {
        str(key): value
        for key, value in manifest.items()
        if key != "snapshot_fingerprint"
    }
    if _fingerprint(unsigned) != snapshot_fingerprint:
        raise ValueError("campaign dataset snapshot manifest fingerprint changed")
    return dict(manifest)


def _eval_case_from_dict(value: Mapping[str, Any]) -> EvalCase:
    case_id = value.get("case_id")
    if not isinstance(case_id, str) or not case_id:
        raise ValueError("campaign dataset snapshot case_id is required")
    metadata = _mapping(value.get("metadata"), "metadata")
    source = _mapping(value.get("source"), "source")
    signals = value.get("self_improvement_signals", ())
    if not isinstance(signals, list) or any(
        not isinstance(item, Mapping) for item in signals
    ):
        raise ValueError("campaign dataset snapshot signals are invalid")
    verification_command = value.get("verification_command")
    if verification_command is not None and not isinstance(
        verification_command, str
    ):
        raise ValueError("campaign dataset verification command is invalid")
    trace_pack_value = value.get("trace_pack")
    context_value = value.get("context_snapshot")
    return EvalCase(
        case_id=case_id,
        input=value.get("input"),
        expected_output=value.get("expected_output"),
        verification_command=verification_command,
        metadata=metadata,
        trace_pack=(
            _trace_pack_from_dict(trace_pack_value)
            if isinstance(trace_pack_value, Mapping)
            else None
        ),
        source=source,
        context_snapshot=(
            _context_snapshot_from_dict(context_value)
            if isinstance(context_value, Mapping)
            else None
        ),
        self_improvement_signals=tuple(dict(item) for item in signals),
    )


def _trace_pack_from_dict(value: Mapping[str, Any]) -> TracePack:
    raw_steps = value.get("steps")
    if not isinstance(raw_steps, list):
        raise ValueError("campaign dataset trace pack steps are invalid")
    steps: list[TraceEvidenceStep] = []
    for item in raw_steps:
        if not isinstance(item, Mapping):
            raise ValueError("campaign dataset trace step must be an object")
        tool_names = item.get("tool_names", ())
        if not isinstance(tool_names, list) or any(
            not isinstance(name, str) for name in tool_names
        ):
            raise ValueError("campaign dataset trace step tool names are invalid")
        steps.append(
            TraceEvidenceStep(
                evidence_id=_required_text(item, "evidence_id"),
                source_index=_non_negative_int(
                    item.get("source_index"),
                    "source_index",
                ),
                original_id=_optional_text(item.get("original_id")),
                state=_mapping(item.get("state"), "state"),
                action=_mapping(item.get("action"), "action"),
                reward=_mapping(item.get("reward"), "reward"),
                agent_id=_optional_text(item.get("agent_id")),
                pre_agent=_optional_text(item.get("pre_agent")),
                tool_names=tuple(tool_names),
            )
        )
    return TracePack(
        pack_id=_required_text(value, "pack_id"),
        source_kind=_required_text(value, "source_kind"),
        task_id=_required_text(value, "task_id"),
        steps=tuple(steps),
        omitted_step_count=_non_negative_int(
            value.get("omitted_step_count", 0),
            "omitted_step_count",
        ),
        compression_summary=_optional_text(value.get("compression_summary")),
    )


def _context_snapshot_from_dict(
    value: Mapping[str, Any],
) -> TrajectoryContextSnapshot:
    if value.get("schema_version") != TRAJECTORY_CONTEXT_SCHEMA_VERSION:
        raise ValueError("campaign dataset context snapshot schema is unsupported")
    raw_steps = value.get("steps")
    raw_turns = value.get("prior_turns")
    if not isinstance(raw_steps, list) or any(
        not isinstance(item, Mapping) for item in raw_steps
    ):
        raise ValueError("campaign dataset context steps are invalid")
    if not isinstance(raw_turns, list):
        raise ValueError("campaign dataset context turns are invalid")
    turns: list[TrajectoryContextTurn] = []
    for item in raw_turns:
        if not isinstance(item, Mapping):
            raise ValueError("campaign dataset context turn must be an object")
        turns.append(
            TrajectoryContextTurn(
                role=_required_text(item, "role"),
                content=_required_text(item, "content", allow_empty=True),
                source_task_id=_required_text(item, "source_task_id"),
                evidence_ref=_required_text(item, "evidence_ref"),
            )
        )
    return TrajectoryContextSnapshot(
        schema_version=TRAJECTORY_CONTEXT_SCHEMA_VERSION,
        case_id=_required_text(value, "case_id"),
        source_kind=_required_text(value, "source_kind"),
        source_record_index=_non_negative_int(
            value.get("source_record_index"),
            "source_record_index",
        ),
        source_fingerprint=_sha256_value(
            value.get("source_fingerprint"),
            "source_fingerprint",
        ),
        session_id=_optional_text(value.get("session_id")),
        task_input=value.get("task_input"),
        steps=tuple(dict(item) for item in raw_steps),
        step_count=_non_negative_int(value.get("step_count"), "step_count"),
        omitted_step_count=_non_negative_int(
            value.get("omitted_step_count"),
            "omitted_step_count",
        ),
        prior_turns=tuple(turns),
        link_strategy=_optional_text(value.get("link_strategy")),
        context_status=_required_text(value, "context_status"),
        context_reason=_optional_text(value.get("context_reason")),
        fingerprint=_sha256_value(value.get("fingerprint"), "fingerprint"),
    )


def dataset_recipe_from_dict(value: object) -> DatasetRecipe:
    if not isinstance(value, Mapping):
        raise ValueError("campaign dataset snapshot recipe is invalid")
    splits = value.get("splits")
    if not isinstance(splits, Mapping):
        raise ValueError("campaign dataset snapshot splits are invalid")
    normalized_splits: dict[str, list[str]] = {}
    for key in ("train", "validation", "held_out"):
        items = splits.get(key)
        if not isinstance(items, list) or any(
            not isinstance(item, str) for item in items
        ):
            raise ValueError("campaign dataset snapshot split members are invalid")
        normalized_splits[key] = list(items)
    trainable = value.get("trainable_case_ids", ())
    held_out = value.get("held_out_case_ids", ())
    if not isinstance(trainable, list) or not isinstance(held_out, list):
        raise ValueError("campaign dataset snapshot recipe identities are invalid")
    if any(not isinstance(item, str) for item in (*trainable, *held_out)):
        raise ValueError("campaign dataset snapshot recipe identities are invalid")
    return DatasetRecipe(
        source=_mapping(value.get("source"), "recipe source"),
        split_seed=_required_text(value, "split_seed"),
        splits=normalized_splits,
        synthetic_generation_policy=_required_text(
            value,
            "synthetic_generation_policy",
        ),
        trainable_case_ids=tuple(trainable),
        held_out_case_ids=tuple(held_out),
    )


def _regular_snapshot_file(path: Path) -> Path:
    if not path.is_file() or path.is_symlink():
        raise FileNotFoundError(f"campaign dataset snapshot file is unavailable: {path}")
    return path


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _fingerprint(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _mapping(value: object, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"campaign dataset snapshot {field_name} is invalid")
    return {str(key): item for key, item in value.items()}


def _required_text(
    value: Mapping[str, Any],
    key: str,
    *,
    allow_empty: bool = False,
) -> str:
    item = value.get(key)
    if not isinstance(item, str) or (not allow_empty and not item):
        raise ValueError(f"campaign dataset snapshot {key} is invalid")
    return item


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("campaign dataset snapshot optional text is invalid")
    return value


def _non_negative_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"campaign dataset snapshot {field_name} is invalid")
    return value


def _sha256_value(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError(f"campaign dataset snapshot {field_name} is invalid")
    return value
