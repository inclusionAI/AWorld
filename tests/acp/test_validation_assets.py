from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_cli.acp.validation_assets import (
    list_validation_config_fields,
    load_validation_template,
    validate_validation_config_shape,
    validation_config_schema_path,
    validation_template_metadata,
)


def test_validation_template_metadata_reports_known_topologies() -> None:
    payload = validation_template_metadata()

    names = [item["name"] for item in payload]
    assert names == ["base", "distributed", "same-host"]
    assert any("AWORLD_WORKER_WORKSPACE" in item["requiredEnv"] for item in payload if item["name"] == "distributed")
    assert "templatePath" in payload[0]


def test_validation_config_schema_path_points_at_existing_schema_asset() -> None:
    schema_path = Path(validation_config_schema_path())

    assert schema_path.name == "acp_stdio_host_contract.schema.json"
    assert schema_path.is_file()


def test_validation_config_schema_supports_topology_driven_partial_config() -> None:
    schema_path = Path(validation_config_schema_path())
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert "oneOf" in schema
    assert any(
        branch.get("required") == ["topology"]
        for branch in schema["oneOf"]
        if isinstance(branch, dict)
    )


def test_list_validation_config_fields_reports_supported_schema() -> None:
    assert list_validation_config_fields() == [
        "command",
        "cwd",
        "env",
        "profile",
        "sessionParams",
        "startupRetries",
        "startupTimeoutSeconds",
        "topology",
    ]


def test_load_validation_template_returns_unexpanded_payload_by_default() -> None:
    payload = load_validation_template("same-host")

    assert payload["topology"] == "same-host"
    assert payload["cwd"] == "${AWORLD_WORKSPACE}"


def test_load_validation_template_can_expand_placeholders() -> None:
    payload = load_validation_template(
        "distributed",
        expand_placeholders=True,
        env={"AWORLD_WORKER_WORKSPACE": "/tmp/worker"},
    )

    assert payload["cwd"] == "/tmp/worker"
    assert payload["sessionParams"]["cwd"] == "/tmp/worker"


def test_load_validation_template_rejects_unknown_topology() -> None:
    with pytest.raises(ValueError):
        load_validation_template("missing")


def test_validate_validation_config_shape_rejects_unknown_field() -> None:
    with pytest.raises(ValueError):
        validate_validation_config_shape({"command": "python -m demo_host", "unexpected": True})


def test_validate_validation_config_shape_rejects_unknown_topology() -> None:
    with pytest.raises(ValueError):
        validate_validation_config_shape({"topology": "weird"})
