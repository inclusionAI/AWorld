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


def test_schema_field_constraint_rejects_payload_like_expected_values() -> None:
    with pytest.raises(ValueError, match="expected values are invalid"):
        SchemaFieldRepairConstraint(
            schema_layer="compile_result",
            field_path="services[*].transport",
            rule="enum",
            expected=("Bearer private-secret",),
        )
