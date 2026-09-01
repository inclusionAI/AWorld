from __future__ import annotations

import pytest

from aworld.config.conf import ContextCompilerRuntimeConfig
from aworld.core.context.base import Context
from aworld.core.context.compiler import (
    CacheBreakReason,
    CatalogChangeAction,
    TaskCatalogSnapshot,
    ToolCatalogEntry,
    compile_minimal_tool_catalog,
    transition_task_catalog,
)
from aworld.skills.progressive_context import publish_progressive_skill_context


def _entry(tool_id: str) -> ToolCatalogEntry:
    return ToolCatalogEntry(
        tool_id=tool_id,
        schema={"type": "function", "function": {"name": tool_id}},
        schema_version="v1",
        source="permission-filtered-test",
        estimated_tokens=1,
    )


def _snapshot(epoch: int, *tool_ids: str) -> TaskCatalogSnapshot:
    return TaskCatalogSnapshot.build(epoch, (_entry(value) for value in tool_ids))


def test_progressive_base_config_distinguishes_compatibility_and_empty_opt_in():
    assert ContextCompilerRuntimeConfig().progressive_tool_base_tools is None
    assert ContextCompilerRuntimeConfig(
        progressive_tool_base_tools=[]
    ).progressive_tool_base_tools == []


def test_minimal_catalog_uses_entries_as_permission_bound_and_preserves_order():
    entries = (_entry("z"), _entry("a"), _entry("m"))

    selected = compile_minimal_tool_catalog(
        entries,
        base_tools=("a", "missing"),
        skill_requested_tools=("z", "denied"),
        task_epoch=3,
    )

    assert [entry.tool_id for entry in selected.entries] == ["z", "a"]


def test_explicit_empty_base_selects_only_skill_requests():
    selected = compile_minimal_tool_catalog(
        (_entry("shell"), _entry("read")),
        base_tools=(),
        skill_requested_tools=("read",),
        task_epoch=1,
    )
    assert [entry.tool_id for entry in selected.entries] == ["read"]


def test_sticky_transition_distinguishes_requested_applied_and_deferred():
    previous = _snapshot(4, "read")
    transition = transition_task_catalog(
        previous,
        _snapshot(4, "read", "write"),
        action=CatalogChangeAction.DEFER_NEXT_EPOCH,
    )

    assert transition.added == ("write",)
    assert transition.applied_added == ()
    assert transition.deferred_added == ("write",)
    assert transition.applied_removed == ()
    assert [entry.tool_id for entry in transition.snapshot.entries] == ["read"]
    assert transition.cache_break_reason is None


def test_sticky_permission_contraction_applies_and_breaks_cache():
    previous = _snapshot(4, "read", "write")
    transition = transition_task_catalog(
        previous,
        _snapshot(4, "read", "new"),
        action=CatalogChangeAction.DEFER_NEXT_EPOCH,
    )

    assert transition.applied_added == ()
    assert transition.deferred_added == ("new",)
    assert transition.applied_removed == ("write",)
    assert [entry.tool_id for entry in transition.snapshot.entries] == ["read"]
    assert transition.cache_break_reason is CacheBreakReason.TOOL_CATALOG_CHANGE


def test_child_catalog_request_never_mutates_parent_binding():
    context = Context(task_id="parent")
    parent = _snapshot(context.task_epoch, "read")
    context.bind_task_tool_catalog("agent", parent)

    transition = context.bind_task_tool_catalog(
        "agent",
        _snapshot(context.task_epoch, "read", "child-write"),
        action=CatalogChangeAction.CHILD_CONTEXT,
    )

    assert transition.candidate_snapshot is not None
    assert [entry.tool_id for entry in transition.snapshot.entries] == ["read"]
    assert transition.applied_added == ()
    assert transition.deferred_added == ("child-write",)
    assert transition.cache_break_reason is None


def test_skill_required_tool_resolves_only_through_exact_permission_mapping():
    context = Context(task_id="skill-task")
    configs = {
        "terminal-skill": {
            "active": True,
            "usage": "Use the configured terminal capability.",
            "tool_list": {"terminal": ["execute"]},
        }
    }

    active = publish_progressive_skill_context(
        context=context,
        agent_id="agent",
        skill_configs=configs,
        sticky=False,
        available_tool_ids=("bash",),
        tool_identity_mapping={"bash": "terminal__execute"},
        require_resolved_tools=True,
    )

    assert active == ("terminal-skill",)
    activation = context.get_skill_activations()["agent"][0]
    assert activation.activated is True
    assert activation.requested_tools == ("bash",)
    assert activation.unavailable_tools == ()


def test_skill_required_tool_cannot_recover_permission_filtered_tool():
    context = Context(task_id="skill-task")
    configs = {
        "terminal-skill": {
            "active": True,
            "usage": "Use the configured terminal capability.",
            "tool_list": {"terminal": ["execute"]},
        }
    }

    with pytest.raises(ValueError, match="skill_required_tool_unavailable"):
        publish_progressive_skill_context(
            context=context,
            agent_id="agent",
            skill_configs=configs,
            sticky=False,
            available_tool_ids=(),
            tool_identity_mapping={"bash": "terminal__execute"},
            require_resolved_tools=True,
        )
    activation = context.get_skill_activations()["agent"][0]
    assert activation.activated is False
    assert activation.reason_code == "skill_required_tool_unavailable"
    assert activation.unavailable_tools == ("terminal__execute",)
