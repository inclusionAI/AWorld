# coding: utf-8
from __future__ import annotations

from typing import Any


def get_declared_eval_suite_schema() -> dict[str, object]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://schemas.aworld.dev/evaluator/declared-suite/v1.json",
        "title": "AWorld Declared Evaluator Suite",
        "type": "object",
        "required": ["suite_id", "base_suite"],
        "properties": {
            "suite_id": {
                "type": "string",
                "minLength": 1,
                "description": "Unique suite identifier exposed through aworld-cli evaluator.",
            },
            "base_suite": {
                "type": "string",
                "const": "app-evaluator",
                "description": "Builtin evaluator suite used as the declaration base.",
            },
            "target_kinds": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["file", "directory", "image"],
                },
                "minItems": 1,
                "uniqueItems": True,
                "description": "Optional target kinds matched by this declared suite.",
            },
            "gate_policy": {
                "type": "object",
                "properties": {
                    "metric_name": {"type": "string"},
                    "pass_threshold": {"type": "number"},
                    "approval_threshold": {"type": ["number", "null"]},
                },
                "additionalProperties": False,
                "description": "Optional simple gate override layered on top of the base suite defaults.",
            },
            "metadata": {
                "type": "object",
                "description": "Optional suite metadata copied into the resolved suite definition.",
            },
            "priority": {
                "type": "integer",
                "description": "Optional suite selection priority. Larger values win automatic selection.",
            },
        },
        "additionalProperties": False,
        "description": "Declared evaluator suites are metadata-only overlays; executable refs and runtime handles are not accepted.",
    }


def validate_declared_eval_suite_manifest(payload: dict[str, Any]) -> None:
    import jsonschema

    try:
        jsonschema.validate(instance=payload, schema=get_declared_eval_suite_schema())
    except jsonschema.ValidationError as exc:
        path = ".".join(str(part) for part in exc.absolute_path)
        location = f" at '{path}'" if path else ""
        raise ValueError(f"declared evaluator suite validation failed{location}: {exc.message}") from exc
