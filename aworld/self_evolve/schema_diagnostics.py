from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


_SUPPORTED_RULES = frozenset(
    {
        "enum",
        "max_chars",
        "max_items",
        "non_empty",
        "required",
        "type",
        "unique",
    }
)
_SUPPORTED_VALUE_TYPES = frozenset(
    {"array", "boolean", "null", "number", "object", "string"}
)
_SCHEMA_TOKEN = re.compile(r"^[A-Za-z0-9_.\[\]*-]{1,240}$")
_EXPECTED_TOKEN = re.compile(r"^[A-Za-z0-9_.:/-]{1,240}$")


@dataclass(frozen=True)
class SchemaFieldRepairConstraint:
    """A payload-free, executable rule for one schema field path."""

    schema_layer: str
    field_path: str
    rule: str
    expected: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if _SCHEMA_TOKEN.fullmatch(self.schema_layer) is None:
            raise ValueError("schema constraint layer is invalid")
        if _SCHEMA_TOKEN.fullmatch(self.field_path) is None:
            raise ValueError("schema constraint field path is invalid")
        if self.rule not in _SUPPORTED_RULES:
            raise ValueError("schema constraint rule is unsupported")
        normalized_expected = tuple(str(item) for item in self.expected)
        if any(
            _EXPECTED_TOKEN.fullmatch(item) is None
            for item in normalized_expected
        ):
            raise ValueError("schema constraint expected values are invalid")
        object.__setattr__(self, "expected", normalized_expected)
        if self.rule in {"enum", "type"} and not normalized_expected:
            raise ValueError("schema constraint rule requires expected values")
        if self.rule == "type" and not set(normalized_expected).issubset(
            _SUPPORTED_VALUE_TYPES
        ):
            raise ValueError("schema constraint declares an unsupported value type")
        if self.rule in {"max_chars", "max_items"} and (
            len(normalized_expected) != 1
            or not normalized_expected[0].isdigit()
            or int(normalized_expected[0]) <= 0
        ):
            raise ValueError("schema constraint bound is invalid")

    @property
    def identity_digest(self) -> str:
        return hashlib.sha256(
            json.dumps(
                self.to_dict(),
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_layer": self.schema_layer,
            "field_path": self.field_path,
            "rule": self.rule,
            "expected": list(self.expected),
        }

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, object],
    ) -> "SchemaFieldRepairConstraint":
        raw_expected = value.get("expected", ())
        if not isinstance(raw_expected, (list, tuple)):
            raise ValueError("schema constraint expected values must be an array")
        return cls(
            schema_layer=str(value.get("schema_layer") or ""),
            field_path=str(value.get("field_path") or ""),
            rule=str(value.get("rule") or ""),
            expected=tuple(str(item) for item in raw_expected),
        )

    def accepts(self, value: Any, *, present: bool = True) -> bool:
        if self.rule == "required":
            return present
        if not present:
            return True
        if self.rule == "enum":
            return isinstance(value, str) and value in self.expected
        if self.rule == "type":
            return schema_value_type(value) in self.expected
        if self.rule == "non_empty":
            return isinstance(value, (str, list, tuple, Mapping)) and bool(value)
        if self.rule == "unique":
            if not isinstance(value, (list, tuple)):
                return False
            canonical = [_canonical_schema_bytes(item) for item in value]
            return len(canonical) == len(set(canonical))
        if self.rule == "max_chars":
            return isinstance(value, str) and len(value) <= int(self.expected[0])
        if self.rule == "max_items":
            return isinstance(value, (list, tuple, Mapping)) and len(value) <= int(
                self.expected[0]
            )
        return False


@dataclass(frozen=True)
class SchemaFieldViolation:
    constraint: SchemaFieldRepairConstraint
    actual_type: str
    actual_fingerprint: str
    occurrence_count: int = 1

    @classmethod
    def create(
        cls,
        constraint: SchemaFieldRepairConstraint,
        value: Any,
        *,
        occurrence_count: int = 1,
    ) -> "SchemaFieldViolation":
        if occurrence_count <= 0:
            raise ValueError("schema violation occurrence count must be positive")
        return cls(
            constraint=constraint,
            actual_type=schema_value_type(value),
            actual_fingerprint=(
                "sha256:" + hashlib.sha256(_canonical_schema_bytes(value)).hexdigest()
            ),
            occurrence_count=occurrence_count,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "constraint_identity_digest": self.constraint.identity_digest,
            "schema_layer": self.constraint.schema_layer,
            "field_path": self.constraint.field_path,
            "rule": self.constraint.rule,
            "actual_type": self.actual_type,
            "actual_fingerprint": self.actual_fingerprint,
            "occurrence_count": self.occurrence_count,
        }


def schema_field_diagnostic_details(
    violations: Sequence[SchemaFieldViolation],
) -> dict[str, object]:
    """Build a public-safe diagnostic preserving every distinct field rule."""

    constraints = {
        violation.constraint.identity_digest: violation.constraint
        for violation in violations
    }
    return {
        "schema_field_constraints": [
            constraints[key].to_dict() for key in sorted(constraints)
        ],
        "schema_field_violations": [
            violation.to_dict() for violation in violations[:100]
        ],
        "schema_field_violation_count": sum(
            violation.occurrence_count for violation in violations
        ),
    }


def schema_value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, (list, tuple)):
        return "array"
    if isinstance(value, (int, float)):
        return "number"
    return f"unsupported:{type(value).__module__}.{type(value).__qualname__}"


def _canonical_schema_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            default=lambda item: (
                f"<{type(item).__module__}.{type(item).__qualname__}>"
            ),
        ).encode("utf-8")
    except (TypeError, ValueError):
        return repr(type(value)).encode("utf-8")
