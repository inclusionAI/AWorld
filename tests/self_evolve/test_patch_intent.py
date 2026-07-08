from __future__ import annotations

import pytest

from aworld.self_evolve.patch_intent import apply_skill_patch_intent


def test_apply_skill_patch_intent_replaces_existing_markdown_section() -> None:
    content = "---\nname: demo\n---\n# Demo\n\n## Guidance\n\nOld rule.\n"

    updated = apply_skill_patch_intent(
        content,
        {
            "operations": [
                {
                    "op": "replace_section",
                    "heading": "Guidance",
                    "content": "New bounded rule.\n",
                }
            ]
        },
    )

    assert updated == "---\nname: demo\n---\n# Demo\n\n## Guidance\n\nNew bounded rule.\n"


def test_apply_skill_patch_intent_appends_section_after_frontmatter() -> None:
    content = "---\nname: demo\n---\n# Demo\n"

    updated = apply_skill_patch_intent(
        content,
        {
            "operations": [
                {
                    "op": "append_section",
                    "heading": "Runtime Guidance",
                    "content": "Use bounded evidence.\n",
                }
            ]
        },
    )

    assert "## Runtime Guidance\n\nUse bounded evidence.\n" in updated


def test_apply_skill_patch_intent_rejects_protected_references() -> None:
    with pytest.raises(ValueError, match="protected reference"):
        apply_skill_patch_intent(
            "---\nname: demo\n---\n# Demo\n",
            {
                "operations": [
                    {
                        "op": "append_section",
                        "heading": "Bad",
                        "content": "Read /Users/me/private/token.txt",
                    }
                ]
            },
        )


def test_apply_skill_patch_intent_rejects_whole_file_rewrite_operation() -> None:
    with pytest.raises(ValueError, match="unsupported patch operation"):
        apply_skill_patch_intent(
            "---\nname: demo\n---\n# Demo\n",
            {"operations": [{"op": "replace_file", "heading": "Demo", "content": "# New"}]},
        )


def test_apply_skill_patch_intent_rejects_oversized_materialized_skill() -> None:
    with pytest.raises(ValueError, match="materialized skill exceeds size limit"):
        apply_skill_patch_intent(
            "---\nname: demo\n---\n# Demo\n",
            {
                "operations": [
                    {
                        "op": "append_section",
                        "heading": "Too Large",
                        "content": "x" * 64,
                    }
                ]
            },
            max_chars=32,
        )
