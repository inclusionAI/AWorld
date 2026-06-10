# coding: utf-8
from __future__ import annotations

from typing import Any, Literal, Mapping

from pydantic import BaseModel

from aworld.evaluations.substrate import JudgeSchemaDef


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


def normalize_trajectory_judge_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    if "dimensions" not in payload:
        return dict(payload)
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
    return flattened


class TrajectoryJudgeSchema:
    @staticmethod
    def default() -> JudgeSchemaDef:
        return JudgeSchemaDef(
            output_model=TrajectoryEvalJudgeOutput,
            normalizer=normalize_trajectory_judge_payload,
        )
