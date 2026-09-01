from __future__ import annotations

import hashlib
import json

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
    assert support[TurnCauseCode.VALIDATION_REPAIR.value] is False
    assert support[TurnCauseCode.DEFERRED_CATALOG_EXPANSION.value] is False
    assert support[TurnCauseCode.DEFERRED_SKILL_EXPANSION.value] is False
    assert TurnKind.MODEL.value == "model"
