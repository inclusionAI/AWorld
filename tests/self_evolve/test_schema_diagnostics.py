from __future__ import annotations

import json

import pytest

from aworld.self_evolve.schema_diagnostics import (
    SchemaFieldRepairConstraint,
    SchemaFieldViolation,
    schema_field_diagnostic_details,
)


@pytest.mark.parametrize(
    ("constraint", "accepted", "rejected"),
    [
        (
            SchemaFieldRepairConstraint(
                schema_layer="compile_result",
                field_path="services[*].transport",
                rule="enum",
                expected=("http_fixture", "skill_runtime"),
            ),
            "skill_runtime",
            "compiler_protocol",
        ),
        (
            SchemaFieldRepairConstraint(
                schema_layer="compile_result",
                field_path="deterministic",
                rule="type",
                expected=("boolean",),
            ),
            True,
            "true",
        ),
        (
            SchemaFieldRepairConstraint(
                schema_layer="compile_result",
                field_path="protocol_probes",
                rule="max_items",
                expected=("2",),
            ),
            [1, 2],
            [1, 2, 3],
        ),
        (
            SchemaFieldRepairConstraint(
                schema_layer="compile_result",
                field_path=(
                    "services[*@request.requirement.status:runtime_required].transport"
                ),
                rule="enum",
                expected=("skill_runtime",),
            ),
            "skill_runtime",
            "http_fixture",
        ),
    ],
)
def test_schema_field_constraint_is_executable_and_round_trips(
    constraint: SchemaFieldRepairConstraint,
    accepted: object,
    rejected: object,
) -> None:
    assert constraint.accepts(accepted) is True
    assert constraint.accepts(rejected) is False
    assert SchemaFieldRepairConstraint.from_dict(constraint.to_dict()) == constraint
    assert len(constraint.identity_digest) == 64


def test_schema_field_violation_projection_is_payload_free_and_exact() -> None:
    constraint = SchemaFieldRepairConstraint(
        schema_layer="compile_result",
        field_path="services[*].transport",
        rule="enum",
        expected=("http_fixture", "skill_runtime", "tcp_fixture"),
    )
    details = schema_field_diagnostic_details(
        (
            SchemaFieldViolation.create(constraint, "private-invalid-value"),
            SchemaFieldViolation.create(
                constraint,
                "another-private-value",
                occurrence_count=3,
            ),
        )
    )

    encoded = json.dumps(details, sort_keys=True)
    assert "private-invalid-value" not in encoded
    assert "another-private-value" not in encoded
    assert details["schema_field_violation_count"] == 4
    assert details["schema_field_constraints"] == [constraint.to_dict()]
    assert len(details["schema_field_violations"]) == 2


def test_source_behavior_constraint_is_explicit_and_round_trips() -> None:
    constraint = SchemaFieldRepairConstraint(
        schema_layer="runtime",
        field_path="environment.RESPONSE_INDEX.consumer",
        rule="enum",
        expected=("json_sidecar_record_value_projector",),
        value_domain="source_behavior",
        required_operations=(
            "read_environment_binding_as_path",
            "parse_json_object",
            "iterate_records_array",
            "project_record_value",
        ),
        forbidden_operations=("substitute_raw_fixture_recursive_scan",),
    )

    assert constraint.to_dict()["value_domain"] == "source_behavior"
    assert constraint.to_dict()["required_operations"] == [
        "read_environment_binding_as_path",
        "parse_json_object",
        "iterate_records_array",
        "project_record_value",
    ]
    assert SchemaFieldRepairConstraint.from_dict(constraint.to_dict()) == constraint
    assert constraint.accepts("json_sidecar_record_value_projector") is True


def test_schema_field_constraint_rejects_unknown_value_domain() -> None:
    with pytest.raises(ValueError, match="value domain is unsupported"):
        SchemaFieldRepairConstraint(
            schema_layer="runtime",
            field_path="source.consumer",
            rule="required",
            value_domain="runtime_assignment",
        )


def test_schema_field_constraint_operations_require_source_behavior() -> None:
    with pytest.raises(ValueError, match="require source_behavior"):
        SchemaFieldRepairConstraint(
            schema_layer="compile_result",
            field_path="services[*].transport",
            rule="enum",
            expected=("skill_runtime",),
            required_operations=("project_record_value",),
        )


def test_schema_field_constraint_rejects_payload_like_expected_values() -> None:
    with pytest.raises(ValueError, match="expected values are invalid"):
        SchemaFieldRepairConstraint(
            schema_layer="compile_result",
            field_path="services[*].transport",
            rule="enum",
            expected=("Bearer private-secret",),
        )
