from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from typing import Any, Mapping


class SelfEvolveRunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"


@dataclass(frozen=True)
class SelfEvolveTargetRef:
    target_type: str
    target_id: str
    path: str | None = None


@dataclass(frozen=True)
class EvaluationSummary:
    variant_id: str
    metrics: Mapping[str, Any]
    dataset_split: str | None = None


@dataclass(frozen=True)
class GateResult:
    gate_name: str
    passed: bool
    reason: str
    details: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class CandidateVariant:
    candidate_id: str
    target: SelfEvolveTargetRef
    content: str
    rationale: str
    parent_candidate_ids: tuple[str, ...] = ()
    target_fingerprint: str | None = None


@dataclass(frozen=True)
class OptimizerLineage:
    candidate_id: str
    optimizer_name: str
    optimizer_version: str
    parent_candidate_ids: tuple[str, ...] = ()
    trainable_case_ids: tuple[str, ...] = ()
    rationale: str | None = None


@dataclass(frozen=True)
class DatasetRecipe:
    source: Mapping[str, Any]
    split_seed: str
    splits: Mapping[str, list[str]]
    synthetic_generation_policy: str = "disabled"
    trainable_case_ids: tuple[str, ...] = ()
    held_out_case_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SelfEvolveRun:
    run_id: str
    target: SelfEvolveTargetRef
    status: SelfEvolveRunStatus = SelfEvolveRunStatus.PENDING
    selected_candidate_id: str | None = None
    metrics: tuple[EvaluationSummary, ...] = ()
    gate_results: tuple[GateResult, ...] = ()


def to_json_dict(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {
            field.name: to_json_dict(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): to_json_dict(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [to_json_dict(item) for item in value]
    if isinstance(value, list):
        return [to_json_dict(item) for item in value]
    return value
