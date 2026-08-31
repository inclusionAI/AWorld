from __future__ import annotations

import ast
import copy
import inspect

import aworld.core.context.compiler.adapters as adapters_module
from aworld.core.context.compiler import (
    AdapterDiagnostic,
    AdapterDiagnosticSeverity,
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
from aworld.core.context.compiler.adapters import (
    LegacyFinalMessageAdapter,
    LegacyToolSchemaAdapter,
    OccurrenceContextAdapter,
    adapt_final_messages,
    adapt_tool_schemas,
)


def test_adapter_diagnostic_freezes_unknown_fields_from_mutable_input() -> None:
    unknown_fields = ["authority"]
    diagnostic = AdapterDiagnostic(
        code="legacy_unknown",
        message="owner evidence is unavailable",
        severity=AdapterDiagnosticSeverity.INFO,
        source_identity="test-owner",
        unknown_fields=unknown_fields,
    )

    unknown_fields.append("trust")

    assert diagnostic.unknown_fields == ("authority",)


def test_final_message_adapter_preserves_order_duplicates_pairs_and_input() -> None:
    duplicate = {"role": "user", "content": ["same", {"part": 2}]}
    messages = [
        {"role": "system", "content": "rules"},
        copy.deepcopy(duplicate),
        copy.deepcopy(duplicate),
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "search", "arguments": '{"q":"x"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": {"result": [1, 2]}},
    ]
    before = copy.deepcopy(messages)

    result = adapt_final_messages(messages, source_identity="request://final/messages")

    assert messages == before
    assert [thaw_json(item.payload) for item in result.items] == before
    assert [item.occurrence for item in result.items] == list(range(len(messages)))
    assert [item.kind for item in result.items] == [
        ContextKind.SYSTEM,
        ContextKind.USER,
        ContextKind.USER,
        ContextKind.UNKNOWN,
        ContextKind.TOOL_RESULT,
    ]
    assert result.items[1].content_hash == result.items[2].content_hash
    assert result.items[1].id != result.items[2].id
    assert result.items[1].id.endswith(":legacy-message:1")
    assert result.items[2].id.endswith(":legacy-message:2")
    assert result.items[3].payload["tool_calls"][0]["id"] == "call-1"
    assert result.items[4].payload["tool_call_id"] == "call-1"
    assert all(item.source.kind is SourceKind.LEGACY_MESSAGE for item in result.items)
    assert len(result.diagnostics) == len(messages)
    assert "kind" in result.diagnostics[3].unknown_fields

    messages[4]["content"]["result"].append(3)
    assert thaw_json(result.items[4].payload) == before[4]


def test_legacy_adapter_keeps_unproved_semantics_unknown() -> None:
    result = LegacyFinalMessageAdapter().adapt(
        [{"role": "system", "content": "legacy system"}],
        source_identity="request://unknown-semantics",
    )
    item = result.items[0]

    assert item.kind is ContextKind.SYSTEM
    assert item.task_epoch is None
    assert item.authority is Authority.UNKNOWN
    assert item.scope.kinds == (ScopeKind.UNKNOWN,)
    assert item.lifetime is Lifetime.UNKNOWN
    assert item.trust is Trust.UNKNOWN
    assert item.stability is Stability.UNKNOWN
    assert item.token_limit is None
    assert item.reducer is None
    assert item.content_hash == canonical_json_hash(
        {"role": "system", "content": "legacy system"}
    )
    diagnostic = result.diagnostics[0]
    assert set(diagnostic.unknown_fields) >= {
        "authority",
        "scope",
        "lifetime",
        "priority",
        "required",
        "trust",
        "stability",
        "token_estimate",
        "task_epoch",
    }


def test_tool_schema_adapter_is_occurrence_preserving_and_never_hash_deduplicates() -> None:
    schema = {
        "type": "function",
        "function": {
            "name": "search",
            "description": "search",
            "parameters": {"type": "object", "properties": {}},
        },
    }
    tools = [copy.deepcopy(schema), copy.deepcopy(schema)]
    before = copy.deepcopy(tools)

    adapter = LegacyToolSchemaAdapter()
    assert isinstance(adapter, OccurrenceContextAdapter)
    result = adapt_tool_schemas(tools, source_identity="request://final/tools", task_epoch=4)

    assert tools == before
    assert [thaw_json(item.payload) for item in result.items] == before
    assert [item.kind for item in result.items] == [
        ContextKind.TOOL_CATALOG,
        ContextKind.TOOL_CATALOG,
    ]
    assert [item.source.kind for item in result.items] == [
        SourceKind.TOOL_CATALOG,
        SourceKind.TOOL_CATALOG,
    ]
    assert result.items[0].content_hash == result.items[1].content_hash
    assert result.items[0].id != result.items[1].id
    assert [item.task_epoch for item in result.items] == [4, 4]
    assert all("task_epoch" not in item.unknown_fields for item in result.diagnostics)


def test_compiler_adapter_has_no_reverse_dependency_on_owner_packages() -> None:
    tree = ast.parse(inspect.getsource(adapters_module))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    imported_modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )

    assert not any("aworld.core.context.amni" in module for module in imported_modules)
    assert not any("aworld.agents" in module for module in imported_modules)
    assert not any("aworld.models" in module for module in imported_modules)
