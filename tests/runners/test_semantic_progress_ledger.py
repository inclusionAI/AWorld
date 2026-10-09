from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from threading import Event
from types import SimpleNamespace

import pytest

import aworld.runners.post_tool_progress as post_tool_progress_module
from aworld.agents.llm_agent import LlmOutputParser
from aworld.core.common import ActionModel, ActionResult, Observation
from aworld.core.context.amni import ApplicationContext
from aworld.core.context.amni.state import (
    ApplicationTaskContextState,
    TaskInput,
    TaskOutput,
    TaskWorkingState,
)
from aworld.core.context.base import Context
from aworld.core.context.execution_state import (
    execution_resolution_observation,
    get_execution_state,
    record_execution_state,
)
from aworld.core.context.work_progress import (
    carry_goal_work_state,
    retain_work_progress,
)
from aworld.core.context.compiler import (
    ADAPTIVE_WORK_STATE_KEY,
    ArtifactEvidence,
    ArtifactRequirement,
    CompletionContract,
    CompletionMode,
    SelfCheckEvidence,
)
from aworld.core.execution_protocol import (
    action_signature,
    ControllerAction,
    ExecutionProtocolPolicy,
    NextActionAlignment,
    ProtocolMode,
)
from aworld.models.model_response import Function, ModelResponse, ToolCall
from aworld.runners.execution_protocol import (
    configure_execution_protocol,
    consume_execution_protocol_guidance,
    load_execution_protocol_state,
    record_model_execution_profile,
    record_model_plan_update,
)
from aworld.runners.post_tool_progress import (
    capture_public_deliverable_baseline,
    record_semantic_tool_progress,
    semantic_progress_for_agent,
)
from aworld.sandbox.tool_observation import classify_tool_effect


def _sandbox_receipt(action: ActionModel, generation: int, **values):
    effect = classify_tool_effect(action)
    return {
        "schema_version": "aworld.sandbox-tool-observation/v1",
        "tool_call_id": action.tool_call_id,
        "canonical_tool": effect.identity,
        "operation_hash": effect.operation_hash,
        "workspace_generation": generation,
        **values,
    }


def _record_failure(
    context: Context,
    index: int,
    *,
    resolution_observation: dict | None = None,
):
    return record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[
            ActionModel(
                tool_name="terminal",
                action_name="execute",
                tool_call_id=f"call-{index}",
                params={"command": f"candidate-{index} --retry"},
            )
        ],
        observation=Observation(
            action_result=[
                ActionResult(
                    success=False,
                    error=f"AssertionError: row {index} at /tmp/run-{index}/check.py",
                )
            ]
        ),
        resolution_observation=resolution_observation,
    )


def _application_context() -> ApplicationContext:
    context = ApplicationContext(
        task_state=ApplicationTaskContextState(
            task_input=TaskInput(
                session_id="semantic-session",
                task_id="semantic-checkpoint",
                content="complete the public task",
            ),
            working_state=TaskWorkingState(messages=[], user_profiles=[], kv_store={}),
            task_output=TaskOutput(),
        )
    )
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            semantic_progress_enabled=True,
        ),
    )
    return context


def test_action_arguments_are_hashed_into_semantic_and_protocol_receipts():
    context = Context(task_id="action-signature")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(mode=ProtocolMode.GUIDE),
    )

    state = _record_failure(context, 7)
    arguments = {"command": "candidate-7 --retry"}
    expected = tuple(
        action_signature(identity, arguments)
        for identity in ("terminal", "execute", "terminal__execute")
    )

    assert state["observed_action_signatures"] == expected
    assert "candidate-7 --retry" not in json.dumps(state)
    protocol_state = load_execution_protocol_state(context, "agent")
    assert protocol_state.history[-1].observed_action_signatures == expected
    assert "candidate-7 --retry" not in json.dumps(protocol_state.history[-1].to_dict())


@pytest.mark.asyncio
async def test_friendly_mcp_identity_survives_mapping_for_plan_alignment():
    parser_agent = SimpleNamespace(
        sandbox=SimpleNamespace(
            mcpservers=SimpleNamespace(mcp_servers={"docker": object()})
        ),
        tool_mapping={"run_code": "docker__run_code"},
        is_model_tool_call_allowed=lambda name: name == "run_code",
    )
    parsed = await LlmOutputParser().parse(
        ModelResponse(
            id="friendly-mcp-call",
            model="offline",
            content="",
            tool_calls=[
                ToolCall(
                    id="call-friendly",
                    function=Function(
                        name="run_code",
                        arguments='{"code":"true"}',
                    ),
                )
            ],
            finish_reason="tool_calls",
            usage={"prompt_tokens": 1, "completion_tokens": 1},
        ),
        agent_id="agent",
        agent=parser_agent,
    )
    action = parsed.actions[0]
    assert action.tool_name == "mcp"
    assert action.action_name == "docker__run_code"
    assert action.model_visible_tool_name == "run_code"

    context = Context(task_id="friendly-mcp-alignment")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(mode=ProtocolMode.GUIDE),
    )
    assert (
        record_model_plan_update(
            context,
            "agent",
            {
                "decision": "continue",
                "horizon": "long",
                "milestone": "validate through the friendly MCP Tool",
                "next_action": "run the exact code probe",
                "next_action_tool": "run_code",
                "next_action_arguments": '{"code":"true"}',
                "verification_plan": "inspect the observed Tool result",
                "completion_assessment": "in_progress",
                "delivery_intent": "validate_candidate",
                "delivery_rationale": "the candidate needs one exact probe",
                "assumptions": [],
                "retired_approaches": [],
                "evidence_refs": [],
                "selected_candidate_id": "candidate-1",
            },
        )
        is not None
    )

    semantic = record_semantic_tool_progress(
        context,
        tool_name="mcp",
        agent_id="agent",
        actions=[action],
        observation=Observation(
            action_result=[
                ActionResult(
                    tool_call_id="call-friendly",
                    content="probe completed",
                    success=True,
                )
            ]
        ),
    )

    assert "run_code" in semantic["observed_action_names"]
    assert (
        action_signature("run_code", {"code": "true"})
        in semantic["observed_action_signatures"]
    )
    protocol_state = load_execution_protocol_state(context, "agent")
    assert protocol_state.last_action_alignment is NextActionAlignment.UNOBSERVABLE
    assert protocol_state.action_alignment_mismatch_count == 0


def test_concurrent_semantic_and_adaptive_evidence_survives_checkpoint(
    monkeypatch,
):
    context = _application_context()
    transport_copies = [context.deep_copy(), context.deep_copy()]
    for transport_copy in transport_copies:
        transport_copy._event_manager = SimpleNamespace(context=context)
    first_derivation_entered = Event()
    release_first_derivation = Event()
    original_record = post_tool_progress_module._record_semantic_tool_progress_locked

    def delayed_record(*args, **kwargs):
        if not first_derivation_entered.is_set():
            first_derivation_entered.set()
            assert release_first_derivation.wait(timeout=5)
        return original_record(*args, **kwargs)

    monkeypatch.setattr(
        post_tool_progress_module,
        "_record_semantic_tool_progress_locked",
        delayed_record,
    )

    def observe(copy: ApplicationContext, index: int):
        return record_semantic_tool_progress(
            copy,
            tool_name="terminal",
            agent_id="agent",
            actions=[
                ActionModel(
                    tool_name="terminal",
                    action_name="execute",
                    tool_call_id=f"concurrent-{index}",
                    params={"command": f"inspect-{index}"},
                )
            ],
            observation=Observation(
                action_result=[
                    ActionResult(content=f"observation-{index}", success=True)
                ]
            ),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(observe, transport_copies[0], 1)
        assert first_derivation_entered.wait(timeout=5)
        second = pool.submit(observe, transport_copies[1], 2)
        assert not second.done()
        release_first_derivation.set()
        assert first.result(timeout=5)["observation_count"] == 1
        assert second.result(timeout=5)["observation_count"] == 2

    restored = ApplicationContext.from_dict(context.to_dict())
    semantic = semantic_progress_for_agent(restored, agent_id="agent")
    adaptive = restored.task_state.working_state.kv_store[
        f"{ADAPTIVE_WORK_STATE_KEY}:agent"
    ]

    assert semantic["observation_count"] == 2
    assert semantic["runtime_revision"] >= 2
    assert adaptive["observation_count"] == 2
    assert len(adaptive["recent_operations"]) == 2
    assert len(set(adaptive["attempted_operation_hashes"])) == 2


def test_repeated_failure_signature_offers_bounded_checkpoint():
    context = Context(task_id="semantic-ledger")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            semantic_progress_enabled=True,
        ),
    )
    baseline = _record_failure(context, 0)
    assert baseline["goal_progress_observable"] is None
    assert baseline["no_goal_progress_count"] == 0

    for index in range(1, 6):
        state = _record_failure(context, index)

    assert state["failure_signature"] == baseline["failure_signature"]
    assert state["no_goal_progress_count"] == 0
    assert state["durable_stagnation_count"] == 6
    metrics = context.context_info["execution_protocol_metrics"]
    assert metrics["last_action"] == ControllerAction.REQUEST_REPLAN.value


def test_changed_failure_signature_is_meaningful_progress():
    context = Context(task_id="semantic-failure-change")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            semantic_progress_enabled=True,
        ),
    )
    _record_failure(context, 1)
    repeated = _record_failure(context, 2)
    assert repeated["no_goal_progress_count"] == 0

    changed = record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[ActionModel(tool_name="terminal", action_name="execute")],
        observation=Observation(
            action_result=[ActionResult(success=False, error="PermissionError: denied")]
        ),
    )

    assert changed["failure_signature"] != repeated["failure_signature"]
    assert changed["semantic_progress_enabled"] is True
    assert changed["goal_progress"] is False
    assert changed["durable_milestone_advanced"] is False
    assert changed["no_goal_progress_count"] == 0
    assert changed["durable_stagnation_count"] == 3
    assert changed["last_meaningful_progress_at"] is not None


def test_failed_tool_result_cannot_promote_public_file_change_to_progress(tmp_path):
    output = tmp_path / "result.json"
    context = Context(task_id="public-delivery-progress")
    context.context_info["public_deliverable_contract"] = {
        "schema_version": "aworld.public-deliverables/v1",
        "authority": "public_task_advisory",
        "source": "public_task_text",
        "artifacts": [
            {
                "deliverable_id": "public-output-1",
                "path": str(output),
                "display_path": "result.json",
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
    record_execution_state(
        context,
        "agent",
        "incomplete",
        "delivery_candidate_missing",
    )

    missing = _record_failure(context, 0)
    assert missing["goal_progress_observable"] is True
    assert missing["public_delivery_count"] == 0
    assert missing["public_delivery_advanced"] is False
    assert missing["public_deliverable_declared"] is True
    assert missing["missing_public_deliverable_count"] == 1
    assert missing["candidate_present"] is False
    assert missing["workspace_mutated"] is False
    assert missing["new_information_observed"] is True
    assert get_execution_state(context, agent_id="agent")["status"] == "incomplete"

    output.write_text("{}")
    created = _record_failure(
        context,
        1,
        resolution_observation=execution_resolution_observation(context, "agent"),
    )
    assert created["completion_advanced"] is False
    assert created["public_delivery_count"] == 1
    assert created["public_delivery_advanced"] is True
    assert created["public_delivery_progress_advanced"] is False
    assert created["candidate_present"] is True
    assert created["candidate_advanced"] is False
    assert created["durable_milestone_advanced"] is False
    assert created["goal_progress"] is False
    assert context.completion_contract is None
    assert get_execution_state(context, agent_id="agent")["status"] == "incomplete"

    repeated = _record_failure(context, 2)
    assert repeated["public_delivery_advanced"] is False
    assert repeated["durable_milestone_advanced"] is False


def test_public_candidate_resolution_uses_tool_start_watermark(tmp_path):
    from aworld.sandbox.tool_observation import semantic_target_sha256

    output = tmp_path / "result.json"
    context = Context(task_id="candidate-resolution-watermark")
    context.context_info["public_deliverable_contract"] = {
        "schema_version": "aworld.public-deliverables/v1",
        "authority": "public_task_advisory",
        "source": "public_task_text",
        "artifacts": [
            {
                "deliverable_id": "public-output-1",
                "path": str(output),
                "display_path": "result.json",
                "kind": "file",
                "authority": "public_task_advisory",
            }
        ],
    }
    capture_public_deliverable_baseline(context)
    stale_tool_start = execution_resolution_observation(context, "agent")
    record_execution_state(
        context, "agent", "incomplete", "delivery_candidate_missing"
    )

    output.write_text("{}")
    stale_action = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        tool_call_id="stale-candidate-write",
        params={"code": f"printf '{{}}' > {output}"},
    )
    record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[stale_action],
        observation=Observation(
            action_result=[
                ActionResult(
                    tool_call_id="stale-candidate-write",
                    content="candidate",
                    success=True,
                    metadata={
                        "sandbox_observation": _sandbox_receipt(
                            stale_action,
                            1,
                            effect="mutating",
                            workspace_mutated=True,
                            action_semantic_receipt={
                                "schema_version": "aworld.action-semantic-receipt/v1",
                                "capability_aliases": ["workspace.mutate"],
                                "effect": "mutating",
                                "target_ids": [
                                    semantic_target_sha256(str(output))
                                ],
                                "executed": True,
                                "succeeded": True,
                                "timed_out": False,
                                "validation_kind": None,
                                "declared_deliverable_targeted": True,
                                "tool_call_id": "stale-candidate-write",
                            },
                        )
                    },
                )
            ]
        ),
        resolution_observation=stale_tool_start,
    )
    assert get_execution_state(context, agent_id="agent")["status"] == "incomplete"

    fresh_tool_start = execution_resolution_observation(context, "agent")
    output.write_text('{"complete": true}')
    fresh_action = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        tool_call_id="fresh-candidate-write",
        params={"code": f"printf complete > {output}"},
    )
    record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[fresh_action],
        observation=Observation(
            action_result=[
                ActionResult(
                    tool_call_id="fresh-candidate-write",
                    content="candidate updated",
                    success=True,
                    metadata={
                        "sandbox_observation": _sandbox_receipt(
                            fresh_action,
                            2,
                            effect="mutating",
                            workspace_mutated=True,
                            action_semantic_receipt={
                                "schema_version": "aworld.action-semantic-receipt/v1",
                                "capability_aliases": ["workspace.mutate"],
                                "effect": "mutating",
                                "target_ids": [
                                    semantic_target_sha256(str(output))
                                ],
                                "executed": True,
                                "succeeded": True,
                                "timed_out": False,
                                "validation_kind": None,
                                "declared_deliverable_targeted": True,
                                "tool_call_id": "fresh-candidate-write",
                            },
                        )
                    },
                )
            ]
        ),
        resolution_observation=fresh_tool_start,
    )
    assert get_execution_state(context, agent_id="agent")["status"] == "running"


def test_successful_typed_declared_mutation_advances_public_candidate(tmp_path):
    from aworld.sandbox.tool_observation import semantic_target_sha256

    output = tmp_path / "result.json"
    context = Context(task_id="typed-public-delivery-progress")
    context.context_info["public_deliverable_contract"] = {
        "schema_version": "aworld.public-deliverables/v1",
        "authority": "public_task_advisory",
        "source": "public_task_text",
        "artifacts": [
            {
                "deliverable_id": "public-output-1",
                "path": str(output),
                "display_path": "result.json",
                "kind": "file",
                "authority": "public_task_advisory",
            }
        ],
    }
    capture_public_deliverable_baseline(context)
    output.write_text("{}")
    action = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        tool_call_id="typed-write",
        params={"code": f"printf '{{}}' > {output}"},
    )
    state = record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[action],
        observation=Observation(
            action_result=[
                ActionResult(
                    tool_call_id="typed-write",
                    success=True,
                    metadata={
                        "sandbox_observation": _sandbox_receipt(
                            action,
                            1,
                            effect="mutating",
                            workspace_mutated=True,
                            action_semantic_receipt={
                                "schema_version": "aworld.action-semantic-receipt/v1",
                                "capability_aliases": ["workspace.mutate"],
                                "effect": "mutating",
                                "target_ids": [semantic_target_sha256(str(output))],
                                "executed": True,
                                "succeeded": True,
                                "timed_out": False,
                                "validation_kind": None,
                                "declared_deliverable_targeted": True,
                                "tool_call_id": "typed-write",
                            },
                        )
                    },
                )
            ]
        ),
    )

    assert state["public_delivery_advanced"] is True
    assert state["public_delivery_progress_advanced"] is True
    assert state["candidate_advanced"] is True
    assert state["delivery_progress_advanced"] is True


def test_failed_declared_mutations_do_not_reset_candidate_convergence(tmp_path):
    from aworld.sandbox.tool_observation import semantic_target_sha256

    output = tmp_path / "result.json"
    context = Context(task_id="failed-public-delivery-progress")
    context.context_info["public_deliverable_contract"] = {
        "schema_version": "aworld.public-deliverables/v1",
        "authority": "public_task_advisory",
        "source": "public_task_text",
        "artifacts": [
            {
                "deliverable_id": "public-output-1",
                "path": str(output),
                "display_path": "result.json",
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
            post_candidate_read_only_threshold=2,
            repetition_threshold=99,
            low_information_gain_threshold=99,
            no_goal_progress_threshold=99,
            stagnation_event_threshold=99,
        ),
    )
    assert record_model_execution_profile(
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
    ) is not None
    capture_public_deliverable_baseline(context)

    def observe(content: str, *, success: bool, index: int):
        output.write_text(content)
        call_id = f"declared-write-{index}"
        action = ActionModel(
            tool_name="terminal",
            action_name="run_code",
            tool_call_id=call_id,
            params={"code": f"printf value > {output}"},
        )
        return record_semantic_tool_progress(
            context,
            tool_name="terminal",
            agent_id="agent",
            actions=[action],
            observation=Observation(
                action_result=[
                    ActionResult(
                        tool_call_id=call_id,
                        success=success,
                        error=None if success else "command_failed",
                        metadata={
                            "sandbox_observation": _sandbox_receipt(
                                action,
                                index,
                                effect="mutating",
                                workspace_mutated=True,
                                action_semantic_receipt={
                                    "schema_version": "aworld.action-semantic-receipt/v1",
                                    "capability_aliases": ["workspace.mutate"],
                                    "effect": "mutating",
                                    "target_ids": [
                                        semantic_target_sha256(str(output))
                                    ],
                                    "executed": True,
                                    "succeeded": success,
                                    "timed_out": False,
                                    "validation_kind": None,
                                    "declared_deliverable_targeted": True,
                                    "tool_call_id": call_id,
                                },
                            )
                        },
                    )
                ]
            ),
        )

    initial = observe("A", success=True, index=1)
    first_failed = observe("B", success=False, index=2)
    second_failed = observe("C", success=False, index=3)

    assert initial["candidate_advanced"] is True
    assert first_failed["public_delivery_advanced"] is True
    assert first_failed["candidate_advanced"] is False
    assert second_failed["candidate_advanced"] is False
    protocol = load_execution_protocol_state(context, "agent")
    assert protocol.post_candidate_no_delivery_progress_observations == 2
    assert protocol.convergence_constraint_active is True


def test_public_deliverable_baseline_distinguishes_existing_file_from_update(
    tmp_path,
):
    output = tmp_path / "result.json"
    output.write_text("{}")
    context = Context(task_id="public-delivery-update")
    context.context_info["public_deliverable_contract"] = {
        "schema_version": "aworld.public-deliverables/v1",
        "authority": "public_task_advisory",
        "source": "public_task_text",
        "artifacts": [
            {
                "deliverable_id": "public-output-1",
                "path": str(output),
                "display_path": "result.json",
                "kind": "file",
                "authority": "public_task_advisory",
            }
        ],
    }
    capture_public_deliverable_baseline(context)

    unchanged = _record_failure(context, 0)
    assert unchanged["candidate_present"] is True
    assert unchanged["candidate_advanced"] is False
    assert "terminal" in unchanged["observed_action_names"]
    assert "terminal__execute" in unchanged["observed_action_names"]

    before = output.stat()
    os.utime(
        output,
        ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000),
    )
    metadata_only = _record_failure(context, 1)
    assert metadata_only["candidate_advanced"] is False

    output.write_text('{"candidate": true}')
    updated = _record_failure(context, 2)
    assert updated["candidate_present"] is True
    assert updated["public_delivery_advanced"] is True
    assert updated["candidate_advanced"] is False

    output.write_text("{}")
    reverted = _record_failure(context, 3)
    assert reverted["candidate_advanced"] is False
    assert reverted["public_delivery_advanced"] is False
    assert reverted["goal_progress"] is False


def _public_delivery_contract(path: str, *, deliverable_id: str = "candidate"):
    return {
        "schema_version": "aworld.public-deliverables/v1",
        "authority": "public_task_advisory",
        "source": "public_task_text",
        "artifacts": [
            {
                "deliverable_id": deliverable_id,
                "path": path,
                "display_path": os.path.basename(path),
                "kind": "file",
                "authority": "public_task_advisory",
            }
        ],
    }


def _successful_unknown_observation(action: ActionModel, content: str):
    return Observation(
        action_result=[
            ActionResult(
                tool_call_id=action.tool_call_id,
                content=content,
                success=True,
                metadata={
                    "sandbox_observation": _sandbox_receipt(
                        action,
                        1,
                        effect="unknown",
                        workspace_mutated=False,
                        cache_hit=False,
                        action_semantic_receipt={
                            "schema_version": "aworld.action-semantic-receipt/v1",
                            "capability_aliases": ["workspace.execute"],
                            "effect": "unknown",
                            "target_ids": [],
                            "executed": True,
                            "succeeded": True,
                            "timed_out": False,
                            "validation_kind": None,
                            "declared_deliverable_targeted": False,
                            "tool_call_id": action.tool_call_id,
                        },
                    )
                },
            )
        ]
    )


def test_structured_quality_debt_clears_after_one_public_cell_grid_repair(
    tmp_path,
):
    from aworld.sandbox.tool_observation import semantic_target_sha256

    output = tmp_path / "candidate.json"
    context = Context(task_id="structured-quality-repair")
    context.context_info["public_deliverable_contract"] = _public_delivery_contract(
        str(output)
    )
    capture_public_deliverable_baseline(context, agent_id="agent")
    output.write_text(
        json.dumps({"blocks": [{"type": "table", "usable": False}]}),
        encoding="utf-8",
    )
    parse_action = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        tool_call_id="parse-quality-open",
        params={"code": "capability-adapter parse source.pdf"},
    )
    open_state = record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[parse_action],
        observation=_successful_unknown_observation(
            parse_action,
            json.dumps(
                {
                    "quality_report": {
                        "tables": {
                            "declared": 1,
                            "usable": 0,
                            "issues": [
                                {"reason": "structured_block_meta_prose"}
                            ],
                        }
                    }
                }
            ),
        ),
    )
    assert open_state["structured_quality_open"] is True
    assert open_state["structured_quality_repair_attempt_count"] == 0

    clear_claim = ActionModel(
        tool_name="terminal",
        action_name="run_code",
        tool_call_id="unverified-quality-clear",
        params={"code": "printf an-unverified-clear-claim"},
    )
    still_open = record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[clear_claim],
        observation=_successful_unknown_observation(
            clear_claim,
            json.dumps(
                {
                    "schema_version": "aworld.structured-quality-observation/v1",
                    "status": "clear",
                    "required_table_count": 1,
                    "usable_table_count": 1,
                    "reason_codes": [],
                }
            ),
        ),
    )
    assert still_open["structured_quality_open"] is True

    output.write_text(
        json.dumps(
            {
                "blocks": [
                    {
                        "type": "table",
                        "table": {"rows": [["name", "value"], ["a", "3"]]},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    repair_action = ActionModel(
        tool_name="filesystem",
        action_name="write_file",
        tool_call_id="repair-cell-grid",
        params={"path": str(output), "content": output.read_text()},
    )
    repaired = record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[repair_action],
        observation=Observation(
            action_result=[
                ActionResult(
                    tool_call_id=repair_action.tool_call_id,
                    content=json.dumps({"success": True, "status": "repaired"}),
                    success=True,
                    metadata={
                        "sandbox_observation": _sandbox_receipt(
                            repair_action,
                            2,
                            effect="mutating",
                            workspace_mutated=True,
                            action_semantic_receipt={
                                "schema_version": "aworld.action-semantic-receipt/v1",
                                "capability_aliases": ["workspace.mutate"],
                                "effect": "mutating",
                                "target_ids": [semantic_target_sha256(str(output))],
                                "executed": True,
                                "succeeded": True,
                                "timed_out": False,
                                "validation_kind": None,
                                "declared_deliverable_targeted": True,
                                "tool_call_id": repair_action.tool_call_id,
                            },
                        )
                    },
                )
            ]
        ),
    )
    assert repaired["structured_quality_open"] is False
    assert repaired["structured_quality_usable_table_count"] >= 1
    assert repaired["structured_quality_repair_attempt_count"] == 1


def test_public_delivery_continuity_survives_implicit_segment_without_recounting(
    tmp_path,
):
    output = tmp_path / "candidate.txt"
    old = Context(task_id="segment-a", task_epoch=1)
    old.context_info["public_deliverable_contract"] = _public_delivery_contract(
        str(output)
    )
    retain_work_progress(old, "agent")
    capture_public_deliverable_baseline(old, agent_id="agent")

    output.write_text("candidate-a", encoding="utf-8")
    first = _record_failure(old, 1)

    assert first["public_candidate_mutated"] is True
    assert first["public_delivery_advanced"] is True
    original_continuity = first["public_delivery_continuity"]
    assert original_continuity["baseline_versions"] == {"candidate": None}
    assert original_continuity["latest_versions"] == first[
        "public_delivery_versions"
    ]

    new = Context(task_id="segment-b", task_epoch=0)
    new.context_info["public_deliverable_contract"] = _public_delivery_contract(
        str(output)
    )
    assert carry_goal_work_state(old, new) == 1
    carried = new.context_info[f"{ADAPTIVE_WORK_STATE_KEY}:agent"]
    assert carried["workspace_generation"] == 0
    assert carried["artifact_fingerprint"] is None
    assert carried["latest_sandbox_observations"] == []
    assert carried["latest_workspace_mutation"] is None
    assert carried["public_delivery_continuity"]["scope"] == {
        "task_id": "segment-b",
        "task_epoch": 0,
    }
    assert carried["public_delivery_continuity"]["baseline_versions"] == {
        "candidate": None
    }
    assert carried["public_delivery_continuity"]["latest_fingerprint"] == (
        original_continuity["latest_fingerprint"]
    )
    assert carried["public_delivery_continuity"]["high_water_bloom"] == (
        original_continuity["high_water_bloom"]
    )

    capture_public_deliverable_baseline(new, agent_id="agent")
    rebound_baseline = new.context_info["public_deliverable_baseline"]
    assert rebound_baseline["artifacts"] == {"candidate": None}

    repeated = _record_failure(new, 2)

    assert repeated["public_candidate_mutated"] is True
    assert repeated["public_delivery_changed"] is False
    assert repeated["public_delivery_advanced"] is False
    assert repeated["public_delivery_continuity"]["latest_fingerprint"] == (
        original_continuity["latest_fingerprint"]
    )
    assert repeated["public_delivery_continuity"]["high_water_bloom"] == (
        original_continuity["high_water_bloom"]
    )

    output.unlink()
    deleted = _record_failure(new, 3)
    assert deleted["candidate_present"] is False
    assert deleted["public_candidate_mutated"] is False
    assert deleted["public_delivery_continuity"]["latest_versions"] == {
        "candidate": None
    }

    output.write_text("candidate-a", encoding="utf-8")
    restored = _record_failure(new, 4)
    assert restored["candidate_present"] is True
    assert restored["public_candidate_mutated"] is True
    assert restored["public_delivery_changed"] is True
    assert restored["public_delivery_advanced"] is False


def test_public_delivery_continuity_rejects_changed_contract(tmp_path):
    old_output = tmp_path / "old.txt"
    old = Context(task_id="segment-old", task_epoch=2)
    old.context_info["public_deliverable_contract"] = _public_delivery_contract(
        str(old_output), deliverable_id="old"
    )
    retain_work_progress(old, "agent")
    capture_public_deliverable_baseline(old, agent_id="agent")
    old_output.write_text("old-candidate", encoding="utf-8")
    old_state = _record_failure(old, 1)

    new_output = tmp_path / "new.txt"
    new_output.write_text("preexisting-new", encoding="utf-8")
    new = Context(task_id="segment-new", task_epoch=0)
    new.context_info["public_deliverable_contract"] = _public_delivery_contract(
        str(new_output), deliverable_id="new"
    )
    assert carry_goal_work_state(old, new) == 1
    carried = new.context_info[f"{ADAPTIVE_WORK_STATE_KEY}:agent"]
    assert "public_delivery_continuity" not in carried

    capture_public_deliverable_baseline(new, agent_id="agent")
    baseline = new.context_info["public_deliverable_baseline"]
    current = _record_failure(new, 2)

    assert baseline["artifacts"] == current["public_delivery_versions"]
    assert current["public_candidate_mutated"] is False
    assert current["public_delivery_changed"] is False
    assert current["public_delivery_advanced"] is False
    assert current["public_delivery_continuity"]["contract_fingerprint"] != (
        old_state["public_delivery_continuity"]["contract_fingerprint"]
    )


def test_public_deliverable_hash_stops_when_file_grows_past_shared_budget(
    tmp_path,
):
    output = tmp_path / "growing.bin"
    output.write_bytes(b"seed")
    stale_stat = output.stat()
    with output.open("ab") as handle:
        handle.write(b"x" * (8 * 1024 * 1024 + 1))

    version, consumed = post_tool_progress_module._public_file_version(
        str(output),
        stale_stat,
        hash_budget_bytes=8 * 1024 * 1024,
    )

    assert version == f"size-only:{output.stat().st_size}"
    assert consumed == 0


def test_public_deliverable_content_oscillation_is_not_repeated_goal_progress(
    tmp_path,
):
    output = tmp_path / "candidate.txt"
    context = Context(task_id="public-delivery-oscillation")
    context.context_info["public_deliverable_contract"] = {
        "schema_version": "aworld.public-deliverables/v1",
        "authority": "public_task_advisory",
        "source": "public_task_text",
        "artifacts": [
            {
                "deliverable_id": "candidate",
                "path": str(output),
                "display_path": "candidate.txt",
                "kind": "file",
                "authority": "public_task_advisory",
            }
        ],
    }
    capture_public_deliverable_baseline(context)

    output.write_text("A")
    first_a = _record_failure(context, 0)
    output.write_text("B")
    first_b = _record_failure(context, 1)
    output.write_text("A")
    repeated_a = _record_failure(context, 2)

    assert first_a["public_delivery_advanced"] is True
    assert first_a["candidate_advanced"] is False
    assert first_b["public_delivery_advanced"] is True
    assert first_b["candidate_advanced"] is False
    assert repeated_a["public_delivery_changed"] is True
    assert repeated_a["candidate_advanced"] is False
    assert repeated_a["public_delivery_advanced"] is False
    assert repeated_a["durable_milestone_advanced"] is False
    assert repeated_a["goal_progress"] is False


def test_alternating_known_failure_signatures_do_not_reset_progress():
    context = Context(task_id="semantic-failure-abab")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            semantic_progress_enabled=True,
        ),
    )

    states = []
    for index, error in enumerate(
        (
            "AssertionError: alpha",
            "PermissionError: beta",
            "AssertionError: alpha",
            "PermissionError: beta",
            "AssertionError: alpha",
            "PermissionError: beta",
        )
    ):
        states.append(
            record_semantic_tool_progress(
                context,
                tool_name="terminal",
                agent_id="agent",
                actions=[
                    ActionModel(
                        tool_name="terminal",
                        action_name="execute",
                        params={"command": f"attempt-{index}"},
                    )
                ],
                observation=Observation(
                    action_result=[ActionResult(success=False, error=error)]
                ),
            )
        )

    assert states[0]["goal_progress"] is False
    assert states[1]["goal_progress"] is False
    assert states[0]["durable_milestone_advanced"] is False
    assert states[1]["durable_milestone_advanced"] is False
    assert all(state["goal_progress"] is False for state in states[2:])
    assert states[-1]["no_goal_progress_count"] == 0
    assert states[-1]["durable_stagnation_count"] == 6
    assert len(states[-1]["recent_failure_signatures"]) == 6


def test_failure_resolution_advances_only_one_novel_unresolved_occurrence():
    context = Context(task_id="novel-failure-resolution")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(mode=ProtocolMode.GUIDE),
    )

    def observe(*, success: bool, index: int):
        call_id = f"retry-{index}"
        action = ActionModel(
            tool_name="terminal",
            action_name="run_code",
            tool_call_id=call_id,
            params={"code": "python check.py"},
        )
        return record_semantic_tool_progress(
            context,
            tool_name="terminal",
            agent_id="agent",
            actions=[action],
            observation=Observation(
                action_result=[
                    ActionResult(
                        tool_call_id=call_id,
                        success=success,
                        error=None if success else "AssertionError: stable failure",
                        metadata={
                            "sandbox_observation": _sandbox_receipt(
                                action,
                                0,
                                effect="read_only",
                                workspace_mutated=False,
                                action_semantic_receipt={
                                    "schema_version": "aworld.action-semantic-receipt/v1",
                                    "capability_aliases": ["workspace.read"],
                                    "effect": "read_only",
                                    "target_ids": [],
                                    "executed": True,
                                    "succeeded": success,
                                    "timed_out": False,
                                    "validation_kind": None,
                                    "declared_deliverable_targeted": False,
                                    "tool_call_id": call_id,
                                },
                            )
                        },
                    )
                ]
            ),
        )

    first_failure = observe(success=False, index=1)
    first_success = observe(success=True, index=2)
    replayed_failure = observe(success=False, index=3)
    replayed_success = observe(success=True, index=4)

    assert first_failure["failure_resolved"] is False
    assert first_failure["unresolved_novel_failure"] is not None
    assert first_success["failure_resolved"] is True
    assert first_success["delivery_progress_advanced"] is True
    assert replayed_failure["failure_resolved"] is False
    assert replayed_failure["unresolved_novel_failure"] is None
    assert replayed_success["failure_resolved"] is False
    assert replayed_success["delivery_progress_advanced"] is False


def test_semantic_progress_ledger_env_opt_out(monkeypatch):
    monkeypatch.setenv("AWORLD_SEMANTIC_PROGRESS_LEDGER", "false")
    context = Context(task_id="semantic-ledger-off")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            semantic_progress_enabled=True,
        ),
    )

    state = _record_failure(context, 1)

    assert state["semantic_progress_enabled"] is False
    assert state["goal_progress_observable"] is False
    assert state["no_goal_progress_count"] == 0


def test_candidate_claim_with_no_delivery_progress_enters_convergence_not_finalization():
    context = Context(task_id="semantic-replan-limit")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            semantic_progress_enabled=True,
            max_replans=2,
        ),
    )
    _record_failure(context, 0)
    next_index = 1
    for _ in range(6):
        _record_failure(context, next_index)
        next_index += 1
    guidance = consume_execution_protocol_guidance(context, "agent")
    assert guidance is not None and "checkpoint" in guidance
    assert (
        record_model_plan_update(
            context,
            "agent",
            {
                "decision": "replan",
                "horizon": "long",
                "milestone": "resolve the repeated failure",
                "next_action": "try a materially different bounded probe",
                "next_action_tool": "terminal__execute",
                "next_action_arguments": json.dumps(
                    {"command": f"candidate-{next_index} --retry"}
                ),
                "verification_plan": "compare the next observed failure signature",
                "completion_assessment": "in_progress",
                "delivery_intent": "validate_candidate",
                "delivery_rationale": "the next probe tests the revised approach",
                "assumptions": [],
                "retired_approaches": ["repeat the same ineffective retry"],
                "evidence_refs": [f"tool:call-{next_index - 1}"],
                "selected_candidate_id": None,
            },
        )
        is not None
    )

    for _ in range(3):
        _record_failure(context, next_index)
        next_index += 1

    metrics = context.context_info["execution_protocol_metrics"]
    assert metrics["last_action"] == ControllerAction.APPLY_CONVERGENCE_CONSTRAINT.value
    assert metrics["last_reason"] == "post_candidate_stagnation"
    assert "convergence constraint" in consume_execution_protocol_guidance(
        context, "agent"
    )
    protocol_state = load_execution_protocol_state(context, "agent")
    assert protocol_state.replan_count == 1
    assert protocol_state.convergence_constraint_active is True
    assert protocol_state.convergence_stage.value == "validate_repair_or_submit"
    assert protocol_state.finalization_entered is False


def test_distinct_successful_investigations_offer_durable_evidence_checkpoint():
    context = Context(task_id="semantic-progress-unknown")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            semantic_progress_enabled=True,
            no_goal_progress_threshold=2,
            stagnation_event_threshold=2,
        ),
    )

    for index in range(6):
        state = record_semantic_tool_progress(
            context,
            tool_name="terminal",
            agent_id="agent",
            actions=[
                ActionModel(
                    tool_name="terminal",
                    action_name="execute",
                    tool_call_id=f"call-{index}",
                    params={"command": f"inspect-stage-{index}"},
                )
            ],
            observation=Observation(
                action_result=[
                    ActionResult(content=f"novel observation {index}", success=True)
                ]
            ),
        )
        if index < 5:
            assert load_execution_protocol_state(context, "agent").replan_count == 0

    assert state["goal_progress_observable"] is None
    assert state["goal_progress"] is False
    assert state["no_goal_progress_count"] == 0
    assert state["result_repetition_count"] == 1
    assert state["durable_stagnation_count"] == 6
    assert state["low_information_gain_count"] == 6
    protocol_state = load_execution_protocol_state(context, "agent")
    assert protocol_state.replan_count == 1
    assert protocol_state.phase.value == "execute"
    assert protocol_state.finalization_entered is False
    guidance = consume_execution_protocol_guidance(context, "agent")
    assert guidance is not None
    assert "bounded next action" in guidance
    assert "inspectable milestone evidence" in guidance
    assert "keeps all normal Tools available" in guidance
    assert consume_execution_protocol_guidance(context, "agent") is None
    assert (
        record_model_plan_update(
            context,
            "agent",
            {
                "decision": "continue",
                "horizon": "long",
                "milestone": "collect discriminating evidence",
                "next_action": "inspect one new stage",
                "next_action_tool": "terminal__execute",
                "next_action_arguments": '{"command":"inspect-after-checkpoint"}',
                "verification_plan": "compare it with the milestone contract",
                "completion_assessment": "in_progress",
                "delivery_intent": "continue_exploration",
                "delivery_rationale": "one new stage can add discriminating evidence",
                "assumptions": ["the next stage is independently observable"],
                "retired_approaches": [],
                "evidence_refs": ["tool:call-5"],
                "selected_candidate_id": None,
            },
        )
        is not None
    )
    reset = record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[
            ActionModel(
                tool_name="terminal",
                action_name="execute",
                tool_call_id="after-checkpoint",
                params={"command": "inspect-after-checkpoint"},
            )
        ],
        observation=Observation(
            action_result=[ActionResult(content="new observation", success=True)]
        ),
    )
    assert reset["durable_stagnation_count"] == 1
    assert reset["low_information_gain_count"] == 1


def test_unverified_artifact_advance_does_not_reset_durable_stagnation():
    context = Context(task_id="semantic-unverified-artifact")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            semantic_progress_enabled=True,
        ),
    )

    for index in range(5):
        state = record_semantic_tool_progress(
            context,
            tool_name="terminal",
            agent_id="agent",
            actions=[
                ActionModel(
                    tool_name="terminal",
                    action_name="execute",
                    tool_call_id=f"inspect-{index}",
                    params={"command": f"inspect-stage-{index}"},
                )
            ],
            observation=Observation(
                action_result=[
                    ActionResult(content=f"novel observation {index}", success=True)
                ]
            ),
        )

    assert state["goal_progress_observable"] is None
    assert state["durable_stagnation_count"] == 5
    assert load_execution_protocol_state(context, "agent").replan_count == 0

    write_action = ActionModel(
        tool_name="filesystem",
        action_name="write_file",
        tool_call_id="write-candidate",
        params={"path": "candidate.txt", "content": "candidate"},
    )
    advanced = record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[write_action],
        observation=Observation(
            action_result=[
                ActionResult(
                    tool_call_id=write_action.tool_call_id,
                    content="candidate written",
                    success=True,
                    metadata={
                        "context_management": {
                            "schema_version": "aworld.sandbox-artifact-progress/v1",
                            "artifact_changed": True,
                            "artifact_fingerprint_after": "artifact-v2",
                        },
                        "sandbox_observation": _sandbox_receipt(
                            write_action,
                            1,
                            effect="mutating",
                            workspace_mutated=True,
                        ),
                    },
                )
            ]
        ),
    )

    assert advanced["artifact_advanced"] is True
    assert advanced["workspace_mutated"] is True
    assert advanced["diagnostic_progress_observable"] is True
    assert advanced["durable_milestone_advanced"] is False
    assert advanced["goal_progress_observable"] is None
    assert advanced["goal_progress"] is False
    assert advanced["durable_stagnation_count"] == 6
    assert advanced["low_information_gain_count"] == 6
    assert load_execution_protocol_state(context, "agent").replan_count == 1


def test_contract_bound_completion_advance_resets_durable_stagnation():
    context = Context(task_id="semantic-contract-progress")
    context.configure_completion_contract(
        CompletionContract(
            required_artifacts=(
                ArtifactRequirement(
                    requirement_id="candidate",
                    path="/app/candidate.txt",
                ),
            ),
            immutable_inputs=(),
            validation_commands=(),
            max_evidence_age_seconds=None,
            required_final_evidence=(),
        ),
        mode=CompletionMode.ENFORCE,
    )
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            semantic_progress_enabled=True,
        ),
    )
    record_execution_state(
        context,
        "agent",
        "incomplete",
        "completion_contract_unsatisfied",
    )

    for index in range(5):
        state = record_semantic_tool_progress(
            context,
            tool_name="terminal",
            agent_id="agent",
            actions=[
                ActionModel(
                    tool_name="terminal",
                    action_name="execute",
                    tool_call_id=f"inspect-contract-{index}",
                    params={"command": f"inspect-stage-{index}"},
                )
            ],
            observation=Observation(
                action_result=[
                    ActionResult(content=f"novel observation {index}", success=True)
                ]
            ),
        )

    assert state["durable_stagnation_count"] == 5
    context.record_completion_artifact(
        ArtifactEvidence(
            requirement_id="candidate",
            exists=True,
            content_hash="sha256:" + "a" * 64,
            observed_at=datetime.now(timezone.utc),
        )
    )
    resolution_observation = execution_resolution_observation(context, "agent")
    advanced = record_semantic_tool_progress(
        context,
        tool_name="filesystem",
        agent_id="agent",
        actions=[
            ActionModel(
                tool_name="filesystem",
                action_name="write_file",
                tool_call_id="write-required-candidate",
                params={"path": "/app/candidate.txt", "content": "candidate"},
            )
        ],
        observation=Observation(
            action_result=[ActionResult(content="candidate written", success=True)]
        ),
        resolution_observation=resolution_observation,
    )

    assert advanced["completion_advanced"] is True
    assert advanced["durable_milestone_advanced"] is True
    assert advanced["goal_progress"] is True
    assert advanced["durable_stagnation_count"] == 0
    assert load_execution_protocol_state(context, "agent").replan_count == 0
    assert get_execution_state(context, agent_id="agent")["status"] == "running"


def test_repeated_identical_completion_evidence_is_not_new_goal_progress():
    context = Context(task_id="semantic-identical-completion-evidence")
    context.configure_completion_contract(
        CompletionContract(
            required_artifacts=(),
            immutable_inputs=(),
            validation_commands=(),
            max_evidence_age_seconds=None,
            required_final_evidence=(),
            required_self_check_ids=("focused-check",),
        ),
        mode=CompletionMode.ENFORCE,
    )
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            semantic_progress_enabled=True,
        ),
    )
    evidence = SelfCheckEvidence(
        command_id="focused-check",
        exit_code=0,
        output_hash="sha256:passed",
        observed_at=datetime.now(timezone.utc),
    )
    context.record_completion_self_check(evidence)
    first = record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[ActionModel(tool_name="terminal", action_name="execute")],
        observation=Observation(
            action_result=[ActionResult(content="check passed", success=True)]
        ),
    )

    context.record_completion_self_check(evidence)
    repeated = record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[ActionModel(tool_name="terminal", action_name="execute")],
        observation=Observation(
            action_result=[ActionResult(content="check passed", success=True)]
        ),
    )

    assert first["completion_score"] == [1, 0]
    assert first["goal_progress"] is True
    assert repeated["completion_score"] == [1, 0]
    assert repeated["completion_advanced"] is False
    assert repeated["validation_evidence_advanced"] is False
    assert repeated["durable_milestone_advanced"] is False
    assert repeated["goal_progress"] is False
    assert repeated["durable_stagnation_count"] == 1


def test_undeclared_completion_evidence_cannot_reset_durable_stagnation():
    context = Context(task_id="semantic-undeclared-completion-evidence")
    context.configure_completion_contract(
        CompletionContract(
            required_artifacts=(),
            immutable_inputs=(),
            validation_commands=(),
            max_evidence_age_seconds=None,
            required_final_evidence=(),
            required_self_check_ids=("required-check",),
        ),
        mode=CompletionMode.ENFORCE,
    )
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            semantic_progress_enabled=True,
        ),
    )

    for index in range(1, 4):
        context.record_completion_self_check(
            SelfCheckEvidence(
                command_id=f"diagnostic-{index}",
                exit_code=0,
                output_hash=f"sha256:diagnostic-{index}",
                observed_at=datetime.now(timezone.utc),
            )
        )
        state = record_semantic_tool_progress(
            context,
            tool_name="terminal",
            agent_id="agent",
            actions=[ActionModel(tool_name="terminal", action_name="execute")],
            observation=Observation(
                action_result=[
                    ActionResult(content=f"diagnostic {index} passed", success=True)
                ]
            ),
        )
        assert state["completion_score"] == [0, -1]
        assert state["completion_advanced"] is False
        assert state["durable_milestone_advanced"] is False
        assert state["goal_progress"] is False

    assert state["durable_stagnation_count"] == 3


def test_completion_regression_cannot_replay_an_old_high_water_milestone():
    context = Context(task_id="semantic-completion-high-water")
    context.configure_completion_contract(
        CompletionContract(
            required_artifacts=(),
            immutable_inputs=(),
            validation_commands=(),
            max_evidence_age_seconds=None,
            required_final_evidence=(),
            required_self_check_ids=("check-a", "check-b"),
        ),
        mode=CompletionMode.ENFORCE,
    )
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            semantic_progress_enabled=True,
        ),
    )

    states = []
    for index, exit_code in enumerate((1, 0, 1, 0, 1, 0)):
        context.record_completion_self_check(
            SelfCheckEvidence(
                command_id="check-a",
                exit_code=exit_code,
                output_hash=f"sha256:check-a-{exit_code}",
                observed_at=datetime.now(timezone.utc),
            )
        )
        states.append(
            record_semantic_tool_progress(
                context,
                tool_name="terminal",
                agent_id="agent",
                actions=[ActionModel(tool_name="terminal", action_name="execute")],
                observation=Observation(
                    action_result=[
                        ActionResult(content=f"check-a exit {exit_code}", success=True)
                    ]
                ),
            )
        )

    assert [state["completion_score"] for state in states] == [
        [0, -2],
        [1, -1],
        [0, -2],
        [1, -1],
        [0, -2],
        [1, -1],
    ]
    assert [state["completion_advanced"] for state in states] == [
        False,
        True,
        False,
        False,
        False,
        False,
    ]
    assert states[-1]["completion_high_water_score"] == [1, -1]
    assert states[-1]["goal_progress"] is False
    assert states[-1]["durable_stagnation_count"] == 4


def test_completion_high_water_is_reset_for_a_new_task_epoch():
    context = Context(task_id="semantic-scope-task-1")
    context.configure_completion_contract(
        CompletionContract(
            required_artifacts=(),
            immutable_inputs=(),
            validation_commands=(),
            max_evidence_age_seconds=None,
            required_final_evidence=(),
            required_self_check_ids=("check-a", "check-b"),
        ),
        mode=CompletionMode.ENFORCE,
    )
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            semantic_progress_enabled=True,
        ),
    )
    for command_id in ("check-a", "check-b"):
        context.record_completion_self_check(
            SelfCheckEvidence(
                command_id=command_id,
                exit_code=0,
                output_hash=f"sha256:{command_id}",
                observed_at=datetime.now(timezone.utc),
            )
        )
    first = record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[ActionModel(tool_name="terminal", action_name="execute")],
        observation=Observation(
            action_result=[ActionResult(content="task one passed", success=True)]
        ),
    )
    assert first["completion_high_water_score"] == [2, 0]
    first_scope = first["scope"]

    context.task_id = "semantic-scope-task-2"
    context.configure_completion_contract(
        CompletionContract(
            required_artifacts=(),
            immutable_inputs=(),
            validation_commands=(),
            max_evidence_age_seconds=None,
            required_final_evidence=(),
            required_self_check_ids=("check-c",),
        ),
        mode=CompletionMode.ENFORCE,
    )
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            semantic_progress_enabled=True,
        ),
    )
    context.record_completion_self_check(
        SelfCheckEvidence(
            command_id="check-c",
            exit_code=0,
            output_hash="sha256:check-c",
            observed_at=datetime.now(timezone.utc),
        )
    )
    second = record_semantic_tool_progress(
        context,
        tool_name="terminal",
        agent_id="agent",
        actions=[ActionModel(tool_name="terminal", action_name="execute")],
        observation=Observation(
            action_result=[ActionResult(content="task two passed", success=True)]
        ),
    )

    assert second["scope"] != first_scope
    assert second["scope"]["task_id"] == "semantic-scope-task-2"
    assert second["completion_score"] == [1, 0]
    assert second["completion_high_water_score"] == [1, 0]
    assert second["completion_advanced"] is True
    assert second["goal_progress"] is True


def test_opaque_workspace_churn_does_not_suppress_advisory_replan():
    context = Context(task_id="semantic-opaque-workspace-churn")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            semantic_progress_enabled=True,
        ),
    )

    for index in range(6):
        action = ActionModel(
            tool_name="terminal",
            action_name="run_code",
            tool_call_id=f"opaque-{index}",
            params={"code": f"download-or-install-stage-{index}"},
        )
        state = record_semantic_tool_progress(
            context,
            tool_name="terminal",
            agent_id="agent",
            actions=[action],
            observation=Observation(
                action_result=[
                    ActionResult(
                        tool_call_id=action.tool_call_id,
                        content=f"novel diagnostic {index}",
                        success=True,
                        metadata={
                            "context_management": {
                                "schema_version": "aworld.sandbox-artifact-progress/v1",
                                "artifact_changed": True,
                                "artifact_fingerprint_after": f"workspace-{index}",
                            },
                            "sandbox_observation": _sandbox_receipt(
                                action,
                                index + 1,
                                effect="unknown",
                                workspace_mutated=True,
                            ),
                        },
                    )
                ]
            ),
        )

    assert state["artifact_advanced"] is True
    assert state["diagnostic_progress_observable"] is True
    assert state["durable_milestone_advanced"] is False
    assert state["goal_progress"] is False
    assert state["durable_stagnation_count"] == 6
    protocol_state = load_execution_protocol_state(context, "agent")
    assert protocol_state.replan_count == 1
    assert protocol_state.finalization_entered is False
    guidance = consume_execution_protocol_guidance(context, "agent")
    assert guidance is not None
    assert "inspectable milestone evidence" in guidance


def test_r5_shaped_failure_and_workspace_churn_requests_replan():
    context = Context(task_id="semantic-r5-shape")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            semantic_progress_enabled=True,
        ),
    )

    states = []
    for index in range(10):
        failed = index in {1, 4, 7}
        action = ActionModel(
            tool_name="terminal",
            action_name="run_code",
            tool_call_id=f"r5-{index}",
            params={"code": f"explore-prerequisite-{index}"},
        )
        states.append(
            record_semantic_tool_progress(
                context,
                tool_name="terminal",
                agent_id="agent",
                actions=[action],
                observation=Observation(
                    action_result=[
                        ActionResult(
                            tool_call_id=action.tool_call_id,
                            content=f"diagnostic {index}",
                            success=not failed,
                            error=(
                                f"network prerequisite {index} failed"
                                if failed
                                else None
                            ),
                            metadata={
                                "context_management": {
                                    "schema_version": "aworld.sandbox-artifact-progress/v1",
                                    "artifact_changed": index in {2, 5, 8},
                                    "artifact_fingerprint_after": f"workspace-{index}",
                                },
                                "sandbox_observation": _sandbox_receipt(
                                    action,
                                    index + 1,
                                    effect="unknown",
                                    workspace_mutated=index in {2, 5, 8},
                                ),
                            },
                        )
                    ]
                ),
            )
        )

    state = states[-1]
    assert any(item["semantic_progress"] is True for item in states)
    assert state["diagnostic_progress_observable"] is True
    assert state["goal_progress_observable"] is None
    assert state["goal_progress"] is False
    assert state["durable_stagnation_count"] == 10
    assert state["low_information_gain_count"] == 10
    protocol_state = load_execution_protocol_state(context, "agent")
    assert protocol_state.replan_count == 1
    assert protocol_state.finalization_entered is False


def test_repeated_failures_remain_diagnostic_without_goal_channel():
    context = Context(task_id="semantic-failure-observable")
    configure_execution_protocol(
        context,
        "agent",
        ExecutionProtocolPolicy(
            mode=ProtocolMode.GUIDE,
            semantic_progress_enabled=True,
        ),
    )

    first = _record_failure(context, 0)
    repeated = _record_failure(context, 1)

    assert first["diagnostic_progress_observable"] is True
    assert repeated["diagnostic_progress_observable"] is True
    assert first["goal_progress_observable"] is None
    assert repeated["goal_progress_observable"] is None
    assert repeated["no_goal_progress_count"] == 0
