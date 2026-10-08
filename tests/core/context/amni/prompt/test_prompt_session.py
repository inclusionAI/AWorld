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


def test_prompt_session_checkpoint_and_tool_change_are_explicit_epoch_boundaries():
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
    assert tool_change.receipt["rollover_reason"] == "tool_catalog_change"
    assert tool_change.receipt["epoch_id"] == checkpoint.receipt["epoch_id"] + 1


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
