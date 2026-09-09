from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping

from aworld.self_evolve.failure_events import FailureOwner


EVIDENCE_REPAIR_CONSTRAINT_SCHEMA_VERSION = (
    "aworld.self_evolve.evidence_repair_constraint.v1"
)

_TOKEN = re.compile(r"^[a-z][a-z0-9_]{0,95}$")
_SUBJECT_KINDS = frozenset(
    {
        "artifact",
        "bibliographic_claim",
        "configuration_claim",
        "evidence_manifest",
        "general_claim",
        "quantitative_claim",
        "quote",
        "symbolic_claim",
    }
)
_FAILURE_MODES = frozenset(
    {
        "invalid_manifest",
        "missing_source",
        "projection_compacted",
        "source_mismatch",
        "support_incomplete",
        "unreadable_artifact",
        "unsupported_claim",
    }
)
_SOURCE_LAYERS = frozenset(
    {
        "artifact_capture",
        "artifact_projection",
        "candidate_output",
        "evidence_manifest",
        "judge_runtime",
    }
)
_REQUIRED_ACTIONS = frozenset(
    {
        "capture_artifact",
        "expand_bounded_projection",
        "reconcile_source",
        "repair_artifact_reference",
        "support_or_omit",
        "validate_manifest",
    }
)


@dataclass(frozen=True)
class EvidenceRepairConstraint:
    """Payload-free evidence repair identity shared by judge and optimizer.

    The identity deliberately excludes occurrence counts and prose. This lets
    one constraint aggregate across repetitions and trajectory members without
    turning task text, claim text, or evaluator wording into policy input.
    """

    subject_kind: str
    failure_mode: str
    source_layer: str
    required_action: str
    owner: FailureOwner
    occurrence_count: int = 1
    schema_version: str = EVIDENCE_REPAIR_CONSTRAINT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != EVIDENCE_REPAIR_CONSTRAINT_SCHEMA_VERSION:
            raise ValueError("unsupported evidence repair constraint schema")
        for field_name, value, supported in (
            ("subject_kind", self.subject_kind, _SUBJECT_KINDS),
            ("failure_mode", self.failure_mode, _FAILURE_MODES),
            ("source_layer", self.source_layer, _SOURCE_LAYERS),
            ("required_action", self.required_action, _REQUIRED_ACTIONS),
        ):
            if _TOKEN.fullmatch(value) is None or value not in supported:
                raise ValueError(
                    f"unsupported evidence repair constraint {field_name}"
                )
        object.__setattr__(self, "owner", FailureOwner(self.owner))
        if (
            isinstance(self.occurrence_count, bool)
            or not isinstance(self.occurrence_count, int)
            or self.occurrence_count <= 0
        ):
            raise ValueError(
                "evidence repair constraint occurrence_count must be positive"
            )

    @property
    def identity_digest(self) -> str:
        encoded = json.dumps(
            self.identity_payload(),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def identity_payload(self) -> dict[str, str]:
        return {
            "schema_version": self.schema_version,
            "subject_kind": self.subject_kind,
            "failure_mode": self.failure_mode,
            "source_layer": self.source_layer,
            "required_action": self.required_action,
            "owner": self.owner.value,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self.identity_payload(),
            "constraint_identity_digest": self.identity_digest,
            "occurrence_count": self.occurrence_count,
        }

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, object],
    ) -> "EvidenceRepairConstraint":
        constraint = cls(
            schema_version=str(
                value.get("schema_version")
                or EVIDENCE_REPAIR_CONSTRAINT_SCHEMA_VERSION
            ),
            subject_kind=str(value.get("subject_kind") or ""),
            failure_mode=str(value.get("failure_mode") or ""),
            source_layer=str(value.get("source_layer") or ""),
            required_action=str(value.get("required_action") or ""),
            owner=FailureOwner(str(value.get("owner") or "")),
            occurrence_count=_positive_count(value.get("occurrence_count", 1)),
        )
        serialized_identity = value.get("constraint_identity_digest")
        if (
            serialized_identity is not None
            and str(serialized_identity) != constraint.identity_digest
        ):
            raise ValueError(
                "evidence repair constraint identity digest does not match"
            )
        return constraint


def evidence_repair_constraints_from_metrics(
    metrics: Mapping[str, Any],
) -> tuple[EvidenceRepairConstraint, ...]:
    """Return typed evidence constraints, with conservative legacy fallback.

    Structured constraints always win. Legacy booleans are converted without
    inspecting evaluator prose. When a valid canonical bundle was presented
    but the judge only received a compacted projection, ownership stays at the
    framework boundary until a complete projection can distinguish an
    unsupported candidate claim from missing judge context.
    """

    structured = _structured_constraints(
        metrics.get("evidence_repair_constraints")
    )
    if not structured:
        structured = _structured_constraints(metrics.get("evidence_constraints"))
    runtime_constraints: list[EvidenceRepairConstraint] = []
    if (
        _metric_bool(metrics.get("judge_artifact_projection_incomplete")) is True
        and _metric_bool(metrics.get("evidence_incomplete")) is True
    ):
        runtime_constraints.append(
            EvidenceRepairConstraint(
                subject_kind="artifact",
                failure_mode="projection_compacted",
                source_layer="artifact_projection",
                required_action="expand_bounded_projection",
                owner=FailureOwner.FRAMEWORK,
                occurrence_count=max(
                    1,
                    _non_negative_count(
                        metrics.get(
                            "judge_artifact_projection_incomplete_count"
                        )
                    ),
                ),
            )
        )
    if structured:
        return merge_evidence_repair_constraints(
            structured,
            runtime_constraints,
        )

    constraints: list[EvidenceRepairConstraint] = list(runtime_constraints)
    invalid_manifest_count = _non_negative_count(
        metrics.get("evidence_manifest_invalid_entry_count")
    )
    if invalid_manifest_count:
        constraints.append(
            EvidenceRepairConstraint(
                subject_kind="evidence_manifest",
                failure_mode="invalid_manifest",
                source_layer="evidence_manifest",
                required_action="validate_manifest",
                owner=FailureOwner.CANDIDATE,
                occurrence_count=invalid_manifest_count,
            )
        )

    has_evidence = _metric_bool(metrics.get("has_evidence"))
    evidence_block_count = _non_negative_count(
        metrics.get("evidence_block_count")
    )
    if has_evidence is False or (
        has_evidence is None
        and evidence_block_count == 0
        and metrics.get("evidence_block_count") is not None
    ):
        constraints.append(
            EvidenceRepairConstraint(
                subject_kind="artifact",
                failure_mode="missing_source",
                source_layer="artifact_capture",
                required_action="capture_artifact",
                owner=FailureOwner.CANDIDATE,
            )
        )

    compacted = _metric_bool(metrics.get("evidence_compacted")) is True
    incomplete = _metric_bool(metrics.get("evidence_incomplete")) is True
    bundle_valid = _metric_bool(metrics.get("evidence_bundle_valid")) is True
    if compacted and (incomplete or not bundle_valid):
        framework_projection = bundle_valid
        constraints.append(
            EvidenceRepairConstraint(
                subject_kind="artifact",
                failure_mode="projection_compacted",
                source_layer=(
                    "artifact_projection"
                    if framework_projection
                    else "artifact_capture"
                ),
                required_action=(
                    "expand_bounded_projection"
                    if framework_projection
                    else "capture_artifact"
                ),
                owner=(
                    FailureOwner.FRAMEWORK
                    if framework_projection
                    else FailureOwner.CANDIDATE
                ),
            )
        )
    if incomplete and not compacted:
        constraints.append(
            EvidenceRepairConstraint(
                subject_kind="general_claim",
                failure_mode="support_incomplete",
                source_layer="candidate_output",
                required_action="support_or_omit",
                owner=FailureOwner.CANDIDATE,
            )
        )
    return merge_evidence_repair_constraints(constraints)


def merge_evidence_repair_constraints(
    *constraint_groups: Iterable[EvidenceRepairConstraint],
) -> tuple[EvidenceRepairConstraint, ...]:
    """Merge multi-trajectory constraints by typed identity."""

    merged: dict[str, EvidenceRepairConstraint] = {}
    for group in constraint_groups:
        for constraint in group:
            previous = merged.get(constraint.identity_digest)
            merged[constraint.identity_digest] = (
                constraint
                if previous is None
                else replace(
                    previous,
                    occurrence_count=(
                        previous.occurrence_count + constraint.occurrence_count
                    ),
                )
            )
    return tuple(merged[key] for key in sorted(merged))


def public_evidence_constraint_payload(
    metrics: Mapping[str, Any],
) -> list[dict[str, object]]:
    return [
        constraint.to_dict()
        for constraint in evidence_repair_constraints_from_metrics(metrics)
    ]


def _structured_constraints(value: Any) -> tuple[EvidenceRepairConstraint, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    constraints: list[EvidenceRepairConstraint] = []
    for item in value[:128]:
        if not isinstance(item, Mapping):
            continue
        try:
            constraints.append(EvidenceRepairConstraint.from_dict(item))
        except (TypeError, ValueError):
            continue
    return tuple(constraints)


def _metric_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "yes", "pass", "passed"}:
            return True
        if normalized in {"0", "false", "no", "fail", "failed"}:
            return False
    return None


def _non_negative_count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


def _positive_count(value: Any) -> int:
    count = _non_negative_count(value)
    if count <= 0:
        raise ValueError("constraint occurrence_count must be positive")
    return count
