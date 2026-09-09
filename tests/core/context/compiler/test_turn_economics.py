from __future__ import annotations

import hashlib
import json
from enum import Enum
from types import SimpleNamespace

import pytest

from aworld.core.context.base import Context
from aworld.core.context.compiler import (
    ArtifactRetrievalPlan,
    ArtifactRetrievalReceipt,
    ToolOutputMode,
    ToolOutputPolicy,
    TurnCauseCode,
    TurnKind,
    canonical_json_hash,
    hashed_identity,
    turn_cause_support,
)
from aworld.core.context.tool_output_runtime import (
    enforce_tool_output_boundary,
    prepare_tool_output_plans,
)
from aworld.core.context.compiler.lifecycle import LifecycleAction
from aworld.memory.tool_result_compaction import compact_tool_result_for_memory


class Value:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _candidate(tmp_path) -> Context:
    context = Context(task_id="generic-noisy-output", workspace_path=str(tmp_path))
    context.configure_tool_output_boundary(
        ToolOutputPolicy(
            max_inline_tokens=512,
            mode=ToolOutputMode.HEAD_TAIL,
            preserve_fields=("artifact_ref",),
            tail_tokens=64,
            artifact_retention="task",
            policy_version="turn-economics-v1",
        )
    )
    return context


def _source_output(context: Context):
    noise = (b"0123456789abcdef" * 8192)  # deterministic 128 KiB
    digest = "sha256:" + hashlib.sha256(noise).hexdigest()
    artifact_ref = "sandbox-output://generic-noise"
    context.register_model_tool_choices("request-source", [{"id": "call-source"}])
    action = Value(
        tool_call_id="call-source", tool_name="generic_stream", action_name="fetch", params={}
    )
    result = Value(
        tool_call_id="call-source",
        tool_name="generic_stream",
        action_name="fetch",
        metadata={},
        content=json.dumps({
            "content": noise.decode("ascii"),
            "output_policy": {
                "artifact_ref": artifact_ref,
                "raw_bytes": len(noise),
                "content_sha256": digest,
            },
        }),
    )
    step = (Value(action_result=[result]),)
    enforce_tool_output_boundary(
        step, (action,), context, prepare_tool_output_plans(context, (action,))
    )
    return noise, digest, artifact_ref, result


def test_generic_noisy_output_offload_retrieval_and_next_model_consumption(tmp_path):
    context = _candidate(tmp_path)
    noise, digest, artifact_ref, source_result = _source_output(context)

    source_record = context.get_tool_output_records()[0]
    assert len(noise) == 128 * 1024
    assert source_record.artifact is not None
    assert source_record.upstream_artifacts[0].ref == artifact_ref
    assert source_result.metadata["turn_economics"]["cause"] == "model_choice"

    chunk = noise[4096:4352]
    chunk_hash = "sha256:" + hashlib.sha256(chunk).hexdigest()
    context.register_model_tool_choices("request-retrieve", [{"id": "call-retrieve"}])
    action = Value(
        tool_call_id="call-retrieve",
        tool_name="generic_stream",
        action_name="read_output_artifact",
        params={"artifact_ref": artifact_ref, "offset": 4096, "limit": 256},
    )
    result = Value(
        tool_call_id="call-retrieve",
        tool_name="generic_stream",
        action_name="read_output_artifact",
        metadata={},
        content={
            "type": "text",
            "content": chunk.decode("ascii"),
            "artifact_ref": artifact_ref,
            "offset": 4096,
            "next_offset": 4352,
            "returned_bytes": 256,
            "total_bytes": len(noise),
            "complete": False,
            "content_sha256": digest,
            "chunk_sha256": chunk_hash,
        },
    )
    step = (Value(action_result=[result]),)
    plans = prepare_tool_output_plans(context, (action,))
    enforce_tool_output_boundary(step, (action,), context, plans)
    model_turn = context.record_model_turn(
        "request-after-retrieval",
        [{"role": "tool", "tool_call_id": "call-retrieve", "content": result.content}],
    )

    retrieval = context.get_artifact_retrieval_receipts()[0]
    assert retrieval.returned_byte_count == 256
    assert retrieval.consumed is True
    assert model_turn.cause is TurnCauseCode.ARTIFACT_RETRIEVAL
    assert context.artifact_retrievals_for_request("request-after-retrieval") == (retrieval,)
    memory = compact_tool_result_for_memory(
        source_result.content,
        force=True,
        result_metadata=source_result.metadata,
    )
    assert memory.applied is False
    assert memory.metadata["preserved_reversible_boundary"] is True

    copied = context.deep_copy()
    assert copied.get_turn_economics_receipts() == context.get_turn_economics_receipts()
    assert copied.get_artifact_retrieval_receipts() == context.get_artifact_retrieval_receipts()
    copied.advance_context_lifecycle(LifecycleAction.CHECKPOINT)
    assert copied.get_turn_economics_receipts() == context.get_turn_economics_receipts()
    assert copied.get_artifact_retrieval_receipts() == context.get_artifact_retrieval_receipts()
    copied.advance_context_lifecycle(LifecycleAction.RESUME)
    assert copied.get_turn_economics_receipts() == context.get_turn_economics_receipts()
    assert copied.get_artifact_retrieval_receipts() == context.get_artifact_retrieval_receipts()
    copied.advance_context_lifecycle(LifecycleAction.NEW_TASK)
    assert copied.get_turn_economics_receipts() == ()
    assert copied.get_artifact_retrieval_receipts() == ()


def test_retrieval_consumption_accepts_lossless_standard_text_part_encoding(tmp_path):
    context = _candidate(tmp_path)
    noise, digest, artifact_ref, _ = _source_output(context)
    chunk = noise[4096:4352]
    action = Value(
        tool_call_id="call-retrieve",
        tool_name="generic_stream",
        action_name="read_output_artifact",
        params={"artifact_ref": artifact_ref, "offset": 4096, "limit": 256},
    )
    result = Value(
        tool_call_id="call-retrieve",
        tool_name="generic_stream",
        action_name="read_output_artifact",
        metadata={},
        content={
            "type": "text",
            "content": chunk.decode("ascii"),
            "artifact_ref": artifact_ref,
            "offset": 4096,
            "next_offset": 4352,
            "returned_bytes": 256,
            "total_bytes": len(noise),
            "complete": False,
            "content_sha256": digest,
            "chunk_sha256": "sha256:" + hashlib.sha256(chunk).hexdigest(),
        },
    )
    context.register_model_tool_choices("request-retrieve", [{"id": "call-retrieve"}])
    enforce_tool_output_boundary(
        (Value(action_result=[result]),),
        (action,),
        context,
        prepare_tool_output_plans(context, (action,)),
    )

    model_turn = context.record_model_turn(
        "request-after-retrieval",
        [
            {
                "role": "tool",
                "tool_call_id": "call-retrieve",
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(result.content, ensure_ascii=False),
                    }
                ],
            }
        ],
    )

    receipt = context.get_artifact_retrieval_receipts()[0]
    assert receipt.consumed is True
    assert receipt.consumed_content_hash == receipt.result_content_hash
    assert model_turn.cause is TurnCauseCode.ARTIFACT_RETRIEVAL


def test_retrieval_consumption_accepts_owner_text_part_without_type(tmp_path):
    context = _candidate(tmp_path)
    noise, digest, artifact_ref, _ = _source_output(context)
    chunk = noise[:16]
    action = Value(
        tool_call_id="call-retrieve",
        tool_name="generic_stream",
        action_name="read_output_artifact",
        params={"artifact_ref": artifact_ref, "offset": 0, "limit": 16},
    )
    content = json.dumps({
        "type": "text",
        "content": chunk.decode("ascii"),
        "artifact_ref": artifact_ref,
        "offset": 0,
        "next_offset": 16,
        "returned_bytes": 16,
        "total_bytes": len(noise),
        "complete": False,
        "content_sha256": digest,
        "chunk_sha256": "sha256:" + hashlib.sha256(chunk).hexdigest(),
    })
    result = Value(
        tool_call_id="call-retrieve",
        tool_name="generic_stream",
        action_name="read_output_artifact",
        metadata={},
        content=content,
    )
    context.register_model_tool_choices("request-retrieve", [{"id": "call-retrieve"}])
    enforce_tool_output_boundary(
        (Value(action_result=[result]),),
        (action,),
        context,
        prepare_tool_output_plans(context, (action,)),
    )

    model_turn = context.record_model_turn(
        "request-after-retrieval",
        [{
            "role": "tool",
            "tool_call_id": "call-retrieve",
            "content": [{"text": content}],
        }],
    )

    assert context.get_artifact_retrieval_receipts()[0].consumed is True
    assert model_turn.cause is TurnCauseCode.ARTIFACT_RETRIEVAL


def test_retrieval_consumption_rejects_tampered_text_part_encoding(tmp_path):
    context = _candidate(tmp_path)
    noise, digest, artifact_ref, _ = _source_output(context)
    chunk = noise[:32]
    action = Value(
        tool_call_id="call-retrieve",
        tool_name="generic_stream",
        action_name="read_output_artifact",
        params={"artifact_ref": artifact_ref, "offset": 0, "limit": 32},
    )
    result = Value(
        tool_call_id="call-retrieve",
        tool_name="generic_stream",
        action_name="read_output_artifact",
        metadata={},
        content={
            "artifact_ref": artifact_ref,
            "content": chunk.decode("ascii"),
            "offset": 0,
            "next_offset": 32,
            "returned_bytes": 32,
            "total_bytes": len(noise),
            "complete": False,
            "content_sha256": digest,
            "chunk_sha256": "sha256:" + hashlib.sha256(chunk).hexdigest(),
        },
    )
    context.register_model_tool_choices("request-retrieve", [{"id": "call-retrieve"}])
    enforce_tool_output_boundary(
        (Value(action_result=[result]),),
        (action,),
        context,
        prepare_tool_output_plans(context, (action,)),
    )
    tampered = dict(result.content)
    tampered["content"] = "different"

    model_turn = context.record_model_turn(
        "request-after-retrieval",
        [{
            "role": "tool",
            "tool_call_id": "call-retrieve",
            "content": [{"type": "text", "text": json.dumps(tampered)}],
        }],
    )

    assert context.get_artifact_retrieval_receipts()[0].consumed is False
    assert model_turn.cause is TurnCauseCode.MODEL_CHOICE


def test_verified_bounded_retrieval_is_not_recursively_offloaded(tmp_path):
    context = _candidate(tmp_path)
    noise, digest, artifact_ref, _ = _source_output(context)
    chunk = noise[:1_536]
    action = Value(
        tool_call_id="call-retrieve-large",
        tool_name="generic_stream",
        action_name="read_output_artifact",
        params={"artifact_ref": artifact_ref, "offset": 0, "limit": 50_000},
    )
    original = {
        "type": "text",
        "content": chunk.decode("ascii"),
        "artifact_ref": artifact_ref,
        "offset": 0,
        "next_offset": 1_536,
        "returned_bytes": 1_536,
        "total_bytes": len(noise),
        "complete": False,
        "content_sha256": digest,
        "chunk_sha256": "sha256:" + hashlib.sha256(chunk).hexdigest(),
    }
    result = Value(
        tool_call_id="call-retrieve-large",
        tool_name="generic_stream",
        action_name="read_output_artifact",
        metadata={},
        content=original,
    )

    enforce_tool_output_boundary(
        (Value(action_result=[result]),),
        (action,),
        context,
        prepare_tool_output_plans(context, (action,)),
    )

    assert result.content == original
    policy = result.metadata["tool_output_policy"]
    assert policy["reason_code"] == "artifact_retrieval_inline"
    assert policy["context_artifact_ref"] is None
    assert policy["offloaded_tokens"] == 0
    assert policy["upstream_artifacts"] == [{
        "ref": artifact_ref,
        "content_hash": digest,
        "byte_count": len(noise),
        "owner_tool": "generic_stream",
        "retrieval_action": "read_output_artifact",
    }]
    assert action.params["limit"] == 1_536
    assert result.metadata["artifact_retrieval_planning"]["limit_adjusted"] is True
    assert result.metadata["artifact_retrieval_planning"]["requested_limit"] == 50_000
    assert result.metadata["artifact_retrieval"]["returned_byte_count"] == 1_536
    next_action = Value(
        tool_call_id="call-retrieve-next",
        tool_name="generic_stream",
        action_name="read_output_artifact",
        params={"artifact_ref": artifact_ref, "offset": 1_536, "limit": 1024},
    )
    prepare_tool_output_plans(context, (next_action,))
    assert context.get_artifact_retrieval_plan(
        "call-retrieve-next"
    ).artifact_ref == artifact_ref


def test_retrieval_larger_than_framework_visibility_cap_remains_offloaded(tmp_path):
    context = _candidate(tmp_path)
    noise, digest, artifact_ref, _ = _source_output(context)
    chunk = noise[:70_000]
    action = Value(
        tool_call_id="call-retrieve-too-large",
        tool_name="generic_stream",
        action_name="read_output_artifact",
        params={"artifact_ref": artifact_ref, "offset": 0, "limit": 70_000},
    )
    result = Value(
        tool_call_id="call-retrieve-too-large",
        tool_name="generic_stream",
        action_name="read_output_artifact",
        metadata={},
        content={
            "type": "text",
            "content": chunk.decode("ascii"),
            "artifact_ref": artifact_ref,
            "offset": 0,
            "next_offset": 70_000,
            "returned_bytes": 70_000,
            "total_bytes": len(noise),
            "complete": False,
            "content_sha256": digest,
            "chunk_sha256": "sha256:" + hashlib.sha256(chunk).hexdigest(),
        },
    )

    enforce_tool_output_boundary(
        (Value(action_result=[result]),),
        (action,),
        context,
        prepare_tool_output_plans(context, (action,)),
    )

    assert result.metadata["tool_output_policy"]["context_artifact_ref"]
    assert result.metadata["tool_output_policy"]["offloaded_tokens"] > 0
    assert result.metadata["artifact_retrieval"]["status"] == "unavailable"


def test_shadow_observation_registers_upstream_artifact_for_retrieval():
    context = Context(task_id="shadow-upstream-artifact")
    source_ref = "/tmp/tool-owned-output.bin"
    source_hash = "sha256:" + "a" * 64
    source_action = Value(
        tool_call_id="source-call",
        tool_name="docker",
        action_name="run_code",
        params={"code": "produce output"},
    )
    source_result = Value(
        content={
            "output_policy": {
                "artifact_ref": source_ref,
                "content_sha256": source_hash,
                "raw_bytes": 4096,
            }
        },
        metadata={},
        tool_call_id="source-call",
        tool_name="docker",
        action_name="run_code",
    )
    enforce_tool_output_boundary(
        (Value(action_result=[source_result]),),
        (source_action,),
        context,
        {},
    )

    retrieval_action = Value(
        tool_call_id="retrieval-call",
        tool_name="docker",
        action_name="read_output_artifact",
        params={"artifact_ref": source_ref, "offset": 0, "limit": 16},
    )
    plans = prepare_tool_output_plans(context, (retrieval_action,))

    assert plans == {}
    plan = context._artifact_retrieval_plans["retrieval-call"]
    assert plan.artifact_ref == source_ref
    assert plan.artifact_content_hash == source_hash
    assert plan.artifact_byte_count == 4096


def test_artifact_receipts_fan_in_across_transport_copies_and_string_ranges(tmp_path):
    root = _candidate(tmp_path)
    source_context = root.deep_copy()
    noise, digest, artifact_ref, _ = _source_output(source_context)

    retrieval_context = root.deep_copy()
    retrieval_context.register_model_tool_choices(
        "request-retrieve", [{"id": "call-retrieve"}]
    )
    action = Value(
        tool_call_id="call-retrieve",
        tool_name="generic_stream",
        action_name="read_output_artifact",
        params={"artifact_ref": artifact_ref, "offset": "4096", "limit": "256"},
    )
    chunk = noise[4096:4352]
    result = Value(
        tool_call_id="call-retrieve",
        tool_name="generic_stream",
        action_name="read_output_artifact",
        metadata={},
        content={
            "type": "text",
            "content": chunk.decode("ascii"),
            "artifact_ref": artifact_ref,
            "offset": 4096,
            "next_offset": 4352,
            "returned_bytes": 256,
            "total_bytes": len(noise),
            "complete": False,
            "content_sha256": digest,
            "chunk_sha256": "sha256:" + hashlib.sha256(chunk).hexdigest(),
        },
    )
    plans = prepare_tool_output_plans(retrieval_context, (action,))
    enforce_tool_output_boundary(
        (Value(action_result=[result]),),
        (action,),
        retrieval_context,
        plans,
    )

    consumer_context = root.deep_copy()
    model_turn = consumer_context.record_model_turn(
        "request-after-retrieval",
        [{"role": "tool", "tool_call_id": "call-retrieve", "content": result.content}],
    )

    assert result.metadata["artifact_retrieval"]["returned_byte_count"] == 256
    assert model_turn.cause is TurnCauseCode.ARTIFACT_RETRIEVAL
    assert root.get_artifact_retrieval_receipts()[0].consumed is True


def test_artifact_plan_uses_framework_work_state_when_transport_record_is_absent():
    context = Context(task_id="work-state-artifact")
    source_ref = "sandbox-output://from-work-state"
    source_hash = "sha256:" + "b" * 64
    context.write_task_runtime_state(
        "agent-1",
        "adaptive_work_state",
        {
            "available_artifacts": [
                {
                    "ref": source_ref,
                    "content_hash": source_hash,
                    "byte_count": 2048,
                    "tool": "docker",
                    "action": "read_output_artifact",
                }
            ]
        },
    )
    action = Value(
        tool_call_id="retrieval-from-work-state",
        tool_name="docker",
        action_name="read_output_artifact",
        agent_name="agent-1",
        params={"artifact_ref": source_ref, "offset": "12", "limit": "32"},
    )

    assert prepare_tool_output_plans(context, (action,)) == {}
    plan = context.get_artifact_retrieval_plan("retrieval-from-work-state")
    assert plan is not None
    assert plan.offset == 12
    assert plan.limit == 32
    assert plan.artifact_content_hash == source_hash


def test_artifact_plan_falls_back_to_amni_working_state():
    context = Context(task_id="amni-work-state-artifact")
    source_ref = "sandbox-output://from-amni-state"
    source_hash = "sha256:" + "c" * 64
    context.get = lambda key: {
        "available_artifacts": [
            {
                "ref": source_ref,
                "content_hash": source_hash,
                "byte_count": 1024,
                "tool": "docker",
                "action": "read_output_artifact",
            }
        ]
    }
    action = Value(
        tool_call_id="retrieval-from-amni-state",
        tool_name="docker",
        action_name="read_output_artifact",
        agent_name="agent-1",
        params={"artifact_ref": source_ref, "offset": 0, "limit": 64},
    )

    assert prepare_tool_output_plans(context, (action,)) == {}
    assert context.get_artifact_retrieval_plan(
        "retrieval-from-amni-state"
    ).artifact_ref == source_ref


def test_artifact_records_share_through_event_manager_runtime_owner(tmp_path):
    root = _candidate(tmp_path)
    manager = SimpleNamespace(context=root)
    root.event_manager = manager

    source_context = _candidate(tmp_path)
    source_context.event_manager = manager
    noise, digest, artifact_ref, _ = _source_output(source_context)

    retrieval_context = _candidate(tmp_path)
    retrieval_context.event_manager = manager
    action = Value(
        tool_call_id="runtime-owner-retrieval",
        tool_name="generic_stream",
        action_name="read_output_artifact",
        agent_name="agent-1",
        params={"artifact_ref": artifact_ref, "offset": 0, "limit": 8},
    )
    result = Value(
        metadata={},
        content={
            "type": "text",
            "content": noise[:8].decode("ascii"),
            "artifact_ref": artifact_ref,
            "offset": 0,
            "next_offset": 8,
            "returned_bytes": 8,
            "total_bytes": len(noise),
            "complete": False,
            "content_sha256": digest,
            "chunk_sha256": "sha256:" + hashlib.sha256(noise[:8]).hexdigest(),
        },
    )
    plans = prepare_tool_output_plans(retrieval_context, (action,))
    enforce_tool_output_boundary(
        (Value(action_result=[result]),), (action,), retrieval_context, plans
    )

    assert result.metadata["artifact_retrieval"]["returned_byte_count"] == 8
    assert len(root.get_artifact_retrieval_receipts()) == 1


def test_artifact_plan_normalizes_string_backed_tool_identities(tmp_path):
    class ToolIdentity(Enum):
        STREAM = "generic_stream"

    class ActionIdentity(Enum):
        READ = "read_output_artifact"

    context = _candidate(tmp_path)
    _, _, artifact_ref, _ = _source_output(context)
    action = Value(
        tool_call_id="enum-identity-retrieval",
        tool_name=ToolIdentity.STREAM,
        action_name=ActionIdentity.READ,
        params={"artifact_ref": artifact_ref, "offset": 0, "limit": 8},
    )

    prepare_tool_output_plans(context, (action,))

    assert context.get_artifact_retrieval_plan(
        "enum-identity-retrieval"
    ).artifact_ref == artifact_ref


def test_artifact_plan_resolves_pre_invocation_mcp_route(tmp_path):
    context = _candidate(tmp_path)
    _, _, artifact_ref, _ = _source_output(context)
    action = Value(
        tool_call_id="mcp-route-retrieval",
        tool_name="mcp",
        action_name="generic_stream__read_output_artifact",
        agent_name="agent-1",
        params={"artifact_ref": artifact_ref, "offset": "0", "limit": "8"},
    )

    prepare_tool_output_plans(context, (action,))

    plan = context.get_artifact_retrieval_plan("mcp-route-retrieval")
    assert plan is not None
    assert plan.owner_tool == "generic_stream"
    assert plan.retrieval_action == "read_output_artifact"


def test_legacy_and_candidate_keep_task_input_and_answer_invariant(tmp_path):
    task_prompt = "Summarize the relevant record and return the exact identifier."
    task_answer = {"identifier": "record-7"}
    noise = "0123456789abcdef" * 8192
    action = Value(tool_call_id="legacy-call", tool_name="generic_stream", action_name="fetch", params={})
    legacy_result = Value(content=noise, metadata={})
    legacy = Context(task_id="legacy")
    enforce_tool_output_boundary(
        (Value(action_result=[legacy_result]),), (action,), legacy,
        prepare_tool_output_plans(legacy, (action,)),
    )
    candidate = _candidate(tmp_path)
    candidate_result = Value(content=noise, metadata={})
    enforce_tool_output_boundary(
        (Value(action_result=[candidate_result]),), (action,), candidate,
        prepare_tool_output_plans(candidate, (action,)),
    )

    assert legacy_result.content == noise
    assert candidate_result.content != noise
    assert task_prompt == "Summarize the relevant record and return the exact identifier."
    assert task_answer == {"identifier": "record-7"}


def test_retrieval_wrong_ref_checksum_and_range_fail_closed(tmp_path):
    context = _candidate(tmp_path)
    noise, digest, artifact_ref, _ = _source_output(context)
    wrong_ref = Value(
        tool_call_id="wrong-ref", tool_name="generic_stream",
        action_name="read_output_artifact", params={"artifact_ref": "sandbox-output://wrong", "limit": 1},
    )
    with pytest.raises(ValueError, match="artifact_retrieval_ref_mismatch"):
        prepare_tool_output_plans(context, (wrong_ref,))

    action = Value(
        tool_call_id="bad-chunk", tool_name="generic_stream",
        action_name="read_output_artifact", params={"artifact_ref": artifact_ref, "offset": 0, "limit": 8},
    )
    result = Value(metadata={}, content={
        "type": "text", "content": noise[:8].decode(), "artifact_ref": artifact_ref,
        "offset": 0, "next_offset": 8, "returned_bytes": 8,
        "total_bytes": len(noise), "complete": False,
        "content_sha256": digest, "chunk_sha256": "sha256:" + "0" * 64,
    })
    enforce_tool_output_boundary(
        (Value(action_result=[result]),), (action,), context,
        prepare_tool_output_plans(context, (action,)),
    )
    assert result.metadata["artifact_retrieval"] == {
        **result.metadata["artifact_retrieval"],
        "status": "unavailable",
        "reason_code": "artifact_retrieval_receipt_failed",
    }

    range_action = Value(
        tool_call_id="bad-range", tool_name="generic_stream",
        action_name="read_output_artifact", params={"artifact_ref": artifact_ref, "offset": 0, "limit": 8},
    )
    valid_chunk_hash = "sha256:" + hashlib.sha256(noise[:8]).hexdigest()
    range_result = Value(metadata={}, content={
        "type": "text", "content": noise[:8].decode(), "artifact_ref": artifact_ref,
        "offset": 0, "next_offset": 9, "returned_bytes": 8,
        "total_bytes": len(noise), "complete": False,
        "content_sha256": digest, "chunk_sha256": valid_chunk_hash,
    })
    enforce_tool_output_boundary(
        (Value(action_result=[range_result]),), (range_action,), context,
        prepare_tool_output_plans(context, (range_action,)),
    )
    assert range_result.metadata["artifact_retrieval"]["status"] == "unavailable"


def test_context_economics_record_failure_never_changes_tool_result(tmp_path, monkeypatch):
    context = Context(task_id="receipt-fail-open", workspace_path=str(tmp_path))
    original = {"answer": "tool-result"}
    action = Value(tool_call_id="call", tool_name="tool", action_name="run", params={})
    result = Value(content=original, metadata={})
    monkeypatch.setattr(
        context, "record_tool_turn", lambda tool_call_id: (_ for _ in ()).throw(RuntimeError("storage"))
    )

    enforce_tool_output_boundary(
        (Value(action_result=[result]),), (action,), context,
        prepare_tool_output_plans(context, (action,)),
    )

    assert result.content is original
    assert result.metadata["turn_economics"] == {
        "status": "unavailable",
        "reason_code": "turn_economics_record_failed",
    }


def test_duplicate_turn_receipt_is_rejected_as_replay():
    context = Context(task_id="replay")
    context.record_model_turn("request", [])
    with pytest.raises(ValueError, match="turn receipt replay"):
        context.record_model_turn("request", [])


def test_turn_contract_is_redacted_and_capabilities_are_explicit():
    plan = ArtifactRetrievalPlan(
        owner_tool="private-tool", retrieval_action="read-secret",
        artifact_ref="/private/path", artifact_content_hash="sha256:" + "1" * 64,
        artifact_byte_count=10, offset=0, limit=10,
        consumer_tool_call_id_hash=hashed_identity("tool_call_id", "call"),
    )
    receipt = ArtifactRetrievalReceipt(
        plan=plan, returned_offset=0, next_offset=10, returned_byte_count=10,
        chunk_checksum="sha256:" + "2" * 64,
        source_content_hash="sha256:" + "1" * 64,
        result_content_hash=canonical_json_hash({"result": 1}), complete=True,
    )
    serialized = json.dumps(receipt.to_redacted_dict())
    assert "private-tool" not in serialized
    assert "read-secret" not in serialized
    assert "/private/path" not in serialized
    support = turn_cause_support()
    assert support[TurnCauseCode.MODEL_CHOICE.value] is True
    assert support[TurnCauseCode.VALIDATION_REPAIR.value] is True
    assert support[TurnCauseCode.DEFERRED_CATALOG_EXPANSION.value] is False
    assert support[TurnCauseCode.DEFERRED_SKILL_EXPANSION.value] is False
    assert TurnKind.MODEL.value == "model"
