from __future__ import annotations

import copy

from aworld.core.context.amni.prompt.assembly.context_adapter import (
    PromptSectionContextAdapter,
    adapt_prompt_sections,
)
from aworld.core.context.amni.prompt.assembly.plan import PromptSection
from aworld.core.context.compiler import (
    Authority,
    ContextKind,
    Lifetime,
    ScopeKind,
    SourceKind,
    Stability,
    Trust,
    canonical_json_hash,
    thaw_json,
)
from aworld.core.context.compiler.adapters import OccurrenceContextAdapter


def test_prompt_section_adapter_preserves_supplied_occurrence_order_and_duplicates() -> None:
    duplicate = PromptSection(
        name="same",
        kind="system",
        stability="stable",
        content={"role": "system", "content": ["same", {"nested": True}]},
        hash="owner-group-hash",
    )
    sections = [
        PromptSection(
            name="dynamic-first",
            kind="memory",
            stability="dynamic",
            content={"role": "system", "content": "dynamic"},
        ),
        copy.deepcopy(duplicate),
        copy.deepcopy(duplicate),
    ]
    before = copy.deepcopy(sections)

    adapter = PromptSectionContextAdapter()
    assert isinstance(adapter, OccurrenceContextAdapter)
    result = adapt_prompt_sections(
        sections,
        source_identity="amni://prompt/caller-order",
    )

    assert sections == before
    assert [item.occurrence for item in result.items] == [0, 1, 2]
    assert [item.payload["name"] for item in result.items] == [
        "dynamic-first",
        "same",
        "same",
    ]
    assert [item.stability for item in result.items] == [
        Stability.TURN_DYNAMIC,
        Stability.STABLE,
        Stability.STABLE,
    ]
    assert result.items[1].content_hash == result.items[2].content_hash
    assert result.items[1].id != result.items[2].id
    assert result.items[1].content_hash == canonical_json_hash(
        {
            "name": "same",
            "kind": "system",
            "stability": "stable",
            "content": {"role": "system", "content": ["same", {"nested": True}]},
            "hash": "owner-group-hash",
        }
    )
    assert result.items[1].content_hash != "owner-group-hash"
    assert result.diagnostics[0].code == "caller_supplied_prompt_section_order"
    assert "not reconstructed" in result.diagnostics[0].message
    assert any(
        item.code == "prompt_section_hash_is_source_metadata"
        for item in result.diagnostics
    )


def test_prompt_section_adapter_uses_only_explicit_stability_without_name_inference() -> None:
    sections = [
        PromptSection(
            name="base_rules",
            kind="system",
            stability="unknown-value",
            content="rules",
        ),
        PromptSection(
            name="relevant_memory",
            kind="memory",
            stability="stable",
            content="memory",
        ),
        PromptSection(
            name="looks-like-a-skill",
            kind="unclassified",
            stability="session_stable",
            content="opaque",
        ),
    ]

    result = PromptSectionContextAdapter().adapt(
        sections,
        source_identity="amni://prompt/explicit-only",
        task_epoch=2,
    )

    assert [item.stability for item in result.items] == [
        Stability.UNKNOWN,
        Stability.STABLE,
        Stability.SESSION_STABLE,
    ]
    assert [item.kind for item in result.items] == [
        ContextKind.SYSTEM,
        ContextKind.MEMORY,
        ContextKind.UNKNOWN,
    ]
    assert "stability" in result.diagnostics[1].unknown_fields
    assert "stability" not in result.diagnostics[2].unknown_fields
    assert "kind" in result.diagnostics[3].unknown_fields
    assert all(item.task_epoch == 2 for item in result.items)


def test_prompt_section_unproved_provenance_remains_unknown_and_payload_is_detached() -> None:
    section = PromptSection(
        name="opaque",
        kind="messages",
        stability="dynamic",
        content=[{"role": "assistant", "content": {"parts": [1, 2]}}],
    )
    before = copy.deepcopy(section)

    result = adapt_prompt_sections([section], source_identity="amni://prompt/opaque")
    item = result.items[0]

    assert section == before
    assert item.kind is ContextKind.UNKNOWN
    assert item.authority is Authority.UNKNOWN
    assert item.scope.kinds == (ScopeKind.UNKNOWN,)
    assert item.lifetime is Lifetime.UNKNOWN
    assert item.trust is Trust.UNKNOWN
    assert item.source.kind is SourceKind.PROMPT_SECTION
    assert item.task_epoch is None
    assert item.token_limit is None
    assert item.reducer is None

    section.content[0]["content"]["parts"].append(3)
    assert thaw_json(item.payload)["content"] == before.content
