# coding: utf-8
from __future__ import annotations

from typing import Any


EVALUATOR_REPORT_FORMAT_ID = "aworld.evaluator.report"
EVALUATOR_REPORT_FORMAT_VERSION = 1


class CaseEvaluationReport(dict):
    def __init__(
        self,
        *,
        case_id: str,
        input: dict[str, Any],
        metrics: dict[str, Any],
        judge: dict[str, Any],
        judge_backend: dict[str, Any] | None = None,
        state_summary: dict[str, Any] | None = None,
        artifacts: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        metric_details: dict[str, Any] | None = None,
    ) -> None:
        payload = {
            "case_id": case_id,
            "input": input,
            "metrics": metrics,
            "judge": judge,
            "judge_backend": judge_backend,
            "state_summary": state_summary or {},
        }
        if artifacts:
            payload["artifacts"] = artifacts
        if metadata:
            payload["metadata"] = metadata
        if metric_details:
            payload["metric_details"] = metric_details
        super().__init__(payload)

    def to_dict(self) -> dict[str, Any]:
        return dict(self)


class EvaluatorReport(dict):
    def to_dict(self) -> dict[str, Any]:
        payload = dict(self)
        results = payload.get("results") or []
        payload["results"] = [item.to_dict() if hasattr(item, "to_dict") else dict(item) for item in results]
        return payload


def get_evaluator_report_schema() -> dict[str, object]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://schemas.aworld.dev/evaluator/report/v{EVALUATOR_REPORT_FORMAT_VERSION}.json",
        "title": "AWorld Evaluator Report",
        "type": "object",
        "$defs": {
            "evalStatus": {
                "type": "string",
                "enum": ["PASSED", "FAILED", "NOT_EVALUATED"],
            },
            "metricScalar": {
                "oneOf": [
                    {"type": "number"},
                    {"type": "boolean"},
                    {"type": "string"},
                ]
            },
            "metricAggregate": {
                "type": "object",
                "properties": {
                    "mean": {"type": "number"},
                    "min": {"type": "number"},
                    "max": {"type": "number"},
                    "std": {"type": "number"},
                    "true_count": {"type": "integer", "minimum": 0},
                    "true_rate": {"type": "number", "minimum": 0, "maximum": 1},
                    "value": {"$ref": "#/$defs/metricScalar"},
                    "eval_status": {"$ref": "#/$defs/evalStatus"},
                },
                "additionalProperties": {
                    "oneOf": [
                        {"type": "number"},
                        {"type": "boolean"},
                        {"type": "string"},
                        {"$ref": "#/$defs/metricAggregate"},
                    ]
                },
            },
            "caseMetric": {
                "type": "object",
                "properties": {
                    "value": {"$ref": "#/$defs/metricScalar"},
                    "status": {"$ref": "#/$defs/evalStatus"},
                },
                "required": ["value"],
                "additionalProperties": False,
            },
            "gateDecision": {
                "type": "object",
                "required": ["status", "metric_name", "value"],
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["pass", "fail", "needs_approval"],
                    },
                    "metric_name": {"type": ["string", "null"]},
                    "value": {"type": ["number", "string", "boolean", "null"]},
                    "matched_conditions": {"type": "array"},
                    "failed_conditions": {"type": "array"},
                },
                "additionalProperties": False,
            },
            "automationSummary": {
                "type": "object",
                "required": [
                    "gate_status",
                    "metric_name",
                    "metric_value",
                    "approval_required",
                    "approval_resolved",
                    "approved",
                    "suggested_exit_code",
                    "case_count",
                    "judge_backend",
                ],
                "properties": {
                    "gate_status": {
                        "type": ["string", "null"],
                        "enum": ["pass", "fail", "needs_approval", None],
                    },
                    "metric_name": {"type": ["string", "null"]},
                    "metric_value": {"type": ["number", "string", "boolean", "null"]},
                    "approval_required": {"type": "boolean"},
                    "approval_resolved": {"type": "boolean"},
                    "approved": {"type": ["boolean", "null"]},
                    "suggested_exit_code": {"type": "integer", "enum": [0, 2, 3]},
                    "case_count": {"type": "integer", "minimum": 0},
                    "judge_backend": {"type": ["string", "null"]},
                },
                "additionalProperties": False,
            },
        },
        "required": [
            "report_version",
            "report_format",
            "generated_at",
            "suite_id",
            "target",
            "summary",
            "metrics",
            "results",
            "result_counts",
            "approval",
        ],
        "properties": {
            "report_version": {"type": "integer", "const": EVALUATOR_REPORT_FORMAT_VERSION},
            "report_format": {
                "type": "object",
                "required": ["id", "version"],
                "properties": {
                    "id": {"type": "string", "const": EVALUATOR_REPORT_FORMAT_ID},
                    "version": {"type": "integer", "const": EVALUATOR_REPORT_FORMAT_VERSION},
                },
                "additionalProperties": False,
            },
            "generated_at": {"type": "string", "format": "date-time"},
            "suite_id": {"type": "string"},
            "target": {"type": "object"},
            "summary": {"type": "object"},
            "metrics": {
                "type": "object",
                "additionalProperties": {"$ref": "#/$defs/metricAggregate"},
            },
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["case_id", "input", "metrics", "judge"],
                    "properties": {
                        "case_id": {"type": "string"},
                        "input": {"type": "object"},
                        "metrics": {
                            "type": "object",
                            "additionalProperties": {"$ref": "#/$defs/caseMetric"},
                        },
                        "judge": {"type": "object"},
                        "judge_backend": {
                            "type": ["object", "null"],
                            "properties": {"backend_id": {"type": "string"}},
                            "required": ["backend_id"],
                            "additionalProperties": False,
                        },
                        "state_summary": {"type": "object"},
                    },
                    "additionalProperties": True,
                },
            },
            "result_counts": {
                "type": "object",
                "required": ["cases_total", "cases_with_metrics", "cases_with_judge"],
                "properties": {
                    "cases_total": {"type": "integer", "minimum": 0},
                    "cases_with_metrics": {"type": "integer", "minimum": 0},
                    "cases_with_judge": {"type": "integer", "minimum": 0},
                },
                "additionalProperties": False,
            },
            "gate": {"$ref": "#/$defs/gateDecision"},
            "approval": {"type": "object"},
            "judge_backend": {"type": "object"},
            "suite_selection": {"type": "object"},
            "automation": {"$ref": "#/$defs/automationSummary"},
            "report_path": {"type": "string"},
            "judge_schema": {"type": "object"},
        },
        "additionalProperties": True,
    }


def validate_evaluator_report(report: dict[str, Any]) -> None:
    import jsonschema

    try:
        jsonschema.validate(instance=report, schema=get_evaluator_report_schema())
    except jsonschema.ValidationError as exc:
        path = ".".join(str(part) for part in exc.absolute_path)
        location = f" at '{path}'" if path else ""
        raise ValueError(f"evaluator report validation failed{location}: {exc.message}") from exc
