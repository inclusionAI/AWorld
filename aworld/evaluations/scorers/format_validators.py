# coding: utf-8
# Copyright (c) inclusionAI.
import json
import re
from typing import Any, Dict

from aworld.config import EvaluationConfig
from aworld.evaluations.base import EvalDataCase, ScorerResult, MetricResult
from aworld.evaluations.scorers import scorer_register
from aworld.evaluations.scorers.base_validator import RuleScorer
from aworld.evaluations.types import MetricNames


@scorer_register(MetricNames.FORMAT_CORRECTNESS)
class FormatValidationScorer(RuleScorer):
    def __init__(self, eval_config: EvaluationConfig = None):
        super().__init__(name=MetricNames.FORMAT_CORRECTNESS, eval_config=eval_config)

    async def score(
            self,
            index: int,
            input: EvalDataCase[dict],
            output: dict
    ) -> ScorerResult:
        format_type = input.case_data.get("format_type", "json")
        content = self._extract(output)

        is_valid, error_msg, details = self._validate_format(content, format_type)

        score = 1.0 if is_valid else 0.0
        metric_result: MetricResult = {
            "value": score,
            "metadata": {
                "format_type": format_type,
                "error": error_msg,
                "details": details
            }
        }

        return ScorerResult(
            scorer_name=self.name,
            metric_results={"format_correctness": metric_result}
        )

    def _validate_format(self, content: str, format_type: str) -> tuple:
        validators = {
            "json": self._validate_json,
            "xml": self._validate_xml,
            "yaml": self._validate_yaml,
            "markdown": self._validate_markdown,
            "html": self._validate_html,
            "csv": self._validate_csv,
        }

        validator = validators.get(format_type.lower(), self._validate_generic)
        return validator(content)

    def _validate_json(self, content: str) -> tuple:
        try:
            data = json.loads(content)
            return True, None, {"parsed": True, "type": type(data).__name__}
        except json.JSONDecodeError as e:
            return False, f"JSON error at line {e.lineno}: {e.msg}", {"error_pos": e.pos}

    def _validate_xml(self, content: str) -> tuple:
        try:
            import xml.etree.ElementTree as ET
            ET.fromstring(content)
            return True, None, {"parsed": True}
        except ImportError:
            return True, "xml.etree not available, skipping validation", {}
        except ET.ParseError as e:
            return False, f"XML parse error: {str(e)}", {}

    def _validate_yaml(self, content: str) -> tuple:
        try:
            import yaml
            data = yaml.safe_load(content)
            return True, None, {"parsed": True, "type": type(data).__name__}
        except ImportError:
            return True, "PyYAML not available, skipping validation", {}
        except yaml.YAMLError as e:
            return False, f"YAML error: {str(e)}", {}

    def _validate_markdown(self, content: str) -> tuple:
        # markdown mark
        has_headers = bool(re.search(r'^#{1,6}\s', content, re.MULTILINE))
        has_lists = bool(re.search(r'^\s*[-*+]\s', content, re.MULTILINE))
        has_code = bool(re.search(r'`.*?`', content))

        details = {
            "has_headers": has_headers,
            "has_lists": has_lists,
            "has_code": has_code
        }

        is_valid = len(content.strip()) > 0
        return is_valid, None if is_valid else "Empty markdown", details

    def _validate_html(self, content: str) -> tuple:
        try:
            from html.parser import HTMLParser
            parser = HTMLParser()
            parser.feed(content)
            return True, None, {"parsed": True}
        except Exception as e:
            return False, f"HTML parse error: {str(e)}", {}

    def _validate_csv(self, content: str) -> tuple:
        try:
            import csv
            import io
            reader = csv.reader(io.StringIO(content))
            rows = list(reader)
            return True, None, {"row_count": len(rows), "parsed": True}
        except Exception as e:
            return False, f"CSV parse error: {str(e)}", {}

    def _validate_generic(self, content: str) -> tuple:
        is_valid = len(content.strip()) > 0
        return is_valid, None if is_valid else "Empty content", {}


@scorer_register(MetricNames.SCHEMA_COMPLIANCE)
class SchemaValidationScorer(RuleScorer):
    """structure_validity, hierarchy_correctness, field_presence, field_type_match."""
    def __init__(self, eval_config: EvaluationConfig = None):
        super().__init__(name=MetricNames.SCHEMA_COMPLIANCE, eval_config=eval_config)

    async def score(
            self,
            index: int,
            input: EvalDataCase[dict],
            output: dict
    ) -> ScorerResult:
        schema = input.case_data.get("schema")
        format_type = input.case_data.get("format_type", "json")

        if not schema:
            metric_result: MetricResult = {
                "value": 1.0,
                "metadata": {"skipped": True, "reason": "No schema provided"}
            }
            return ScorerResult(
                scorer_name=self.name,
                metric_results={"schema_compliance": metric_result}
            )

        content = self._extract(output)
        data = self._parse_content(content, format_type)

        if data is None:
            score = 0.0
            error_msg = "Failed to parse content"
            details = {}
        else:
            is_valid, error_msg, details = self._validate_schema(data, schema)
            score = 1.0 if is_valid else 0.0

        metric_result: MetricResult = {
            "value": score,

            "metadata": {
                "error": error_msg,
                "details": details
            }
        }

        return ScorerResult(
            scorer_name=self.name,
            metric_results={"schema_compliance": metric_result}
        )

    def _extract_content(self, output: Any) -> str:
        if isinstance(output, str):
            return output
        elif isinstance(output, dict):
            return output.get("content", output.get("text", str(output)))
        else:
            return str(output)

    def _parse_content(self, content: str, format_type: str) -> Any:
        try:
            if format_type == "json":
                return json.loads(content)
            elif format_type == "yaml":
                import yaml
                return yaml.safe_load(content)
            else:
                return content
        except:
            return None

    def _validate_schema(self, data: Any, schema: dict) -> tuple:
        try:
            try:
                import jsonschema

                jsonschema.validate(instance=data, schema=schema)
                return True, None, {"validated": True}
            except ImportError:
                # use inner simple schema validation
                return self._simple_schema_validation(data, schema)
        except Exception as e:
            return False, str(e), {}

    def _simple_schema_validation(self, data: Any, schema: dict) -> tuple:
        if not isinstance(data, dict):
            return False, "Data is not a dictionary", {}

        # check required field
        required = schema.get("required", [])
        missing = [f for f in required if f not in data]

        if missing:
            return False, f"Missing required fields: {missing}", {"missing": missing}

        # check field type
        properties = schema.get("properties", {})
        type_errors = []

        for field, field_schema in properties.items():
            if field in data:
                expected_type = field_schema.get("type")
                actual_value = data[field]

                if expected_type and not self._check_type(actual_value, expected_type):
                    type_errors.append(f"{field}: expected {expected_type}, got {type(actual_value).__name__}")

        if type_errors:
            return False, f"Type errors: {type_errors}", {"type_errors": type_errors}

        return True, None, {"validated": True}

    def _check_type(self, value: Any, expected_type: str) -> bool:
        type_map = {
            "string": str,
            "number": (int, float),
            "integer": int,
            "boolean": bool,
            "array": list,
            "object": dict,
            "null": type(None)
        }

        expected = type_map.get(expected_type)
        if expected:
            return isinstance(value, expected)
        return True
