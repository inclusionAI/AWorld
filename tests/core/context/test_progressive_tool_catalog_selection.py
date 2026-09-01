from __future__ import annotations

import pytest

from aworld.config.conf import ContextCompilerRuntimeConfig
from aworld.core.context.amni import ApplicationContext
from aworld.core.context.amni.state import (
    ApplicationTaskContextState,
    TaskInput,
    TaskOutput,
    TaskWorkingState,
)
from aworld.core.context.base import Context
from aworld.core.context.compiler import (
    CacheBreakReason,
    CatalogChangeAction,
    ContextEmissionIntent,
    ModelResidency,
    TaskCatalogSnapshot,
    ToolCatalogEntry,
    compile_minimal_tool_catalog,
    transition_task_catalog,
)
from aworld.skills.progressive_context import (
    apply_progressive_skill_proposal,
    prepare_progressive_skill_context,
    publish_progressive_skill_context,
)


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


def test_child_catalog_preview_without_parent_does_not_poison_later_sticky_bind():
    context = Context(task_id="parent")

    context.bind_task_tool_catalog(
        "agent",
        _snapshot(context.task_epoch, "child-write"),
        action=CatalogChangeAction.CHILD_CONTEXT,
    )
    parent = context.bind_task_tool_catalog(
        "agent",
        _snapshot(context.task_epoch, "read"),
        action=CatalogChangeAction.DEFER_NEXT_EPOCH,
    )

    assert [entry.tool_id for entry in parent.snapshot.entries] == ["read"]
    assert parent.deferred_added == ()


def _skill_config(usage: str, *tools: str, version: str = "v1"):
    return {
        "skill": {
            "active": True,
            "usage": usage,
            "tool_list": {"server": list(tools)},
            "aworld_metadata": {"version": version},
        }
    }


def _proposal(
    context,
    usage: str,
    *tools: str,
    available=("read", "write"),
    version: str = "v1",
):
    return prepare_progressive_skill_context(
        context=context,
        agent_id="agent",
        skill_configs=_skill_config(usage, *tools, version=version),
        available_tool_ids=available,
        tool_identity_mapping={
            "read": "server__read",
            "write": "server__write",
        },
        require_resolved_tools=True,
    )


def test_sticky_tool_expansion_retains_previous_skill_content_and_dependencies():
    context = Context(task_id="sticky-skill")
    initial = _proposal(context, "use read", "read")
    apply_progressive_skill_proposal(
        context=context,
        agent_id="agent",
        proposal=initial,
        sticky=True,
        available_tool_ids=("read",),
    )
    context.bind_task_tool_catalog(
        "agent", _snapshot(context.task_epoch, "read")
    )

    updated = _proposal(
        context, "now use write", "read", "write", version="v2"
    )
    requested = context.preview_task_skill_tool_requests(
        "agent", updated.snapshot, sticky=True
    )
    tool_transition = context.bind_task_tool_catalog(
        "agent",
        compile_minimal_tool_catalog(
            (_entry("read"), _entry("write")),
            base_tools=(),
            skill_requested_tools=requested,
            task_epoch=context.task_epoch,
        ),
        action=CatalogChangeAction.DEFER_NEXT_EPOCH,
    )
    active = apply_progressive_skill_proposal(
        context=context,
        agent_id="agent",
        proposal=updated,
        sticky=True,
        available_tool_ids=tuple(
            entry.tool_id for entry in tool_transition.snapshot.entries
        ),
    )

    sidecar = context.get_context_observations(
        owner="skills.progressive", namespace="agent"
    )[0]
    activation = context.get_skill_activations()["agent"][0]
    assert active == ("skill",)
    assert sidecar.result.items[0].payload["content"].endswith("use read")
    assert sidecar.result.items[0].version == "v1"
    assert activation.requested_tools == ("read",)
    assert activation.reason_code == "skill_sticky_previous_retained"
    assert tool_transition.deferred_added == ("write",)


def test_permission_contraction_removes_tool_and_skill_together():
    context = Context(task_id="permission-contraction")
    initial = _proposal(context, "use write", "write")
    apply_progressive_skill_proposal(
        context=context,
        agent_id="agent",
        proposal=initial,
        sticky=True,
        available_tool_ids=("write",),
    )
    context.bind_task_tool_catalog(
        "agent", _snapshot(context.task_epoch, "write")
    )

    contracted = _proposal(context, "use write", "write", available=())
    tool_transition = context.bind_task_tool_catalog(
        "agent",
        _snapshot(context.task_epoch),
        action=CatalogChangeAction.DEFER_NEXT_EPOCH,
    )
    active = apply_progressive_skill_proposal(
        context=context,
        agent_id="agent",
        proposal=contracted,
        sticky=True,
        available_tool_ids=(),
    )

    sidecar = context.get_context_observations(
        owner="skills.progressive", namespace="agent"
    )[0]
    assert active == ()
    assert sidecar.result.items == ()
    assert tool_transition.applied_removed == ("write",)
    assert context.get_skill_activations()["agent"][0].activated is False


def _amni_context():
    return ApplicationContext(
        task_state=ApplicationTaskContextState(
            task_input=TaskInput(
                session_id="checkpoint-session",
                task_id="checkpoint-task",
                content="task",
            ),
            working_state=TaskWorkingState(
                messages=[], user_profiles=[], kv_store={}
            ),
            task_output=TaskOutput(),
        )
    )


def test_amni_checkpoint_round_trip_preserves_validated_sticky_snapshots():
    context = _amni_context()
    proposal = _proposal(context, "use read", "read")
    apply_progressive_skill_proposal(
        context=context,
        agent_id="agent",
        proposal=proposal,
        sticky=True,
        available_tool_ids=("read",),
    )
    context.bind_task_tool_catalog(
        "agent", _snapshot(context.task_epoch, "read")
    )

    payload = context.to_dict()
    restored = ApplicationContext.from_dict(payload)

    assert restored.export_progressive_state() == payload["progressive_state"]
    new_skill = prepare_progressive_skill_context(
        context=restored,
        agent_id="agent",
        skill_configs={
            **_skill_config("use read", "read"),
            "new-skill": {
                "active": True,
                "usage": "use write",
                "tool_list": {"server": ["write"]},
            },
        },
        available_tool_ids=("read", "write"),
        tool_identity_mapping={
            "read": "server__read",
            "write": "server__write",
        },
        require_resolved_tools=True,
    )
    requested = restored.preview_task_skill_tool_requests(
        "agent", new_skill.snapshot, sticky=True
    )
    assert requested == ("read",)


def test_progressive_checkpoint_hash_tamper_is_rejected_atomically():
    context = Context(task_id="tamper")
    context.bind_task_tool_catalog(
        "agent", _snapshot(context.task_epoch, "read")
    )
    payload = context.export_progressive_state()
    payload["tool_catalogs"]["agent"]["catalog_hash"] = "sha256:" + "0" * 64
    clean = Context(task_id="tamper")

    with pytest.raises(ValueError, match="hash mismatch"):
        clean.restore_progressive_state(payload)

    assert clean.export_progressive_state()["tool_catalogs"] == {}

    amni = _amni_context()
    amni.bind_task_tool_catalog(
        "agent", _snapshot(amni.task_epoch, "read")
    )
    amni_payload = amni.to_dict()
    amni_payload["progressive_state"]["tool_catalogs"]["agent"][
        "catalog_hash"
    ] = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="hash mismatch"):
        ApplicationContext.from_dict(amni_payload)


def test_deep_copy_preserves_but_does_not_share_progressive_bindings():
    context = Context(task_id="copy")
    proposal = _proposal(context, "use read", "read")
    apply_progressive_skill_proposal(
        context=context,
        agent_id="agent",
        proposal=proposal,
        sticky=True,
        available_tool_ids=("read",),
    )
    context.bind_task_tool_catalog(
        "agent", _snapshot(context.task_epoch, "read")
    )
    clone = context.deep_copy()
    clone.bind_task_tool_catalog(
        "agent",
        _snapshot(clone.task_epoch),
        action=CatalogChangeAction.ACCEPT_CURRENT_EPOCH,
    )

    original_preview = context.bind_task_tool_catalog(
        "agent",
        _snapshot(context.task_epoch, "read", "write"),
        action=CatalogChangeAction.DEFER_NEXT_EPOCH,
    )
    assert [entry.tool_id for entry in original_preview.snapshot.entries] == ["read"]
    assert clone.export_progressive_state()["skill_snapshots"] == (
        context.export_progressive_state()["skill_snapshots"]
    )


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
    sidecar = context.get_context_observations()[-1]
    assert sidecar.owner == "skills.progressive"
    assert sidecar.model_residency is ModelResidency.UNKNOWN
    assert sidecar.emission_intent is ContextEmissionIntent.EVIDENCE_ONLY


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
