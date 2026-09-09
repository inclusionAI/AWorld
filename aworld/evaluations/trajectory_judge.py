# coding: utf-8
from __future__ import annotations

from typing import Any, Literal, Mapping

from pydantic import BaseModel, Field

from aworld.evaluations.substrate import JudgeSchemaDef


class EvidenceRepairConstraintOutput(BaseModel):
    """Payload-free evidence diagnosis emitted once per distinct issue type."""

    subject_kind: Literal[
        "artifact",
        "bibliographic_claim",
        "configuration_claim",
        "evidence_manifest",
        "general_claim",
        "quantitative_claim",
        "quote",
        "symbolic_claim",
    ]
    failure_mode: Literal[
        "invalid_manifest",
        "missing_source",
        "projection_compacted",
        "source_mismatch",
        "support_incomplete",
        "unreadable_artifact",
        "unsupported_claim",
    ]
    source_layer: Literal[
        "artifact_capture",
        "artifact_projection",
        "candidate_output",
        "evidence_manifest",
        "judge_runtime",
    ]
    required_action: Literal[
        "capture_artifact",
        "expand_bounded_projection",
        "reconcile_source",
        "repair_artifact_reference",
        "support_or_omit",
        "validate_manifest",
    ]
    owner: Literal["candidate", "framework", "infrastructure", "task"]
    occurrence_count: int = Field(default=1, ge=1)


class TrajectoryEvalJudgeOutput(BaseModel):
    score: float
    verdict: Literal["Excellent", "Pass", "Marginal", "Fail"]
    A1_groundedness: int
    A2_completeness: int
    A3_relevance: int
    A4_readability: int
    B1_tool_use: int
    B2_efficiency: int
    B3_compliance: int
    B4_robustness: int
    veto_triggered: bool = False
    has_evidence: bool = False
    evidence_block_count: int = 0
    evidence_compacted: bool = False
    evidence_incomplete: bool = False
    evidence_quality: dict[str, Any] = Field(default_factory=dict)
    evidence_repair_constraints: list[EvidenceRepairConstraintOutput] = Field(
        default_factory=list
    )


def normalize_trajectory_judge_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    if "dimensions" not in payload:
        flattened = dict(payload)
    else:
        flattened = dict(payload)
        if "score" not in flattened and "weighted_score" in flattened:
            flattened["score"] = flattened["weighted_score"]
        dimensions = payload.get("dimensions") or {}
        for metric_name in (
            "A1_groundedness",
            "A2_completeness",
            "A3_relevance",
            "A4_readability",
            "B1_tool_use",
            "B2_efficiency",
            "B3_compliance",
            "B4_robustness",
        ):
            metric_payload = dimensions.get(metric_name) if isinstance(dimensions, Mapping) else None
            if isinstance(metric_payload, Mapping) and "score" in metric_payload:
                flattened[metric_name] = metric_payload["score"]

    evidence_quality = flattened.get("evidence_quality")
    if isinstance(evidence_quality, Mapping):
        for metric_name in (
            "has_evidence",
            "evidence_block_count",
            "evidence_compacted",
            "evidence_incomplete",
            "evidence_repair_constraints",
        ):
            if metric_name not in flattened and metric_name in evidence_quality:
                flattened[metric_name] = evidence_quality[metric_name]
    return flattened


class TrajectoryJudgeSchema:
    @staticmethod
    def default() -> JudgeSchemaDef:
        return JudgeSchemaDef(
            output_model=TrajectoryEvalJudgeOutput,
            normalizer=normalize_trajectory_judge_payload,
        )
