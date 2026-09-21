from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence



def _schema_field_contract_fingerprint(
    details: Mapping[str, object],
) -> str | None:
    raw_constraints = details.get("schema_field_constraints")
    constraints = [
        {
            "schema_layer": item.get("schema_layer"),
            "field_path": item.get("field_path"),
            "rule": item.get("rule"),
            "expected": item.get("expected"),
            "value_domain": item.get("value_domain", "schema_value"),
            "required_operations": item.get("required_operations", ()),
            "forbidden_operations": item.get("forbidden_operations", ()),
        }
        for item in (
            raw_constraints[:100]
            if isinstance(raw_constraints, (list, tuple))
            else ()
        )
        if isinstance(item, Mapping)
    ]
    raw_runtime_constraints = details.get("runtime_response_constraints")
    runtime_constraints = [
        dict(item)
        for item in (
            raw_runtime_constraints[:64]
            if isinstance(raw_runtime_constraints, (list, tuple))
            else ()
        )
        if isinstance(item, Mapping)
    ]
    raw_runtime_routes = details.get("runtime_route_constraints")
    runtime_routes = [
        dict(item)
        for item in (
            raw_runtime_routes[:64]
            if isinstance(raw_runtime_routes, (list, tuple))
            else ()
        )
        if isinstance(item, Mapping)
    ]
    raw_runtime_artifacts = details.get("runtime_artifact_constraints")
    runtime_artifacts = [
        dict(item)
        for item in (
            raw_runtime_artifacts[:64]
            if isinstance(raw_runtime_artifacts, (list, tuple))
            else ()
        )
        if isinstance(item, Mapping)
    ]
    if (
        not constraints
        and not runtime_constraints
        and not runtime_routes
        and not runtime_artifacts
    ):
        return None
    sorted_schema_constraints = sorted(
        constraints,
        key=lambda item: json.dumps(item, sort_keys=True, default=str),
    )
    sorted_runtime_constraints = sorted(
        runtime_constraints,
        key=lambda item: json.dumps(item, sort_keys=True, default=str),
    )
    sorted_runtime_routes = sorted(
        runtime_routes,
        key=lambda item: json.dumps(item, sort_keys=True, default=str),
    )
    sorted_runtime_artifacts = sorted(
        runtime_artifacts,
        key=lambda item: json.dumps(item, sort_keys=True, default=str),
    )
    active_components = sum(
        bool(item)
        for item in (
            constraints,
            runtime_constraints,
            runtime_routes,
            runtime_artifacts,
        )
    )
    payload: object
    if active_components == 1:
        payload = (
            sorted_schema_constraints
            if constraints
            else sorted_runtime_constraints
            if runtime_constraints
            else sorted_runtime_routes
            if runtime_routes
            else sorted_runtime_artifacts
        )
    else:
        payload = {
            "schema_field_constraints": sorted_schema_constraints,
            "runtime_response_constraints": sorted_runtime_constraints,
            "runtime_route_constraints": sorted_runtime_routes,
            "runtime_artifact_constraints": sorted_runtime_artifacts,
        }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    prefix = (
        "schema-fields"
        if constraints and active_components == 1
        else "runtime-response"
        if runtime_constraints and active_components == 1
        else "runtime-route"
        if runtime_routes and active_components == 1
        else "runtime-artifact"
        if runtime_artifacts and active_components == 1
        else "typed-repair"
    )
    return f"{prefix}:sha256:" + hashlib.sha256(encoded).hexdigest()


def _repair_contract_fingerprint(
    details: Mapping[str, object],
) -> str | None:
    """Resolve the typed constraint identity from a gate or its projection."""

    direct = _schema_field_contract_fingerprint(details)
    if direct is not None:
        return direct
    projected = details.get("repair_conformance")
    if isinstance(projected, Mapping):
        return _schema_field_contract_fingerprint(projected)
    return None


_SUPPORTED_RULES = frozenset(
    {
        "enum",
        "contains_all",
        "max_chars",
        "max_items",
        "non_empty",
        "required",
        "starts_with",
        "type",
        "unique",
    }
)
_SUPPORTED_VALUE_TYPES = frozenset(
    {"array", "boolean", "null", "number", "object", "string"}
)
_SUPPORTED_VALUE_DOMAINS = frozenset({"schema_value", "source_behavior"})
_SCHEMA_LAYER_TOKEN = re.compile(r"^[A-Za-z0-9_.\[\]*-]{1,240}$")
_FIELD_PATH_TOKEN = re.compile(r"^[A-Za-z0-9_.\[\]*@:-]{1,240}$")
_EXPECTED_TOKEN = re.compile(r"^[A-Za-z0-9_.:/-]{1,240}$")


@dataclass(frozen=True)
class SchemaFieldRepairConstraint:
    """A payload-free, executable rule for one typed validation subject."""

    schema_layer: str
    field_path: str
    rule: str
    expected: tuple[str, ...] = ()
    value_domain: str = "schema_value"
    required_operations: tuple[str, ...] = ()
    forbidden_operations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if _SCHEMA_LAYER_TOKEN.fullmatch(self.schema_layer) is None:
            raise ValueError("schema constraint layer is invalid")
        if _FIELD_PATH_TOKEN.fullmatch(self.field_path) is None:
            raise ValueError("schema constraint field path is invalid")
        if self.rule not in _SUPPORTED_RULES:
            raise ValueError("schema constraint rule is unsupported")
        if self.value_domain not in _SUPPORTED_VALUE_DOMAINS:
            raise ValueError("schema constraint value domain is unsupported")
        normalized_expected = tuple(str(item) for item in self.expected)
        normalized_required_operations = tuple(
            str(item) for item in self.required_operations
        )
        normalized_forbidden_operations = tuple(
            str(item) for item in self.forbidden_operations
        )
        if any(
            _EXPECTED_TOKEN.fullmatch(item) is None
            for item in normalized_expected
        ):
            raise ValueError("schema constraint expected values are invalid")
        if any(
            _EXPECTED_TOKEN.fullmatch(item) is None
            for item in (
                *normalized_required_operations,
                *normalized_forbidden_operations,
            )
        ):
            raise ValueError("schema constraint operations are invalid")
        object.__setattr__(self, "expected", normalized_expected)
        object.__setattr__(
            self,
            "required_operations",
            tuple(dict.fromkeys(normalized_required_operations)),
        )
        object.__setattr__(
            self,
            "forbidden_operations",
            tuple(dict.fromkeys(normalized_forbidden_operations)),
        )
        if (
            normalized_required_operations or normalized_forbidden_operations
        ) and self.value_domain != "source_behavior":
            raise ValueError(
                "schema constraint operations require source_behavior domain"
            )
        if len(normalized_required_operations) > 32 or len(
            normalized_forbidden_operations
        ) > 32:
            raise ValueError("schema constraint declares too many operations")
        if (
            self.rule in {"enum", "type", "contains_all", "starts_with"}
            and not normalized_expected
        ):
            raise ValueError("schema constraint rule requires expected values")
        if self.rule == "type" and not set(normalized_expected).issubset(
            _SUPPORTED_VALUE_TYPES
        ):
            raise ValueError("schema constraint declares an unsupported value type")
        if self.rule == "starts_with" and len(normalized_expected) != 1:
            raise ValueError("schema constraint prefix is invalid")
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
        payload: dict[str, object] = {
            "schema_layer": self.schema_layer,
            "field_path": self.field_path,
            "rule": self.rule,
            "expected": list(self.expected),
        }
        # Preserve the v1 projection for ordinary schema fields while making
        # analyzer-owned source predicates explicit to repair consumers.
        if self.value_domain != "schema_value":
            payload["value_domain"] = self.value_domain
        if self.required_operations:
            payload["required_operations"] = list(self.required_operations)
        if self.forbidden_operations:
            payload["forbidden_operations"] = list(self.forbidden_operations)
        return payload

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, object],
    ) -> "SchemaFieldRepairConstraint":
        raw_expected = value.get("expected", ())
        if not isinstance(raw_expected, (list, tuple)):
            raise ValueError("schema constraint expected values must be an array")
        raw_required_operations = value.get("required_operations", ())
        raw_forbidden_operations = value.get("forbidden_operations", ())
        if not isinstance(raw_required_operations, (list, tuple)):
            raise ValueError(
                "schema constraint required operations must be an array"
            )
        if not isinstance(raw_forbidden_operations, (list, tuple)):
            raise ValueError(
                "schema constraint forbidden operations must be an array"
            )
        return cls(
            schema_layer=str(value.get("schema_layer") or ""),
            field_path=str(value.get("field_path") or ""),
            rule=str(value.get("rule") or ""),
            expected=tuple(str(item) for item in raw_expected),
            value_domain=str(value.get("value_domain") or "schema_value"),
            required_operations=tuple(
                str(item) for item in raw_required_operations
            ),
            forbidden_operations=tuple(
                str(item) for item in raw_forbidden_operations
            ),
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
        if self.rule == "contains_all":
            if not isinstance(value, (list, tuple)):
                return False
            actual = {str(item) for item in value}
            return set(self.expected).issubset(actual)
        if self.rule == "starts_with":
            return (
                isinstance(value, str)
                and len(self.expected) == 1
                and value.startswith(self.expected[0])
            )
        if self.rule == "max_chars":
            return isinstance(value, str) and len(value) <= int(self.expected[0])
        if self.rule == "max_items":
            return isinstance(value, (list, tuple, Mapping)) and len(value) <= int(
                self.expected[0]
            )
        return False


def websocket_handshake_http_version_constraint() -> SchemaFieldRepairConstraint:
    """Return the implementation-neutral HTTP version contract for WebSocket.

    The executable protocol probe owns the dynamic proof. This bounded source
    behavior projection gives candidate repair an exact, payload-free producer
    obligation without prescribing a server library or handler class.
    """

    return SchemaFieldRepairConstraint(
        schema_layer="runtime",
        field_path="websocket_handshake.http_version",
        rule="enum",
        expected=("HTTP/1.1",),
        value_domain="source_behavior",
        required_operations=(
            "emit_http_1_1_websocket_upgrade_status_line",
        ),
        forbidden_operations=(
            "emit_http_1_0_websocket_upgrade_status_line",
        ),
    )


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

    violations = aggregate_schema_field_violations(violations)
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


def aggregate_schema_field_violations(
    violations: Sequence[SchemaFieldViolation],
) -> tuple[SchemaFieldViolation, ...]:
    """Collapse repeated instances without losing their observed cardinality.

    A compiler can violate one wildcard constraint once for every emitted
    service.  Those instances are one repair contract, not independent repair
    frontiers.  Keep distinct observed types/fingerprints separate while
    accumulating identical occurrences into a stable, bounded projection.
    """

    aggregated: dict[tuple[str, str, str], SchemaFieldViolation] = {}
    for violation in violations:
        key = (
            violation.constraint.identity_digest,
            violation.actual_type,
            violation.actual_fingerprint,
        )
        current = aggregated.get(key)
        if current is None:
            aggregated[key] = violation
            continue
        aggregated[key] = SchemaFieldViolation(
            constraint=current.constraint,
            actual_type=current.actual_type,
            actual_fingerprint=current.actual_fingerprint,
            occurrence_count=(
                current.occurrence_count + violation.occurrence_count
            ),
        )
    return tuple(aggregated[key] for key in sorted(aggregated))


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
