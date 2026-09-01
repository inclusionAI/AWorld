from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib

import pytest

from aworld.agents.final_context_adapter import adapt_agent_final_request
from aworld.core.context.compiler import (
    AdapterResult,
    AttributionCollection,
    AttributionOwnerCode,
    AttributionSerialization,
    AttributionCollectionShape,
    ProviderToolsLowering,
    Authority,
    BudgetAllocationTier,
    ContextEmissionKind,
    ContextInputBudget,
    ContextItem,
    ContextKind,
    ContextEmissionIntent,
    ContextObservationSidecar,
    ContextScope,
    ContextSource,
    FinalCompileCandidate,
    FinalCompileInput,
    FinalCompilePolicy,
    InferenceProfile,
    Lifetime,
    LogicalResidency,
    ModelResidency,
    ProviderAttributionMismatch,
    ProviderCacheMaterial,
    ProviderCandidateEnvelope,
    ProviderLoweringCapability,
    ProviderLoweringReceipt,
    ProviderRequestFidelity,
    ProviderRequestSnapshot,
    RequestCaptureStage,
    SerializedPrefixEvidence,
    ScopeKind,
    SourceKind,
    Stability,
    TokenEstimate,
    Trust,
    build_provider_attribution_receipt,
    build_observed_model_boundary_attribution_plan,
    build_cache_identity,
    build_unknown_attribution_plan,
    canonical_json_bytes,
    canonical_json_hash,
    compile_final_context,
    compile_model_boundary_context,
    inspect_final_context,
)


def _policy() -> FinalCompilePolicy:
    return FinalCompilePolicy(
        compiler_version="attribution-test-v1",
        policy_version="policy-v1",
        input_budget=ContextInputBudget(10000, 100, 10, 10, 5000),
    )


def _profile() -> InferenceProfile:
    return InferenceProfile(
        provider="openai",
        model="gpt-test",
        reasoning_effort=None,
        execution_mode="chat_completions",
        context_limit=10000,
    )


def _item(
    item_id: str,
    payload: dict,
    *,
    kind: ContextKind,
    stability: Stability,
    occurrence: int,
) -> ContextItem:
    return ContextItem(
        id=item_id,
        kind=kind,
        payload=payload,
        task_epoch=1,
        authority=Authority.APPLICATION_AGENT,
        scope=ContextScope(kinds=(ScopeKind.TASK,), task_id="task"),
        lifetime=Lifetime.TASK,
        priority=occurrence,
        required=True,
        trust=Trust.TRUSTED,
        stability=stability,
        token_limit=None,
        reducer=None,
        source=ContextSource(kind=SourceKind.AGENT),
        occurrence=occurrence,
    )


def test_final_plan_preserves_duplicate_occurrences_and_actual_residency():
    duplicate = {"role": "user", "content": "same"}
    items = (
        _item("stable", {"role": "system", "content": "rules"}, kind=ContextKind.SYSTEM, stability=Stability.STABLE, occurrence=0),
        _item("dynamic", duplicate, kind=ContextKind.USER, stability=Stability.TURN_DYNAMIC, occurrence=1),
        _item("declared-stable-after-dynamic", duplicate, kind=ContextKind.USER, stability=Stability.STABLE, occurrence=2),
    )
    result = compile_final_context(
        compiler_input=FinalCompileInput(
            request_id="request-1",
            provider_name="openai",
            provider_params={},
            candidates=tuple(
                FinalCompileCandidate(
                    item=item,
                    tokens=TokenEstimate(1, "test-v1", False),
                    allocation_tier=BudgetAllocationTier(0, "required"),
                    emission=ContextEmissionKind.MESSAGE,
                    semantics_proven=True,
                    owner_code=AttributionOwnerCode.MODEL_FINAL_MESSAGES,
                )
                for item in items
            ),
            inference_profile=_profile(),
            created_at=datetime.now(timezone.utc),
            task_id="task",
            task_epoch=1,
        ),
        policy=_policy(),
    )

    entries = result.attribution_plan.entries
    assert [entry.ordinal for entry in entries] == [0, 1, 2]
    assert entries[1].content_hash == entries[2].content_hash
    assert [entry.residency for entry in entries] == [
        LogicalResidency.STABLE,
        LogicalResidency.DYNAMIC,
        LogicalResidency.DYNAMIC,
    ]
    assert "rules" not in repr(result.attribution_plan.to_redacted_dict())
    assert result.attribution_plan.fingerprint == canonical_json_hash(
        result.attribution_plan.fingerprint_payload()
    )
    inspected = inspect_final_context(result)
    assert inspected["attribution"]["entry_count"] == 3
    assert inspected["attribution"]["plan_fingerprint"] == result.attribution_plan.fingerprint
    assert "rules" not in repr(inspected["attribution"])


def test_runtime_binds_only_current_model_collection_ordinal_not_hash():
    messages = ({"role": "user", "content": "same"},) * 2
    tools = ({"role": "user", "content": "same"},)
    message_result, tool_result = adapt_agent_final_request(
        messages=messages,
        tools=tools,
        source_identity="model-final://agent/task-task/epoch-1/request-request-2",
        task_id="task",
        task_epoch=1,
        agent_id="agent",
        amni_folded_system=False,
    )
    observations = (
        ContextObservationSidecar.from_adapter_result(
            owner="model.final_messages", namespace="agent", source_identity="opaque-message-provenance", result=message_result,
            request_id_hash=canonical_json_hash({"request_id": "request-2"}), collection=AttributionCollection.MESSAGES, task_epoch=1,
        ),
        ContextObservationSidecar.from_adapter_result(
            owner="model.final_tool_catalog", namespace="agent", source_identity="opaque-tool-provenance", result=tool_result,
            request_id_hash=canonical_json_hash({"request_id": "request-2"}), collection=AttributionCollection.TOOLS, task_epoch=1,
        ),
    )
    legacy = ProviderRequestSnapshot(
        request_id="request-2",
        provider_name="openai",
        payload={"messages": messages, "tools": tools, "params": {}},
        capture_stage=RequestCaptureStage.MODEL_BOUNDARY,
        fidelity=ProviderRequestFidelity.MODEL_BOUNDARY,
    )
    result = compile_model_boundary_context(
        legacy_request=legacy,
        observations=observations,
        inference_profile=_profile(),
        policy=_policy(),
        created_at=datetime.now(timezone.utc),
        task_id="task",
        session_id=None,
        trace_id=None,
        task_epoch=1,
    )
    assert [entry.owner_code for entry in result.attribution_plan.entries] == [
        AttributionOwnerCode.MODEL_FINAL_MESSAGES,
        AttributionOwnerCode.MODEL_FINAL_MESSAGES,
        AttributionOwnerCode.MODEL_FINAL_TOOL_CATALOG,
    ]
    assert [entry.collection for entry in result.attribution_plan.entries] == [
        AttributionCollection.MESSAGES,
        AttributionCollection.MESSAGES,
        AttributionCollection.TOOLS,
    ]

    wrong_first = replace(message_result.items[0], occurrence=1)
    wrong = ContextObservationSidecar.from_adapter_result(
        owner="model.final_messages",
        namespace="agent",
        source_identity="model-final://agent/task-task/epoch-1/request-request-2",
        result=AdapterResult(
            items=(wrong_first, message_result.items[1]), diagnostics=()
        ),
        request_id_hash=canonical_json_hash({"request_id": "request-2"}),
        collection=AttributionCollection.MESSAGES,
        task_epoch=1,
    )
    blocked = compile_model_boundary_context(
        legacy_request=legacy,
        observations=(wrong, observations[1]),
        inference_profile=_profile(),
        policy=_policy(),
        created_at=datetime.now(timezone.utc),
        task_id="task",
        session_id=None,
        trace_id=None,
        task_epoch=1,
    )
    assert blocked.enforce_ready is False
    assert "source_lowering_unproven" in blocked.blocker_codes
    assert blocked.attribution_plan.entries[0].owner_code is AttributionOwnerCode.UNKNOWN


def test_observed_plan_binds_duplicate_occurrences_and_marks_missing_owner_unknown():
    messages = ({"role": "user", "content": "same"},) * 2
    legacy = ProviderRequestSnapshot(
        request_id="observed-duplicates",
        provider_name="openai",
        payload={"messages": messages, "tools": None, "params": {}},
        capture_stage=RequestCaptureStage.MODEL_BOUNDARY,
        fidelity=ProviderRequestFidelity.MODEL_BOUNDARY,
    )
    message_result, _ = adapt_agent_final_request(
        messages=messages,
        tools=(),
        source_identity="model-final://agent/task-task/epoch-1/request-observed-duplicates",
        task_id="task",
        task_epoch=1,
        agent_id="agent",
        amni_folded_system=False,
    )
    sidecar = ContextObservationSidecar.from_adapter_result(
        owner="model.final_messages",
        namespace="agent",
        source_identity="opaque",
        result=message_result,
        request_id_hash=canonical_json_hash({"request_id": "observed-duplicates"}),
        collection=AttributionCollection.MESSAGES,
        task_epoch=1,
    )

    bound = build_observed_model_boundary_attribution_plan(
        observed_request=legacy, observations=(sidecar,), task_epoch=1
    )
    missing = build_observed_model_boundary_attribution_plan(
        observed_request=legacy, observations=(), task_epoch=1
    )

    assert [entry.ordinal for entry in bound.entries] == [0, 1]
    assert [entry.owner_code for entry in bound.entries] == [
        AttributionOwnerCode.MODEL_FINAL_MESSAGES,
        AttributionOwnerCode.MODEL_FINAL_MESSAGES,
    ]
    assert all(entry.residency is LogicalResidency.UNKNOWN for entry in bound.entries)
    assert all(entry.owner_code is AttributionOwnerCode.UNKNOWN for entry in missing.entries)


def test_additional_sidecar_requires_typed_not_resident_message_intent():
    payload = {"role": "system", "content": "same instruction"}
    legacy = ProviderRequestSnapshot(
        request_id="residency",
        provider_name="openai",
        payload={"messages": (payload,), "tools": None, "params": {}},
        capture_stage=RequestCaptureStage.MODEL_BOUNDARY,
        fidelity=ProviderRequestFidelity.MODEL_BOUNDARY,
    )
    instruction = _item(
        "instruction",
        payload,
        kind=ContextKind.INSTRUCTION,
        stability=Stability.STABLE,
        occurrence=0,
    )
    default_sidecar = ContextObservationSidecar.from_adapter_result(
        owner="workspace.nested_instructions",
        namespace="workspace",
        source_identity="workspace",
        result=AdapterResult(items=(instruction,), diagnostics=()),
    )
    explicit_sidecar = ContextObservationSidecar.from_adapter_result(
        owner="workspace.nested_instructions",
        namespace="workspace",
        source_identity="workspace",
        result=AdapterResult(items=(instruction,), diagnostics=()),
        model_residency=ModelResidency.NOT_RESIDENT,
        emission_intent=ContextEmissionIntent.MESSAGE,
    )

    default_result = compile_model_boundary_context(
        legacy_request=legacy,
        observations=(default_sidecar,),
        inference_profile=_profile(), policy=_policy(),
        created_at=datetime.now(timezone.utc), task_id="task",
        session_id=None, trace_id=None, task_epoch=1,
    )
    explicit_result = compile_model_boundary_context(
        legacy_request=legacy,
        observations=(explicit_sidecar,),
        inference_profile=_profile(), policy=_policy(),
        created_at=datetime.now(timezone.utc), task_id="task",
        session_id=None, trace_id=None, task_epoch=1,
    )

    assert len(default_result.request_snapshot.payload["messages"]) == 1
    assert len(explicit_result.request_snapshot.payload["messages"]) == 2


@pytest.mark.parametrize("tools", [None, []])
def test_provider_receipt_conserves_unicode_canonical_bytes_without_text_search(tools):
    secret = '私密🙂 "quote" \\ path'
    snapshot = ProviderRequestSnapshot(
        request_id="request-3",
        provider_name="openai",
        payload={
            "messages": [
                {"role": "user", "content": secret},
                {"role": "user", "content": secret},
            ],
            "tools": tools,
            "params": {"temperature": 0},
        },
        capture_stage=RequestCaptureStage.MODEL_BOUNDARY,
        fidelity=ProviderRequestFidelity.MODEL_BOUNDARY,
    )
    plan = build_unknown_attribution_plan(snapshot)
    provider_request = {
        "messages": snapshot.thaw()["messages"],
        "tools": tools,
        "temperature": 0,
        "model": "gpt-test",
    }
    body = canonical_json_bytes(provider_request)
    receipt = build_provider_attribution_receipt(
        plan=plan,
        provider_request=provider_request,
        serialization=AttributionSerialization.HTTP_SERIALIZED_CANONICAL_JSON,
        canonical_request_body=body,
    )

    assert receipt.total_canonical_bytes == len(body)
    assert receipt.attributed_value_bytes + receipt.provider_envelope_and_params_bytes == len(body)
    assert len(receipt.entries) == 2
    assert secret not in repr(receipt.to_redacted_dict())


def test_provider_reorder_add_drop_or_transform_is_attribution_mismatch():
    snapshot = ProviderRequestSnapshot(
        request_id="request-4",
        provider_name="openai",
        payload={
            "messages": [
                {"role": "user", "content": "one"},
                {"role": "user", "content": "two"},
            ],
            "tools": None,
            "params": {},
        },
        capture_stage=RequestCaptureStage.MODEL_BOUNDARY,
        fidelity=ProviderRequestFidelity.MODEL_BOUNDARY,
    )
    plan = build_unknown_attribution_plan(snapshot)
    for messages in (
        [snapshot.thaw()["messages"][1], snapshot.thaw()["messages"][0]],
        [*snapshot.thaw()["messages"], {"role": "user", "content": "three"}],
        [snapshot.thaw()["messages"][0]],
        [{"role": "user", "content": "changed"}, snapshot.thaw()["messages"][1]],
    ):
        with pytest.raises(ProviderAttributionMismatch):
            build_provider_attribution_receipt(
                plan=plan,
                provider_request={"messages": messages, "model": "gpt-test"},
                serialization=AttributionSerialization.PROVIDER_PREPARED_CANONICAL_JSON,
            )


def test_collection_shape_does_not_conflate_absent_null_and_empty_tools():
    null_snapshot = ProviderRequestSnapshot(
        request_id="shape-null",
        provider_name="openai",
        payload={"messages": [], "tools": None, "params": {}},
        capture_stage=RequestCaptureStage.MODEL_BOUNDARY,
        fidelity=ProviderRequestFidelity.MODEL_BOUNDARY,
    )
    null_plan = build_unknown_attribution_plan(null_snapshot)
    assert null_plan.messages_count == 0
    assert null_plan.tools_shape is AttributionCollectionShape.NULL
    assert null_plan.tools_count is None
    with pytest.raises(ProviderAttributionMismatch):
        build_provider_attribution_receipt(
            plan=null_plan,
            provider_request={"messages": [], "tools": []},
            serialization=AttributionSerialization.PROVIDER_PREPARED_CANONICAL_JSON,
            tools_lowering=ProviderToolsLowering.NULL_TO_ABSENT,
        )
    build_provider_attribution_receipt(
        plan=null_plan,
        provider_request={"messages": []},
        serialization=AttributionSerialization.PROVIDER_PREPARED_CANONICAL_JSON,
        tools_lowering=ProviderToolsLowering.NULL_TO_ABSENT,
    )
    with pytest.raises(ProviderAttributionMismatch):
        build_provider_attribution_receipt(
            plan=null_plan,
            provider_request={"messages": []},
            serialization=AttributionSerialization.PROVIDER_PREPARED_CANONICAL_JSON,
            tools_lowering=ProviderToolsLowering.PRESERVE,
        )

    empty_snapshot = ProviderRequestSnapshot(
        request_id="shape-empty",
        provider_name="openai",
        payload={"messages": [], "tools": [], "params": {}},
        capture_stage=RequestCaptureStage.MODEL_BOUNDARY,
        fidelity=ProviderRequestFidelity.MODEL_BOUNDARY,
    )
    empty_plan = build_unknown_attribution_plan(empty_snapshot)
    assert empty_plan.tools_shape is AttributionCollectionShape.ARRAY
    assert empty_plan.tools_count == 0
    with pytest.raises(ProviderAttributionMismatch):
        build_provider_attribution_receipt(
            plan=empty_plan,
            provider_request={"messages": []},
            serialization=AttributionSerialization.PROVIDER_PREPARED_CANONICAL_JSON,
            tools_lowering=ProviderToolsLowering.NULL_TO_ABSENT,
        )


def test_legacy_public_constructors_allow_missing_attribution_but_enforce_rejects_it():
    candidate = ProviderRequestSnapshot(
        request_id="legacy-envelope",
        provider_name="openai",
        payload={"messages": [], "tools": None, "params": {}},
        capture_stage=RequestCaptureStage.MODEL_BOUNDARY,
        fidelity=ProviderRequestFidelity.MODEL_BOUNDARY,
    )
    lowering = ProviderLoweringCapability(
        provider_name="openai",
        adapter_identity="test.openai",
        adapter_version="v1",
        request_projection="test.params.v1",
    )
    envelope = ProviderCandidateEnvelope(
        candidate_request=candidate,
        compiler_identity="aworld.context.compiler.framework",
        compiler_version="legacy-v1",
        expected_lowering=lowering,
    )
    assert envelope.attribution_plan is None
    prepared = ProviderRequestSnapshot(
        request_id="legacy-envelope",
        provider_name="openai",
        payload={"messages": [], "model": "gpt-test"},
        capture_stage=RequestCaptureStage.PROVIDER_PREPARED,
        fidelity=ProviderRequestFidelity.PROVIDER_PREPARED,
    )
    receipt = ProviderLoweringReceipt(
        candidate_content_hash=candidate.content_hash,
        provider_request=prepared,
        lowering=lowering,
    )
    assert receipt.attribution is None


def test_lowering_serialized_evidence_binds_snapshot_serialized_checksum():
    candidate = ProviderRequestSnapshot(
        request_id="request-5",
        provider_name="openai",
        payload={
            "messages": [{"role": "user", "content": "one"}],
            "tools": None,
            "params": {},
        },
        capture_stage=RequestCaptureStage.MODEL_BOUNDARY,
        fidelity=ProviderRequestFidelity.MODEL_BOUNDARY,
    )
    plan = build_unknown_attribution_plan(candidate)
    lowering = ProviderLoweringCapability(
        provider_name="openai",
        adapter_identity="test.openai",
        adapter_version="v1",
        request_projection="test.params.v1",
    )
    cache_material = ProviderCacheMaterial(
        inference_profile=_profile(),
        policy_version="policy-v1",
        tool_catalog_hash="sha256:" + "1" * 64,
        skill_set_hash="sha256:" + "2" * 64,
        logical_stable_prefix_hash="sha256:" + "3" * 64,
        stable_message_count=0,
    )
    envelope = ProviderCandidateEnvelope(
        candidate_request=candidate,
        compiler_identity="aworld.context.compiler.framework",
        compiler_version="test-v1",
        expected_lowering=lowering,
        attribution_plan=plan,
        cache_material=cache_material,
    )
    provider_payload = {"messages": candidate.thaw()["messages"], "model": "gpt"}
    serialized = b'{ "messages" : [{"content":"one","role":"user"}], "model" : "gpt" }'
    serialized_checksum = "sha256:" + hashlib.sha256(serialized).hexdigest()
    provider_snapshot = ProviderRequestSnapshot(
        request_id="request-5",
        provider_name="openai",
        payload=provider_payload,
        capture_stage=RequestCaptureStage.PROVIDER_PREPARED,
        fidelity=ProviderRequestFidelity.PROVIDER_PREPARED,
        serialized_checksum=serialized_checksum,
    )
    evidence = SerializedPrefixEvidence.provider_wire(
        serialized_prefix=serialized[:1],
        serialized_request=serialized,
        provider_name="openai",
        adapter_identity="test.openai",
        serialization_version="noncanonical-test-v1",
        request_id="request-5",
    )
    cache_identity = build_cache_identity(
        inference_profile=_profile(),
        policy_version="policy-v1",
        tool_catalog_hash=cache_material.tool_catalog_hash,
        skill_set_hash=cache_material.skill_set_hash,
        serialized_prefix_evidence=evidence,
    )
    attribution = build_provider_attribution_receipt(
        plan=plan,
        provider_request=provider_payload,
        serialization=AttributionSerialization.PROVIDER_PREPARED_CANONICAL_JSON,
        tools_lowering=ProviderToolsLowering.NULL_TO_ABSENT,
    )

    receipt = ProviderLoweringReceipt.from_envelope(
        envelope=envelope,
        provider_request=provider_snapshot,
        lowering=lowering,
        attribution=attribution,
        serialized_prefix_evidence=evidence,
        cache_identity=cache_identity,
    )

    assert receipt.serialized_prefix_evidence.request_serialized_checksum == serialized_checksum
    assert receipt.provider_request.serialized_checksum == serialized_checksum
    assert receipt.provider_request.content_hash != serialized_checksum
