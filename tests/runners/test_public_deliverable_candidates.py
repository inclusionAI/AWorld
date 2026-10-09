from __future__ import annotations

import pytest

from aworld.core.common import ActionModel, ActionResult, Observation
from aworld.core.context.base import Context
from aworld.core.execution_protocol import ExecutionProtocolPolicy, ProtocolMode
from aworld.runners.execution_protocol import (
    _missing_public_deliverable_names,
    _public_delivery_status,
    configure_execution_protocol,
    load_execution_protocol_state,
    record_model_execution_profile,
)
from aworld.runners.post_tool_progress import (
    capture_public_deliverable_baseline,
    record_semantic_tool_progress,
)
from aworld.runners.public_deliverables import inspect_public_deliverable
from aworld.sandbox.tool_observation import (
    classify_tool_effect,
    semantic_target_sha256,
)


@pytest.mark.parametrize(
    "content,reason",
    [
        (b"", "blank"),
        (b" \t\r\n", "blank"),
        ("\ufeff \n".encode(), "blank"),
        (b"TBD\n", "placeholder"),
        (b"todo", "placeholder"),
        (b'"UNKNOWN"', "placeholder"),
        (b"# TODO: implement the real answer", "placeholder"),
        (b"/* to be determined */", "placeholder"),
        (b"```text\nI don't know\n```", "placeholder"),
    ],
)
def test_empty_and_known_placeholder_files_are_not_candidate_eligible(
    tmp_path, content: bytes, reason: str
) -> None:
    output = tmp_path / "result.txt"
    output.write_bytes(content)

    inspection = inspect_public_deliverable(str(output))

    assert inspection.exists is True
    assert inspection.candidate_eligible is False
    assert inspection.rejection_reason == reason


@pytest.mark.parametrize(
    "content",
    [
        b"0",
        b"A",
        b"OK",
        b"N/A",
        b"none",
        b"false",
        b"?",
        b"flag{answer}",
        b"The input token is unknown, so emit the literal value.\nunknown\n",
        b"TODO items: none\nresult: 42\n",
        b"todoimplement",
        b"unknownyet",
        b"\x00TODO\xff",
    ],
)
def test_short_text_binary_and_non_placeholder_content_remain_eligible(
    tmp_path, content: bytes
) -> None:
    output = tmp_path / "result.bin"
    output.write_bytes(content)

    inspection = inspect_public_deliverable(str(output))

    assert inspection.exists is True
    assert inspection.candidate_eligible is True
    assert inspection.rejection_reason is None


def _public_context(output_path: str) -> Context:
    context = Context(task_id="placeholder-delivery")
    context.context_info["public_deliverable_contract"] = {
        "schema_version": "aworld.public-deliverables/v1",
        "authority": "public_task_advisory",
        "source": "public_task_text",
        "artifacts": [
            {
                "deliverable_id": "public-output-1",
                "path": output_path,
                "display_path": "out.txt",
                "kind": "file",
                "authority": "public_task_advisory",
            }
        ],
    }
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            semantic_progress_enabled=True,
        ),
    )
    assert (
        record_model_execution_profile(
            context,
            "agent",
            {
                "horizon": "long",
                "confidence": 0.9,
                "milestone_count": 2,
                "expected_tool_actions": 4,
                "verification_required": True,
                "workspace_mutation_required": True,
            },
        )
        is not None
    )
    return context


def _record_declared_write(
    context: Context,
    output_path: str,
    *,
    call_index: int,
) -> dict:
    call_id = f"declared-write-{call_index}"
    action = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        tool_call_id=call_id,
        params={"code": f"write declared output {call_index}"},
    )
    effect = classify_tool_effect(action)
    return record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[action],
        observation=Observation(
            action_result=[
                ActionResult(
                    tool_call_id=call_id,
                    success=True,
                    metadata={
                        "sandbox_observation": {
                            "schema_version": "aworld.sandbox-tool-observation/v1",
                            "tool_call_id": call_id,
                            "canonical_tool": effect.identity,
                            "operation_hash": effect.operation_hash,
                            "workspace_generation": call_index,
                            "effect": "mutating",
                            "workspace_mutated": True,
                            "action_semantic_receipt": {
                                "schema_version": ("aworld.action-semantic-receipt/v1"),
                                "capability_aliases": ["workspace.mutate"],
                                "effect": "mutating",
                                "target_ids": [semantic_target_sha256(output_path)],
                                "executed": True,
                                "succeeded": True,
                                "timed_out": False,
                                "validation_kind": None,
                                "declared_deliverable_targeted": True,
                                "tool_call_id": call_id,
                            },
                        }
                    },
                )
            ]
        ),
    )


def test_placeholder_write_does_not_discharge_delivery_debt(tmp_path) -> None:
    output = tmp_path / "out.txt"
    context = _public_context(str(output))
    capture_public_deliverable_baseline(context)

    output.write_text("TBD\n")
    placeholder = _record_declared_write(context, str(output), call_index=1)

    assert placeholder["public_delivery_count"] == 0
    assert placeholder["public_delivery_non_candidate_count"] == 1
    assert placeholder["missing_public_deliverable_count"] == 1
    assert placeholder["candidate_present"] is False
    assert placeholder["public_candidate_mutated"] is False
    assert placeholder["public_delivery_progress_advanced"] is False
    assert placeholder["candidate_advanced"] is False
    assert placeholder["delivery_progress_advanced"] is False
    assert (
        load_execution_protocol_state(context, "agent").delivery_debt_observations == 1
    )

    output.write_text("G1 X10 Y20\n")
    candidate = _record_declared_write(context, str(output), call_index=2)

    assert candidate["public_delivery_count"] == 1
    assert candidate["public_delivery_non_candidate_count"] == 0
    assert candidate["missing_public_deliverable_count"] == 0
    assert candidate["candidate_present"] is True
    assert candidate["public_candidate_mutated"] is True
    assert candidate["public_delivery_progress_advanced"] is True
    assert candidate["candidate_advanced"] is True
    assert candidate["delivery_progress_advanced"] is True
    assert (
        load_execution_protocol_state(context, "agent").delivery_debt_observations == 0
    )


def test_preexisting_placeholder_is_not_a_public_candidate(tmp_path) -> None:
    output = tmp_path / "out.txt"
    output.write_text("unknown")
    context = _public_context(str(output))
    capture_public_deliverable_baseline(context)

    state = _record_declared_write(context, str(output), call_index=1)

    assert state["public_delivery_count"] == 0
    assert state["public_delivery_non_candidate_count"] == 1
    assert state["missing_public_deliverable_count"] == 1
    assert state["candidate_present"] is False
    assert state["public_candidate_mutated"] is False
    assert (
        load_execution_protocol_state(context, "agent").delivery_debt_observations == 1
    )


def test_protocol_status_treats_placeholder_as_missing_candidate(tmp_path) -> None:
    output = tmp_path / "out.txt"
    output.write_text("TODO\n", encoding="utf-8")
    context = _public_context(str(output))

    status = _public_delivery_status(context)

    assert status == {
        "public_deliverable_declared": True,
        "missing_public_deliverable_count": 1,
        "candidate_present": False,
    }
    assert _missing_public_deliverable_names(context) == ("out.txt",)
