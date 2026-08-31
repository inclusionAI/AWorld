from __future__ import annotations

import ast
import copy
from pathlib import Path

from aworld.agents.history_context_adapter import (
    CleanedHistoryReplayContextAdapter,
    adapt_cleaned_history_replay,
)
from aworld.core.context.compiler import (
    Authority,
    ContextKind,
    Lifetime,
    ScopeKind,
    Stability,
    Trust,
    thaw_json,
)
from aworld.core.context.compiler.adapters import OccurrenceContextAdapter


def test_observes_owner_cleaned_tool_replay_verbatim() -> None:
    # This is the shape LLMAgent.async_messages_transform owns after it has
    # discarded an orphan result and ordered the retained results by tool call.
    duplicate = {"role": "user", "content": {"parts": ["same"]}}
    owner_final = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-b",
                    "type": "function",
                    "function": {"name": "second", "arguments": "{}"},
                },
                {
                    "id": "call-a",
                    "type": "function",
                    "function": {"name": "first", "arguments": "{}"},
                },
            ],
        },
        {"role": "tool", "tool_call_id": "call-b", "content": "result-b"},
        {"role": "tool", "tool_call_id": "call-a", "content": "result-a"},
        copy.deepcopy(duplicate),
        copy.deepcopy(duplicate),
    ]
    before = copy.deepcopy(owner_final)

    adapter = CleanedHistoryReplayContextAdapter()
    assert isinstance(adapter, OccurrenceContextAdapter)
    result = adapter.adapt(
        owner_final,
        source_identity="llm-agent://agent-1/final-history-replay",
    )

    assert owner_final == before
    assert [thaw_json(item.payload) for item in result.items] == before
    assert [item.occurrence for item in result.items] == list(range(len(before)))
    assert [item.kind for item in result.items] == [
        ContextKind.UNKNOWN,
        ContextKind.TOOL_RESULT,
        ContextKind.TOOL_RESULT,
        ContextKind.USER,
        ContextKind.USER,
    ]
    assert result.items[3].content_hash == result.items[4].content_hash
    assert result.items[3].id != result.items[4].id
    assert result.items[1].payload["tool_call_id"] == "call-b"
    assert result.items[2].payload["tool_call_id"] == "call-a"
    assert result.diagnostics[0].code == "owner_finalized_history_replay"
    assert "did not clean, pair, deduplicate, or reorder" in result.diagnostics[0].message

    owner_final[0]["tool_calls"].reverse()
    assert thaw_json(result.items[0].payload) == before[0]


def test_adapter_does_not_reclean_or_synthesize_owner_occurrences() -> None:
    # If a caller violates the post-cleanup precondition, observation remains
    # lossless: the adapter must not become a second cleanup implementation.
    occurrences = [
        {"role": "tool", "tool_call_id": "orphan", "content": "keep-observed"},
        {"role": "assistant", "content": "after orphan"},
    ]

    result = adapt_cleaned_history_replay(
        occurrences,
        source_identity="llm-agent://agent-1/post-cleanup-boundary",
        task_epoch=8,
    )

    assert [thaw_json(item.payload) for item in result.items] == occurrences
    assert len(result.items) == 2
    assert result.items[0].id.endswith(":legacy-message:0")
    assert result.items[1].id.endswith(":legacy-message:1")
    assert all(item.task_epoch == 8 for item in result.items)


def test_unproved_history_provenance_remains_unknown() -> None:
    result = adapt_cleaned_history_replay(
        [{"role": "user", "content": "from an opaque final replay"}],
        source_identity="llm-agent://unknown-owner/final-history-replay",
    )
    item = result.items[0]

    assert item.authority is Authority.UNKNOWN
    assert item.scope.kinds == (ScopeKind.UNKNOWN,)
    assert item.lifetime is Lifetime.UNKNOWN
    assert item.trust is Trust.UNKNOWN
    assert item.stability is Stability.UNKNOWN
    assert item.task_epoch is None
    assert item.token_limit is None
    assert item.source.uri == "llm-agent://unknown-owner/final-history-replay"
    assert item.source.ref["occurrence"] == 0
    assert set(result.diagnostics[1].unknown_fields) >= {
        "authority",
        "scope",
        "lifetime",
        "trust",
        "stability",
        "task_epoch",
    }


def test_compiler_core_does_not_import_history_owner() -> None:
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
    assert not any(
        module == "aworld.memory" or module.startswith("aworld.memory.")
        for module in imported_modules
    )
