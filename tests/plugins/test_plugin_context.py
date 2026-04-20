from pathlib import Path

from aworld.plugins.discovery import discover_plugins
from aworld_cli.plugin_capabilities.context import (
    CONTEXT_PHASES,
    load_plugin_contexts,
    run_context_phase,
)


def test_load_plugin_contexts_groups_adapters_by_phase():
    plugin = discover_plugins([Path("tests/fixtures/plugins/context_like").resolve()])[0]

    loaded = load_plugin_contexts([plugin])

    assert tuple(loaded.keys()) == CONTEXT_PHASES
    assert [adapter.entrypoint_id for adapter in loaded["schema"]] == ["workspace-memory"]
    assert [adapter.entrypoint_id for adapter in loaded["persist"]] == ["workspace-memory"]


def test_run_context_phase_applies_plugin_lifecycle_outputs():
    plugin = discover_plugins([Path("tests/fixtures/plugins/context_like").resolve()])[0]
    loaded = load_plugin_contexts([plugin])

    context = {"workspace": {"name": "aworld"}}
    state = {"notes": ["remember plugin state"]}

    schema = run_context_phase("schema", loaded["schema"], context=context, state=state)
    bootstrap = run_context_phase("bootstrap", loaded["bootstrap"], context=context, state=state)
    enrich = run_context_phase("enrich", loaded["enrich"], context=context, state=state)
    propagate = run_context_phase(
        "propagate",
        loaded["propagate"],
        context=context,
        state=state,
        target={"kind": "subagent"},
    )
    persist = run_context_phase("persist", loaded["persist"], context=context, state=state)

    assert schema["context-like.workspace-memory"]["fields"]["memory_notes"] == "list[str]"
    assert bootstrap["context-like.workspace-memory"]["memory_notes"] == ["remember plugin state"]
    assert enrich["context-like.workspace-memory"]["workspace_label"] == "aworld"
    assert propagate["context-like.workspace-memory"]["target_kind"] == "subagent"
    assert persist["context-like.workspace-memory"]["saved"] is True
