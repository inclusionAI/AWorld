from __future__ import annotations

import json

import pytest

from aworld.self_evolve.candidate_protocol import (
    CANDIDATE_SCHEMA_VERSION,
    CandidateProtocolError,
    normalize_candidate_output,
)


CURRENT_CONTENT = "# Demo\n\nExisting guidance.\n"


def test_normalizes_canonical_top_level_candidate() -> None:
    normalized = normalize_candidate_output(
        {
            "schema_version": CANDIDATE_SCHEMA_VERSION,
            "content": "# Demo\n\nImproved guidance.\n",
            "rationale": "improve the reusable workflow",
            "files": [],
        },
        current_content=CURRENT_CONTENT,
    )

    assert normalized == {
        "schema_version": CANDIDATE_SCHEMA_VERSION,
        "content": "# Demo\n\nImproved guidance.\n",
        "rationale": "improve the reusable workflow",
        "files": [],
    }


def test_normalizes_legacy_candidate_output_contract_envelope() -> None:
    normalized = normalize_candidate_output(
        {
            "candidate_index": 0,
            "candidate_strategy": "missing_capability_completion",
            "candidate_output_contract": {
                "content": "# Demo\n\nEnvelope candidate.\n",
                "rationale": "publish the missing capability",
                "files": [],
            },
        },
        current_content=CURRENT_CONTENT,
    )

    assert normalized["schema_version"] == CANDIDATE_SCHEMA_VERSION
    assert normalized["content"].endswith("Envelope candidate.\n")
    assert "candidate_output_contract" not in normalized
    assert "candidate_strategy" not in normalized


@pytest.mark.parametrize(
    "raw_output",
    [
        "```json\n"
        + json.dumps({"content": "# Demo\n\nFenced candidate.\n", "files": []})
        + "\n```",
        "Candidate package follows:\n"
        + json.dumps({"content": "# Demo\n\nProse candidate.\n", "files": []})
        + "\nEnd of candidate.",
    ],
)
def test_normalizes_one_json_object_from_supported_text_variants(
    raw_output: str,
) -> None:
    normalized = normalize_candidate_output(
        raw_output,
        current_content=CURRENT_CONTENT,
    )

    assert normalized["schema_version"] == CANDIDATE_SCHEMA_VERSION
    assert normalized["content"].startswith("# Demo")


def test_rejects_conflicting_direct_and_envelope_candidate_fields() -> None:
    with pytest.raises(CandidateProtocolError) as error:
        normalize_candidate_output(
            {
                "content": "# Demo\n\nDirect.\n",
                "candidate_output_contract": {
                    "content": "# Demo\n\nEnvelope.\n",
                    "files": [],
                },
            },
            current_content=CURRENT_CONTENT,
        )

    assert error.value.code == "conflicting_candidate_envelope"
    assert error.value.field_path == "content"


def test_rejects_multiple_json_objects_in_text() -> None:
    with pytest.raises(CandidateProtocolError) as error:
        normalize_candidate_output(
            '{"content":"# Demo\\nFirst"} {"content":"# Demo\\nSecond"}',
            current_content=CURRENT_CONTENT,
        )

    assert error.value.code == "multiple_json_objects"


def test_rejects_candidate_with_both_content_and_patch_intent() -> None:
    with pytest.raises(CandidateProtocolError) as error:
        normalize_candidate_output(
            {
                "content": "# Demo\n\nReplacement.\n",
                "patch_intent": {
                    "operations": [
                        {
                            "op": "append_section",
                            "heading": "Workflow",
                            "content": "Use the workflow.",
                        }
                    ]
                },
                "files": [],
            },
            current_content=CURRENT_CONTENT,
        )

    assert error.value.code == "ambiguous_candidate_body"


def test_protocol_error_exposes_bounded_typed_diagnostic() -> None:
    error = CandidateProtocolError(
        "missing_candidate_body",
        "candidate requires content or patch_intent",
        field_path="content|patch_intent",
    )

    assert error.to_diagnostic() == {
        "code": "missing_candidate_body",
        "stage": "candidate_protocol",
        "failure_class": "candidate",
        "repairable": True,
        "field_path": "content|patch_intent",
    }
