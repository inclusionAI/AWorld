from __future__ import annotations

import json

import pytest

from aworld.self_evolve.candidate_protocol import (
    CANDIDATE_SCHEMA_VERSION,
    CandidateProtocolError,
    normalize_candidate_output,
)


CURRENT_CONTENT = "# Demo\n\nExisting guidance.\n"
SKILL_CONTENT = "---\nname: demo\ndescription: demo skill\n---\n\n# Demo\n"


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


def test_rejects_full_skill_content_that_drops_yaml_frontmatter() -> None:
    with pytest.raises(CandidateProtocolError) as error:
        normalize_candidate_output(
            {
                "schema_version": CANDIDATE_SCHEMA_VERSION,
                "content": "# Demo\n\nImproved guidance.\n",
                "files": [],
            },
            current_content=SKILL_CONTENT,
        )

    assert error.value.code == "missing_candidate_frontmatter"
    assert error.value.field_path == "content"
    assert error.value.repairable is False


def test_rejects_destructive_full_replacement_of_large_target() -> None:
    current = "---\nname: demo\n---\n\n# Demo\n\n" + ("existing guidance\n" * 2_000)
    replacement = "---\nname: demo\n---\n\n# Demo\n\nShort rewrite.\n"

    with pytest.raises(CandidateProtocolError) as error:
        normalize_candidate_output(
            {
                "schema_version": CANDIDATE_SCHEMA_VERSION,
                "content": replacement,
                "files": [],
            },
            current_content=current,
        )

    assert error.value.code == "destructive_full_content_replacement"
    assert error.value.field_path == "content"
    assert error.value.repairable is True


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


def test_repairs_one_truncated_candidate_json_before_semantic_validation() -> None:
    normalized = normalize_candidate_output(
        (
            '{"content":"# Demo\\n\\nRecovered candidate.\\n",'
            '"rationale":"recover transport truncation","files":[]'
        ),
        current_content=CURRENT_CONTENT,
    )

    assert normalized["schema_version"] == CANDIDATE_SCHEMA_VERSION
    assert normalized["content"].endswith("Recovered candidate.")


def test_accepts_files_only_skill_package_behavior_delta() -> None:
    normalized = normalize_candidate_output(
        {
            "schema_version": CANDIDATE_SCHEMA_VERSION,
            "rationale": "publish reusable candidate-owned runtime behavior",
            "files": [
                {
                    "path": "replay/runtime.py",
                    "operation": "upsert",
                    "content": "def respond():\n    return {'recorded': True}\n",
                }
            ],
        },
        current_content=CURRENT_CONTENT,
    )

    assert "content" not in normalized
    assert "patch_intent" not in normalized
    assert normalized["files"][0]["path"] == "replay/runtime.py"


def test_rejects_candidate_without_body_or_package_files() -> None:
    with pytest.raises(CandidateProtocolError) as error:
        normalize_candidate_output(
            {"rationale": "no behavior delta", "files": []},
            current_content=CURRENT_CONTENT,
        )

    assert error.value.code == "missing_candidate_body"


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


def test_accepts_structural_replace_patch_before_focused_base_is_available() -> None:
    normalized = normalize_candidate_output(
        {
            "patch_intent": {
                "operations": [
                    {
                        "op": "replace_section",
                        "heading": "Focused Candidate Section",
                        "content": "Keep the verified behavior and finalize once.",
                    }
                ]
            },
            "rationale": "Apply this delta to the focused repair candidate.",
        },
        current_content=CURRENT_CONTENT,
    )

    assert normalized["patch_intent"]["operations"][0]["op"] == "replace_section"


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
