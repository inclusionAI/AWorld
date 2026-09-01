from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from aworld.agents.final_context_adapter import adapt_agent_final_request
from aworld.core.context.base import Context
from aworld.core.context.compiler import (
    ArtifactEvidence,
    ArtifactRequirement,
    Authority,
    CatalogChangeAction,
    ChildResult,
    ChildStatus,
    ChildUsage,
    CompletionContract,
    CompletionMode,
    CompletionStatus,
    ContextItem,
    ContextPack,
    ContextScope,
    ContextSource,
    DelegationSpec,
    DisclosureLevel,
    InferenceProfile,
    Lifetime,
    MergePolicy,
    ScopeKind,
    SerializedPrefixEvidence,
    SkillDescriptor,
    SkillIndexEntry,
    SourceKind,
    Stability,
    StopCondition,
    TaskCatalogSnapshot,
    ToolCatalogEntry,
    ToolOutputMode,
    ToolOutputPolicy,
    Trust,
    build_cache_identity,
    canonical_json_hash,
    merge_child_result,
    route_skills,
    transition_task_catalog,
)
from aworld.core.context.compiler.lifecycle import LifecycleAction
from aworld.core.context.instructions import ScopedInstructionLoader
from aworld.core.context.tool_output_runtime import (
    enforce_tool_output_boundary,
    prepare_tool_output_plans,
)


def _profile() -> InferenceProfile:
    return InferenceProfile(
        provider="openai",
        model="gpt-test",
        reasoning_effort=None,
        execution_mode="chat",
        context_limit=128000,
    )


def _item(item_id: str, *, required: bool = False) -> ContextItem:
    return ContextItem(
        id=item_id,
        kind="instruction",
        payload={"role": "system", "content": item_id},
        task_epoch=3,
        authority=(
            Authority.APPLICATION_AGENT if required else Authority.WORKSPACE
        ),
        scope=ContextScope(kinds=(ScopeKind.TASK,), task_id="parent"),
        lifetime=Lifetime.TASK,
        priority=1,
        required=required,
        trust=Trust.TRUSTED,
        stability=Stability.SESSION_STABLE,
        token_limit=None,
        reducer=None,
        source=ContextSource(
            kind=SourceKind.AGENT,
            uri=f"test://{item_id}",
            version="v1",
        ),
        version="v1",
        activation_reason="test",
    )


def test_amni_final_system_uses_known_low_trust_semantics():
    messages, _ = adapt_agent_final_request(
        messages=({"role": "system", "content": "folded"},),
        tools=(),
        source_identity="model-final://test",
        task_id="task",
        task_epoch=1,
        agent_id="agent",
        amni_folded_system=True,
    )

    item = messages.items[0]
    assert item.authority is Authority.APPLICATION_AGENT
    assert item.trust is Trust.USER_CONTROLLED


def test_latest_tool_turn_is_atomic_required_and_history_is_optional():
    messages, _ = adapt_agent_final_request(
        messages=(
            {"role": "user", "content": "start"},
            {
                "role": "assistant",
                "tool_calls": [{"id": "old", "function": {"name": "read"}}],
            },
            {"role": "tool", "tool_call_id": "old", "content": "old result"},
            {
                "role": "assistant",
                "tool_calls": [{"id": "current", "function": {"name": "read"}}],
            },
            {"role": "tool", "tool_call_id": "current", "content": "current result"},
        ),
        tools=(),
        source_identity="model-final://tool-turns",
        task_id="task",
        task_epoch=1,
        agent_id="agent",
        amni_folded_system=False,
    )

    old_assistant, old_tool = messages.items[1:3]
    current_assistant, current_tool = messages.items[3:5]
    assert old_assistant.required is False
    assert old_tool.required is False
    assert current_assistant.required is True
    assert current_tool.required is True
    assert old_assistant.source.ref["atomic_group_id"] == old_tool.source.ref["atomic_group_id"]
    assert current_assistant.source.ref["atomic_group_id"] == current_tool.source.ref["atomic_group_id"]


def test_scoped_instruction_loader_distinguishes_global_nested_and_path(tmp_path):
    global_file = tmp_path / "global" / "AWORLD.md"
    workspace = tmp_path / "workspace"
    nested = workspace / "src" / "service"
    global_file.parent.mkdir()
    nested.mkdir(parents=True)
    global_file.write_text("global rule", encoding="utf-8")
    (workspace / "AWORLD.md").write_text("workspace rule", encoding="utf-8")
    (workspace / "src" / "AWORLD.md").write_text(
        "---\npaths: src/**\n---\npath rule", encoding="utf-8"
    )
    (nested / "AWORLD.md").write_text("nested rule", encoding="utf-8")

    result = ScopedInstructionLoader().load(
        workspace=workspace,
        active_path=nested / "main.py",
        task_epoch=4,
        global_instruction=global_file,
    )

    by_content = {item.payload["content"]: item for item in result.items}
    assert by_content["global rule"].scope.kinds == (ScopeKind.GLOBAL,)
    assert by_content["global rule"].lifetime is Lifetime.INSTALLATION
    assert ScopeKind.PATH_PATTERN in by_content["path rule"].scope.kinds
    assert ScopeKind.DIRECTORY in by_content["nested rule"].scope.kinds


def test_progressive_skill_risk_gate_and_task_sticky_catalog_contraction():
    entry = SkillIndexEntry(
        skill_id="deploy",
        name="Deploy",
        description="Deploy safely",
        trigger_codes=("deploy_requested",),
        risk="write",
        estimated_tokens=50,
        version="v1",
    )
    descriptor = SkillDescriptor(
        index=entry,
        required_tools=("shell",),
        resource_refs=(),
        content_hash=canonical_json_hash({"content": "deploy"}),
    )
    blocked = route_skills(
        (entry,), (descriptor,), explicit_skill_ids=("deploy",)
    )[0]
    active = route_skills(
        (entry,),
        (descriptor,),
        explicit_skill_ids=("deploy",),
        allowed_risks=("write",),
        content_available_ids=("deploy",),
    )[0]
    assert blocked.level is DisclosureLevel.INDEX and not blocked.activated
    assert active.level is DisclosureLevel.CONTENT and active.activated

    def catalog(*ids: str) -> TaskCatalogSnapshot:
        return TaskCatalogSnapshot.build(
            7,
            (
                ToolCatalogEntry(
                    tool_id=value,
                    schema={"name": value},
                    schema_version="v1",
                    source="test",
                    estimated_tokens=1,
                )
                for value in ids
            ),
        )

    previous = catalog("read", "write")
    transition = transition_task_catalog(
        previous,
        catalog("read", "new"),
        action=CatalogChangeAction.DEFER_NEXT_EPOCH,
    )
    assert [entry.tool_id for entry in transition.snapshot.entries] == ["read"]
    assert transition.added == ("new",)
    assert transition.removed == ("write",)


def test_provider_wire_cache_continuity_consumes_lifecycle_breaks():
    context = Context(task_id="cache-task")

    def identity(request_id: str):
        prefix = b'{"messages":['
        request = prefix + b']}'
        evidence = SerializedPrefixEvidence.provider_wire(
            serialized_prefix=prefix,
            serialized_request=request,
            provider_name="openai",
            adapter_identity="test-openai",
            serialization_version="v1",
            request_id=request_id,
        )
        return build_cache_identity(
            inference_profile=_profile(),
            policy_version="v1",
            tool_catalog_hash=canonical_json_hash([]),
            skill_set_hash=canonical_json_hash([]),
            serialized_prefix_evidence=evidence,
        )

    assert context.commit_provider_cache_identity(identity("r1"))["status"] == (
        "initialized"
    )
    assert context.commit_provider_cache_identity(identity("r2"))["status"] == (
        "continued"
    )
    context.advance_context_lifecycle(LifecycleAction.CHECKPOINT)
    broken = context.commit_provider_cache_identity(identity("r3"))
    assert broken["status"] == "broken"
    assert broken["break_reasons"] == ["history_compaction"]


def test_tool_output_boundary_offloads_and_retrieves_raw_bytes(tmp_path):
    context = Context(task_id="tool-output", workspace_path=str(tmp_path))
    context.configure_tool_output_boundary(
        ToolOutputPolicy(
            max_inline_tokens=64,
            mode=ToolOutputMode.HEAD_TAIL,
            preserve_fields=("head", "tail", "artifact_ref"),
            tail_tokens=16,
            artifact_retention="task",
            policy_version="v1",
        )
    )
    action = SimpleNamespace(tool_call_id="call-1")
    raw = "start-" + ("x" * 5000) + "-end"
    result = SimpleNamespace(content=raw, metadata={})
    step_result = (SimpleNamespace(action_result=[result]),)

    plans = prepare_tool_output_plans(context, (action,))
    enforce_tool_output_boundary(step_result, (action,), context, plans)

    record = context.get_tool_output_records()[0]
    assert record.artifact is not None
    assert result.content["artifact_ref"] == record.artifact.ref
    assert context.read_tool_output_artifact(record.artifact.ref) == raw.encode()


def test_delegation_pack_isolates_epoch_tools_and_parent_revalidates_schema():
    mandatory = _item("policy", required=True)
    selected = _item("selected")
    spec = DelegationSpec(
        objective="inspect evidence",
        context_item_ids=("selected",),
        allowed_tools=("read", "write"),
        token_budget=1000,
        max_output_tokens=200,
        max_turns=3,
        max_depth=2,
        deadline=None,
        expected_output_schema={
            "type": "object",
            "required": ["answer"],
            "properties": {"answer": {"type": "string"}},
            "additionalProperties": False,
        },
        inference_profile=_profile(),
        stop_conditions=(StopCondition("objective_satisfied"),),
        merge_policy=MergePolicy.ANSWER_EVIDENCE,
    )
    pack = ContextPack.build(
        spec=spec,
        available_items=(mandatory, selected),
        parent_allowed_tools=("read",),
        child_declared_tools=("read", "write"),
        parent_task_epoch=3,
        child_depth=1,
        child_task_id="child",
        child_task_epoch=0,
    )
    assert pack.allowed_tools == ("read",)
    assert {item.source.ref["parent_item_id"] for item in pack.items} == {
        "policy",
        "selected",
    }
    assert all(item.task_epoch == 0 for item in pack.items)
    assert all(item.scope.child_task_id == "child" for item in pack.items)

    invalid = ChildResult(
        status=ChildStatus.SUCCEEDED,
        answer={"answer": 42},
        evidence=({"source": "child"},),
        artifacts=(),
        context_delta=(),
        usage=ChildUsage(input_tokens=10, output_tokens=5, turns=1),
        schema_validated=True,
    )
    merged = merge_child_result(spec, invalid)
    assert not merged.schema_validated
    assert merged.answer is None and merged.evidence == ()


def test_completion_contract_keeps_agent_claim_separate_from_evidence():
    context = Context(task_id="completion")
    context.configure_completion_contract(
        CompletionContract(
            required_artifacts=(
                ArtifactRequirement("report", "report.json"),
            ),
            immutable_inputs=(),
            validation_commands=(),
            max_evidence_age_seconds=60,
            required_final_evidence=("final_answer",),
            max_repairs=0,
        ),
        mode=CompletionMode.ENFORCE,
    )
    missing = context.assess_completion_contract(agent_claimed_finished=True)
    assert missing.status is CompletionStatus.FAILED
    assert set(missing.reason_codes) == {
        "required_artifact_missing",
        "final_evidence_missing",
    }
    context.record_completion_artifact(
        ArtifactEvidence(
            requirement_id="report",
            exists=True,
            content_hash=canonical_json_hash({"ok": True}),
            observed_at=datetime.now(timezone.utc),
        )
    )
    context.record_completion_final_evidence("final_answer")
    satisfied = context.assess_completion_contract(agent_claimed_finished=True)
    assert satisfied.status is CompletionStatus.SATISFIED
