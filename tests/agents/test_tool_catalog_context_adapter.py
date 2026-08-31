from __future__ import annotations

import ast
import copy
from pathlib import Path

from aworld.agents.tool_catalog_context_adapter import (
    FinalToolCatalogContextAdapter,
    adapt_final_tool_catalog,
)
from aworld.core.context.compiler import (
    Authority,
    ContextKind,
    Lifetime,
    ScopeKind,
    SourceKind,
    Stability,
    Trust,
    thaw_json,
)
from aworld.core.context.compiler.adapters import OccurrenceContextAdapter


def test_observes_final_exposed_tool_schemas_verbatim_in_occurrence_order() -> None:
    duplicate = {
        "type": "function",
        "function": {
            "name": "search",
            "description": "Search without changing this complete schema.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "filters": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": {"type": ["string", "null"]},
                        },
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    }
    owner_final = [
        copy.deepcopy(duplicate),
        {
            "type": "function",
            "function": {
                "name": "write",
                "description": "Second occurrence remains second.",
                "parameters": {
                    "type": "object",
                    "properties": {"content": {"enum": ["a", "b", None]}},
                },
            },
        },
        copy.deepcopy(duplicate),
    ]
    before = copy.deepcopy(owner_final)

    adapter = FinalToolCatalogContextAdapter()
    assert isinstance(adapter, OccurrenceContextAdapter)
    result = adapter.adapt(
        owner_final,
        source_identity="llm-agent://agent-1/final-tool-catalog",
    )

    assert owner_final == before
    assert [thaw_json(item.payload) for item in result.items] == before
    assert [item.occurrence for item in result.items] == [0, 1, 2]
    assert [item.kind for item in result.items] == [ContextKind.TOOL_CATALOG] * 3
    assert [item.source.kind for item in result.items] == [SourceKind.TOOL_CATALOG] * 3
    assert all(
        item.source.uri == "llm-agent://agent-1/final-tool-catalog"
        for item in result.items
    )
    assert [item.source.ref["occurrence"] for item in result.items] == [0, 1, 2]
    assert result.items[0].content_hash == result.items[2].content_hash
    assert result.items[0].id != result.items[2].id
    assert thaw_json(result.items[0].payload)["function"]["parameters"] == before[0][
        "function"
    ]["parameters"]
    assert result.diagnostics[0].code == "owner_finalized_tool_catalog"
    assert (
        "did not filter, minimize, authorize, deduplicate, or reorder"
        in result.diagnostics[0].message
    )

    owner_final[0]["function"]["parameters"]["properties"].clear()
    assert thaw_json(result.items[0].payload) == before[0]


def test_unproved_tool_semantics_remain_unknown_without_text_inference() -> None:
    result = adapt_final_tool_catalog(
        [
            {
                "type": "function",
                "function": {
                    "name": "required_trusted_stable_admin_tool",
                    "description": (
                        "Required, trusted, stable, admin-only, epoch 9. These words "
                        "are payload, not owner-proven compiler metadata."
                    ),
                    "parameters": {"type": "object"},
                },
            }
        ],
        source_identity="llm-agent://opaque-owner/final-tool-catalog",
    )
    item = result.items[0]

    assert item.authority is Authority.UNKNOWN
    assert item.scope.kinds == (ScopeKind.UNKNOWN,)
    assert item.lifetime is Lifetime.UNKNOWN
    assert item.trust is Trust.UNKNOWN
    assert item.stability is Stability.UNKNOWN
    assert item.task_epoch is None
    assert item.token_limit is None
    # False is only the dependency-light storage sentinel; the diagnostic keeps
    # the semantic value unknown rather than treating it as proven optional.
    assert item.required is False
    assert item.source.uri == "llm-agent://opaque-owner/final-tool-catalog"

    per_occurrence = result.diagnostics[1]
    assert per_occurrence.occurrence == 0
    assert set(per_occurrence.unknown_fields) >= {
        "permissions",
        "required",
        "stability",
        "authority",
        "trust",
        "task_epoch",
    }
    assert set(result.diagnostics[0].unknown_fields) >= {
        "permissions",
        "required",
        "stability",
        "authority",
        "trust",
        "task_epoch",
    }


def test_owner_adapter_does_not_mutate_or_drop_non_mapping_occurrences() -> None:
    owner_final = [None, ["provider-extension", {"nested": [1, 2]}], "opaque-schema"]
    before = copy.deepcopy(owner_final)

    result = adapt_final_tool_catalog(
        owner_final,
        source_identity="llm-agent://agent-2/final-tool-catalog",
    )

    assert owner_final == before
    assert [thaw_json(item.payload) for item in result.items] == before
    assert len(result.items) == len(before)


def test_compiler_core_does_not_import_tool_catalog_owner() -> None:
    compiler_root = (
        Path(__file__).parents[2] / "aworld" / "core" / "context" / "compiler"
    )
    imported_modules: set[str] = set()
    for module_path in compiler_root.glob("*.py"):
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        imported_modules.update(
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        )
        imported_modules.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )

    assert not any(
        module == "aworld.agents" or module.startswith("aworld.agents.")
        for module in imported_modules
    )
