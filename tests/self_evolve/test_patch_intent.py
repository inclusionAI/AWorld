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


def test_apply_skill_patch_intent_normalizes_rendered_markdown_heading() -> None:
    content = "---\nname: demo\n---\n# Demo\n\n## Guidance\n\nOld rule.\n"

    updated = apply_skill_patch_intent(
        content,
        {
            "operations": [
                {
                    "op": "replace_section",
                    "heading": "## Guidance",
                    "content": "## Guidance\n\nNew bounded rule.\n",
                }
            ]
        },
    )

    assert updated == "---\nname: demo\n---\n# Demo\n\n## Guidance\n\nNew bounded rule.\n"
    assert updated.count("## Guidance") == 1


def test_replace_section_ignores_markdown_headings_inside_fenced_code() -> None:
    content = (
        "---\nname: demo\n---\n# Demo\n\n## Guidance\n\n"
        "```bash\necho before\n# shell comment\necho after\n```\n\n"
        "## Next\n\nKeep.\n"
    )

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

    assert updated == (
        "---\nname: demo\n---\n# Demo\n\n## Guidance\n\n"
        "New bounded rule.\n## Next\n\nKeep.\n"
    )


def test_replace_section_does_not_match_heading_inside_fenced_code() -> None:
    content = (
        "---\nname: demo\n---\n# Demo\n\n## Example\n\n"
        "~~~bash\n# Fake Heading\necho example\n~~~\n"
    )

    with pytest.raises(ValueError, match="section not found"):
        apply_skill_patch_intent(
            content,
            {
                "operations": [
                    {
                        "op": "replace_section",
                        "heading": "Fake Heading",
                        "content": "Replacement.\n",
                    }
                ]
            },
        )


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


def test_apply_skill_patch_intent_normalizes_appended_markdown_heading() -> None:
    updated = apply_skill_patch_intent(
        "---\nname: demo\n---\n# Demo\n",
        {
            "operations": [
                {
                    "op": "append_section",
                    "heading": "## Runtime Guidance",
                    "content": "## Runtime Guidance\n\nUse bounded evidence.\n",
                }
            ]
        },
    )

    assert updated.count("## Runtime Guidance") == 1
    assert updated.endswith("## Runtime Guidance\n\nUse bounded evidence.\n")


def test_append_section_upserts_existing_section_in_focused_repair() -> None:
    content = (
        "---\nname: demo\n---\n# Demo\n\n"
        "## Runtime Guidance\n\nOld rule.\n\n"
        "## Next\n\nKeep this section.\n"
    )

    updated = apply_skill_patch_intent(
        content,
        {
            "operations": [
                {
                    "op": "append_section",
                    "heading": "Runtime Guidance",
                    "content": "Consolidated rule.\n",
                }
            ]
        },
    )

    assert updated.count("## Runtime Guidance") == 1
    assert "Old rule." not in updated
    assert "Consolidated rule." in updated
    assert "## Next\n\nKeep this section." in updated


def test_replace_section_collapses_duplicate_focused_repair_sections() -> None:
    content = (
        "---\nname: demo\n---\n# Demo\n\n"
        "## Evidence\n\nFirst stale rule.\n\n"
        "## Unrelated\n\nPreserve me.\n\n"
        "## Evidence\n\nSecond stale rule.\n\n"
        "## Tail\n\nAlso preserve me.\n"
    )

    updated = apply_skill_patch_intent(
        content,
        {
            "operations": [
                {
                    "op": "replace_section",
                    "heading": "Evidence",
                    "content": "One consolidated rule.\n",
                }
            ]
        },
    )

    assert updated.count("## Evidence") == 1
    assert "First stale rule." not in updated
    assert "Second stale rule." not in updated
    assert "One consolidated rule." in updated
    assert "## Unrelated\n\nPreserve me." in updated
    assert "## Tail\n\nAlso preserve me." in updated


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


def test_apply_skill_patch_intent_allows_generic_credential_guidance() -> None:
    updated = apply_skill_patch_intent(
        "---\nname: demo\n---\n# Demo\n",
        {
            "operations": [
                {
                    "op": "append_section",
                    "heading": "Authentication",
                    "content": (
                        "Fill the username and password fields, then persist cookies "
                        "only in the isolated runtime. Use <api-key> as a placeholder."
                    ),
                }
            ]
        },
    )

    assert "username and password fields" in updated
    assert "Use <api-key> as a placeholder" in updated


@pytest.mark.parametrize(
    "protected_content",
    [
        "Set api_key = sk-live-secret-value before running.",
        "Send Authorization: Bearer actual-access-token.",
    ],
)
def test_apply_skill_patch_intent_rejects_concrete_credential_values(
    protected_content: str,
) -> None:
    with pytest.raises(ValueError, match="protected reference"):
        apply_skill_patch_intent(
            "---\nname: demo\n---\n# Demo\n",
            {
                "operations": [
                    {
                        "op": "append_section",
                        "heading": "Bad",
                        "content": protected_content,
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
