from aworld.core.context.amni import ApplicationContext
from aworld.core.context.amni.prompt.session import (
    PROMPT_SESSION_STATE_KEY,
    advance_prompt_session,
)


SCOPE = {
    "session_id": "session-1",
    "session_epoch": 0,
    "task_id": "task-1",
    "task_epoch": 0,
    "branch_id": "main",
    "agent_id": "agent",
    "provider_name": "openai",
    "model_name": "model",
}
TOOLS = [{"type": "function", "function": {"name": "run_code"}}]


def _advance(previous, messages, *, tools=TOOLS, checkpoint_revision=0):
    return advance_prompt_session(
        previous,
        messages=messages,
        tools=tools,
        scope=SCOPE,
        checkpoint_revision=checkpoint_revision,
        stable_prefix_hash="stable",
    )


def test_prompt_session_appends_without_rewriting_committed_wire_prefix():
    first_messages = [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "task"},
    ]
    first = _advance(None, first_messages)
    second = _advance(
        first.state,
        [*first_messages, {"role": "assistant", "content": "working"}],
    )

    assert first.receipt["rollover_reason"] == "initial"
    assert first.receipt["epoch_started"] is True
    assert first.receipt["epoch_rollover"] is False
    assert second.receipt["epoch_rollover"] is False
    assert second.receipt["mode"] == "append"
    assert second.messages[: len(first.messages)] == first.messages
    assert second.receipt["appended_message_count"] == 1


def test_prompt_session_makes_inserted_delta_an_explicit_epoch_boundary():
    first_messages = [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "prior"},
    ]
    first = _advance(None, first_messages)
    inserted = {"role": "user", "content": "new control delta"}
    second = _advance(
        first.state,
        [first_messages[0], inserted, *first_messages[1:]],
    )

    assert second.receipt["mode"] == "epoch_start"
    assert second.receipt["epoch_rollover"] is True
    assert second.receipt["rollover_reason"] == "projection_rewrite"
    assert second.messages == [first_messages[0], inserted, *first_messages[1:]]


def test_prompt_session_checkpoint_rolls_epoch_and_tool_change_starts_a_lane():
    first = _advance(None, [{"role": "user", "content": "task"}])
    checkpoint = _advance(
        first.state,
        [{"role": "user", "content": "checkpoint"}],
        checkpoint_revision=1,
    )
    tool_change = _advance(
        checkpoint.state,
        [{"role": "user", "content": "tool-free review"}],
        tools=[],
        checkpoint_revision=1,
    )

    assert checkpoint.receipt["rollover_reason"] == "context_checkpoint"
    assert checkpoint.receipt["epoch_id"] == first.receipt["epoch_id"] + 1
    assert tool_change.receipt["rollover_reason"] == "lane_start"
    assert tool_change.receipt["lane_switched"] is True
    assert tool_change.receipt["epoch_rollover"] is False


def test_prompt_session_restores_solver_lane_after_control_catalog_call():
    solver_messages = [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "task"},
    ]
    solver = _advance(None, solver_messages)
    decision_tools = [
        {
            "type": "function",
            "function": {"name": "aworld__execution_decision"},
        }
    ]
    decision = _advance(
        solver.state,
        [{"role": "system", "content": "decision"}],
        tools=decision_tools,
    )
    resumed = _advance(
        decision.state,
        [
            *solver_messages,
            {"role": "user", "content": "bounded decision feedback"},
        ],
    )

    assert decision.receipt["lane_switched"] is True
    assert decision.receipt["epoch_rollover"] is False
    assert resumed.receipt["lane_switched"] is True
    assert resumed.receipt["lane_restored"] is True
    assert resumed.receipt["epoch_id"] == solver.receipt["epoch_id"]
    assert resumed.receipt["epoch_rollover"] is False
    assert resumed.receipt["appended_message_count"] == 1
    assert resumed.messages[: len(solver.messages)] == solver.messages
    assert resumed.state["total_request_count"] == 3
    assert resumed.state["lane_total_request_count"] == 2
    inactive = resumed.state["inactive_prompt_lanes"]
    assert all("messages" not in lane for lane in inactive.values())


def test_prompt_session_evicts_old_control_lane_but_keeps_recent_solver_lane():
    def catalog(name):
        return [{"type": "function", "function": {"name": name}}]

    solver_messages = [{"role": "user", "content": "task"}]
    solver = _advance(None, solver_messages)
    solver_lane_id = solver.receipt["lane_id"]

    decision = _advance(
        solver.state,
        [{"role": "system", "content": "decision"}],
        tools=catalog("aworld__execution_decision"),
    )
    decision_lane_id = decision.receipt["lane_id"]
    solver_after_decision = _advance(
        decision.state,
        [*solver_messages, {"role": "assistant", "content": "after decision"}],
    )
    review = _advance(
        solver_after_decision.state,
        [{"role": "system", "content": "review"}],
        tools=catalog("terminal__execute_with_review"),
    )
    solver_after_review = _advance(
        review.state,
        [
            *solver_messages,
            {"role": "assistant", "content": "after decision"},
            {"role": "assistant", "content": "after review"},
        ],
    )
    control = _advance(
        solver_after_review.state,
        [{"role": "system", "content": "acceptance"}],
        tools=catalog("aworld__acceptance_probe"),
    )
    solver_recent = _advance(
        control.state,
        [
            *solver_messages,
            {"role": "assistant", "content": "after decision"},
            {"role": "assistant", "content": "after review"},
            {"role": "assistant", "content": "after acceptance"},
        ],
    )
    finalization = _advance(
        solver_recent.state,
        [{"role": "system", "content": "finalization"}],
        tools=catalog("aworld__finalization"),
    )

    inactive = finalization.state["inactive_prompt_lanes"]
    assert len(inactive) == 3
    assert decision_lane_id not in inactive
    assert solver_lane_id in inactive
    assert all("messages" not in lane for lane in inactive.values())

    restored_solver = _advance(
        finalization.state,
        [
            *solver_messages,
            {"role": "assistant", "content": "after decision"},
            {"role": "assistant", "content": "after review"},
            {"role": "assistant", "content": "after acceptance"},
            {"role": "assistant", "content": "resume solver"},
        ],
    )
    assert restored_solver.receipt["lane_restored"] is True
    assert restored_solver.receipt["epoch_rollover"] is False
    assert restored_solver.receipt["appended_message_count"] == 1


def test_inactive_prompt_lane_does_not_duplicate_large_prompt_bodies():
    large_body = "large-solver-prefix:" + ("x" * (256 * 1024))
    solver = _advance(
        None,
        [{"role": "user", "content": large_body}],
    )
    control = _advance(
        solver.state,
        [{"role": "system", "content": "bounded control"}],
        tools=[
            {
                "type": "function",
                "function": {"name": "aworld__execution_decision"},
            }
        ],
    )

    inactive = control.state["inactive_prompt_lanes"][solver.receipt["lane_id"]]
    assert "messages" not in inactive
    assert inactive["message_count"] == 1
    assert len(inactive["message_fingerprints"]) == 1
    assert "large-solver-prefix" not in repr(inactive)
    assert len(repr(inactive)) < 8_000


def test_appended_system_delta_does_not_break_an_exact_wire_prefix():
    first_messages = [{"role": "system", "content": "base"}]
    first = _advance(None, first_messages)
    second = advance_prompt_session(
        first.state,
        messages=[
            *first_messages,
            {"role": "system", "content": "dynamic tail delta"},
        ],
        tools=TOOLS,
        scope=SCOPE,
        checkpoint_revision=0,
        stable_prefix_hash="expanded-stable-observation",
    )

    assert second.receipt["stable_prefix_changed"] is True
    assert second.receipt["epoch_rollover"] is False
    assert second.messages[: len(first.messages)] == first.messages


def test_prompt_session_surfaces_unexplained_projection_rewrite():
    first = _advance(
        None,
        [
            {"role": "user", "content": "task"},
            {"role": "assistant", "content": "old history"},
        ],
    )
    rewritten = _advance(
        first.state,
        [
            {"role": "user", "content": "task"},
            {"role": "assistant", "content": "replacement summary"},
        ],
    )

    assert rewritten.receipt["epoch_rollover"] is True
    assert rewritten.receipt["rollover_reason"] == "projection_rewrite"
    assert rewritten.receipt["candidate_common_prefix_messages"] == 1


def test_application_context_shares_prompt_session_across_transport_copy():
    context = ApplicationContext.create(
        session_id="session-1",
        task_id="task-1",
        task_content="task",
    )
    first_messages, first_receipt = context.materialize_append_only_prompt_session(
        namespace="agent",
        messages=[{"role": "user", "content": "task"}],
        tools=TOOLS,
        provider_name="openai",
        model_name="model",
        stable_prefix_hash="stable",
    )
    transported = context.deep_copy()
    second_messages, second_receipt = (
        transported.materialize_append_only_prompt_session(
            namespace="agent",
            messages=[
                {"role": "user", "content": "task"},
                {"role": "assistant", "content": "next"},
            ],
            tools=TOOLS,
            provider_name="openai",
            model_name="model",
            stable_prefix_hash="stable",
        )
    )

    assert second_receipt["epoch_id"] == first_receipt["epoch_id"]
    assert second_receipt["epoch_rollover"] is False
    assert second_messages[: len(first_messages)] == first_messages
    state = context.read_task_runtime_state("agent", PROMPT_SESSION_STATE_KEY)
    assert state["wire_messages_hash"] == second_receipt["wire_messages_hash"]


def test_application_context_records_provider_cache_evidence_per_session():
    context = ApplicationContext.create(
        session_id="session-1",
        task_id="task-1",
        task_content="task",
    )
    context.materialize_append_only_prompt_session(
        namespace="agent",
        messages=[{"role": "user", "content": "task"}],
        tools=TOOLS,
        provider_name="openai",
        model_name="model",
        stable_prefix_hash="stable",
    )

    context.record_append_only_prompt_cache_usage(
        namespace="agent",
        cache_hit_tokens=120,
        cache_write_tokens=30,
    )
    context.record_append_only_prompt_cache_usage(
        namespace="agent",
        cache_hit_tokens=0,
        cache_write_tokens=20,
    )

    evidence = context.read_task_runtime_state("agent", PROMPT_SESSION_STATE_KEY)[
        "provider_cache_evidence"
    ]
    assert evidence == {
        "epoch_id": 1,
        "last_cache_hit_tokens": 0,
        "last_cache_write_tokens": 20,
        "observation_count": 2,
        "cache_hit_request_count": 1,
        "total_cache_hit_tokens": 120,
        "total_cache_write_tokens": 50,
        "epoch_observation_count": 2,
        "epoch_cache_hit_request_count": 1,
        "epoch_cache_hit_tokens": 120,
        "epoch_cache_write_tokens": 50,
    }

    context.advance_context_lifecycle("checkpoint")
    _, checkpoint_receipt = context.materialize_append_only_prompt_session(
        namespace="agent",
        messages=[{"role": "user", "content": "checkpoint"}],
        tools=TOOLS,
        provider_name="openai",
        model_name="model",
        stable_prefix_hash="stable",
    )
    context.record_append_only_prompt_cache_usage(
        namespace="agent",
        cache_hit_tokens=5,
        cache_write_tokens=0,
    )

    after_rollover = context.read_task_runtime_state("agent", PROMPT_SESSION_STATE_KEY)[
        "provider_cache_evidence"
    ]
    assert checkpoint_receipt["rollover_reason"] == "context_checkpoint"
    assert (
        checkpoint_receipt["prior_provider_cache_evidence"]["total_cache_hit_tokens"]
        == 120
    )
    assert after_rollover["observation_count"] == 3
    assert after_rollover["total_cache_hit_tokens"] == 125
    assert after_rollover["epoch_id"] == checkpoint_receipt["epoch_id"]
    assert after_rollover["epoch_observation_count"] == 1
    assert after_rollover["epoch_cache_hit_tokens"] == 5


def test_provider_cache_evidence_follows_the_active_prompt_lane():
    context = ApplicationContext.create(
        session_id="session-1",
        task_id="task-1",
        task_content="task",
    )
    solver_messages = [{"role": "user", "content": "task"}]
    context.materialize_append_only_prompt_session(
        namespace="agent",
        messages=solver_messages,
        tools=TOOLS,
        provider_name="openai",
        model_name="model",
        stable_prefix_hash="stable",
    )
    context.record_append_only_prompt_cache_usage(
        namespace="agent",
        cache_hit_tokens=120,
    )
    solver_state = context.read_task_runtime_state(
        "agent", PROMPT_SESSION_STATE_KEY
    )
    solver_lane_id = solver_state["active_lane_id"]

    context.materialize_append_only_prompt_session(
        namespace="agent",
        messages=[{"role": "system", "content": "review"}],
        tools=[],
        provider_name="openai",
        model_name="model",
        stable_prefix_hash="review",
        request_cache_scope_hash="review-effort",
    )
    context.record_append_only_prompt_cache_usage(
        namespace="agent",
        cache_write_tokens=40,
    )
    control_state = context.read_task_runtime_state(
        "agent", PROMPT_SESSION_STATE_KEY
    )
    assert control_state["active_lane_id"] != solver_lane_id
    assert control_state["provider_cache_evidence"]["total_cache_hit_tokens"] == 0
    assert control_state["provider_cache_evidence"]["total_cache_write_tokens"] == 40

    _, resumed_receipt = context.materialize_append_only_prompt_session(
        namespace="agent",
        messages=[
            *solver_messages,
            {"role": "assistant", "content": "resume"},
        ],
        tools=TOOLS,
        provider_name="openai",
        model_name="model",
        stable_prefix_hash="stable",
    )
    resumed_state = context.read_task_runtime_state(
        "agent", PROMPT_SESSION_STATE_KEY
    )

    assert resumed_receipt["lane_restored"] is True
    assert resumed_receipt["epoch_rollover"] is False
    assert resumed_state["active_lane_id"] == solver_lane_id
    assert resumed_state["provider_cache_evidence"]["total_cache_hit_tokens"] == 120
    assert resumed_state["provider_cache_evidence"]["total_cache_write_tokens"] == 0
