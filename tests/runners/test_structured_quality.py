import json

import pytest

from aworld.core.context.base import Context
from aworld.runners.structured_quality import (
    inspect_public_structured_quality,
    observe_structured_quality,
)


def _result(payload, *, call_id="call-1"):
    return {
        "tool_call_id": call_id,
        "success": True,
        "error": None,
        "content": json.dumps(
            {
                "success": True,
                "message": {"stdout": json.dumps(payload)},
            }
        ),
    }


def test_observes_open_table_quality_from_sandbox_bound_json():
    observation = observe_structured_quality(
        [
            _result(
                {
                    "result_id": "result-1",
                    "quality_report": {
                        "tables": {
                            "declared": 2,
                            "usable": 0,
                            "issues": [
                                {"reason": "filex_structured_block_meta_prose"}
                            ],
                        }
                    },
                }
            )
        ],
        trusted_call_ids={"call-1"},
    )

    assert observation is not None
    assert observation["status"] == "open"
    assert observation["required_table_count"] >= 2
    assert observation["usable_table_count"] == 0
    assert "filex_structured_block_meta_prose" in observation["reason_codes"]


def test_ignores_unbound_or_failed_quality_claims():
    payload = {
        "quality_report": {"tables": {"declared": 1, "usable": 0}}
    }
    assert (
        observe_structured_quality(
            [_result(payload, call_id="forged")],
            trusted_call_ids={"different-call"},
        )
        is None
    )


@pytest.mark.parametrize(
    ("payload", "expected_status"),
    (
        (
            {"quality_report": {"tables": {"declared": 1, "usable": 0}}},
            "open",
        ),
        (
            {"quality_report": {"tables": {"declared": 1, "usable": 1}}},
            "clear",
        ),
        ({"blocks": [{"type": "table", "text": "a value is three"}]}, "open"),
        (
            {
                "blocks": [
                    {"type": "table", "table": {"rows": [["a"], ["3"]]}}
                ]
            },
            "clear",
        ),
        ({"blocks": [{"type": "table", "cells": [{"row": 0, "col": 0}]}]}, "clear"),
        (
            {
                "blocks": [
                    {"type": "table", "text": "| a | b |\n| --- | --- |\n| 1 | 2 |"}
                ]
            },
            "clear",
        ),
        ({"blocks": [{"type": "table", "html": "<table><tr><td>1</td></tr></table>"}]}, "clear"),
        (
            {
                "schema_version": "aworld.structured-quality-observation/v1",
                "status": "open",
                "required_table_count": 1,
                "usable_table_count": 0,
                "reason_codes": ["cell_grid_missing"],
            },
            "open",
        ),
        (
            {
                "schema_version": "aworld.structured-quality-observation/v1",
                "status": "clear",
                "required_table_count": 1,
                "usable_table_count": 1,
                "reason_codes": [],
            },
            "clear",
        ),
    ),
)
def test_generic_nine_case_table_quality_matrix(payload, expected_status):
    observation = observe_structured_quality(
        [_result(payload)],
        trusted_call_ids={"call-1"},
    )

    assert observation is not None
    assert observation["status"] == expected_status


def test_public_candidate_clears_debt_only_with_real_cell_structure(tmp_path):
    document = tmp_path / "document.md"
    layout = tmp_path / "layout.json"
    context = Context(task_id="structured-quality")
    context.context_info["public_deliverable_contract"] = {
        "schema_version": "aworld.public-deliverables/v1",
        "authority": "public_task_advisory",
        "source": "public_task_text",
        "artifacts": [
            {
                "deliverable_id": "document",
                "path": str(document),
                "display_path": "document.md",
                "kind": "file",
                "authority": "public_task_advisory",
            },
            {
                "deliverable_id": "layout",
                "path": str(layout),
                "display_path": "layout.json",
                "kind": "file",
                "authority": "public_task_advisory",
            },
        ],
    }
    document.write_text("Based on the image, the first value is 3.")
    layout.write_text(
        json.dumps({"blocks": [{"type": "table", "usable": False}]})
    )

    open_observation = inspect_public_structured_quality(
        context, required_table_count=1
    )
    assert open_observation is not None
    assert open_observation["status"] == "open"

    document.write_text("| name | value |\n| --- | --- |\n| a | 3 |\n")
    layout.write_text(
        json.dumps(
            {
                "blocks": [
                    {
                        "type": "table",
                        "table": {"rows": [["name", "value"], ["a", "3"]]},
                    }
                ]
            }
        )
    )
    clear_observation = inspect_public_structured_quality(
        context, required_table_count=1
    )
    assert clear_observation is not None
    assert clear_observation["status"] == "clear"
