from __future__ import annotations

import json

import pytest

import aworld.agents.llm_agent as module
from aworld.agents.llm_agent import (
    LlmOutputParser,
    ModelToolCallSurface,
    ToolCallBatchParseError,
    ToolCallParseIssueCode,
)
from aworld.core.context.execution_state import get_execution_state
from aworld.core.context.generation_budget import GenerationBudgetPolicy
from aworld.models.model_response import Function, ModelResponse, ToolCall
from tests.core.agent.test_generation_action_budget import _agent, _message


@pytest.fixture(autouse=True)
def silence_events(monkeypatch):
    async def noop(*args, **kwargs):
        pass
    monkeypatch.setattr(module, "send_message", noop)


def tool(arguments, call_id="call-1"):
    return ToolCall(id=call_id, function=Function(name="workspace__write", arguments=arguments))


@pytest.mark.parametrize(
    ("names", "choice", "finish", "terminal", "markup"),
    [
        ({"unrelated"}, None, "stop", True, "complete"),
        ({"run_code"}, "none", "stop", True, "complete"),
        ({"run_code", "unrelated"}, {"function": {"name": "unrelated"}}, "stop", True, "complete"),
        ({"run_code"}, None, "length", True, "complete"),
        ({"run_code"}, None, "stop", False, "complete"),
        ({"run_code"}, None, "stop", True, "partial"),
        ({"run_code"}, None, "stop", True, "extra_open"),
        ({"run_code"}, None, "stop", True, "nested_open"),
    ],
)
def test_reasoning_only_recovery_rejects_incomplete_or_unauthorized_batches(
    names, choice, finish, terminal, markup,
):
    opening = '<｜DSML｜tool_calls><｜DSML｜invoke name="terminal">'
    complete = (
        opening
        + '<｜DSML｜parameter name="cmd" string="true">pwd</｜DSML｜parameter>'
        + '</｜DSML｜invoke></｜DSML｜tool_calls>'
    )
    reasoning = {
        "complete": complete,
        "partial": complete.removesuffix("</｜DSML｜tool_calls>"),
        "extra_open": complete + opening,
        "nested_open": complete.replace(
            opening, opening + '<｜DSML｜invoke name="run_code"><｜DSML｜invoke name="run_code">'
        ),
    }[markup]
    response = ModelResponse(
        id="reasoning-batch",
        model="fixture",
        reasoning_content=reasoning,
        finish_reason=finish,
        message={"aworld_stream_terminal_observed": terminal},
    )
    agent = _agent(policy=GenerationBudgetPolicy())
    surface = ModelToolCallSurface("Aworld", "test", 0, frozenset(names))
    assert agent._recover_textual_response_tool_calls(
        response, surface=surface, tool_choice=choice,
    )
    assert not response.tool_calls
    assert "tool_calls" not in response.message


def test_active_skill_usage_is_bound_to_final_model_messages() -> None:
    agent = _agent(policy=GenerationBudgetPolicy(total_timeout_seconds=5))
    agent.skill_configs = {
        "spreadsheet-recovery": {
            "active": True,
            "skill_path": "/installed-agent/skills/spreadsheet-recovery/SKILL.md",
            "usage": "Inspect formulas first, then preserve workbook formatting.",
        },
        "unrelated": {
            "active": False,
            "skill_path": "/installed-agent/skills/unrelated/SKILL.md",
            "usage": "Must not be injected.",
        },
    }

    projected = agent._inject_active_skill_instructions(
        [{"role": "user", "content": "repair the workbook"}]
    )

    assert projected[0]["role"] == "system"
    assert "<aworld_active_skill_instructions>" in projected[0]["content"]
    assert "spreadsheet-recovery" in projected[0]["content"]
    assert "Inspect formulas first" in projected[0]["content"]
    assert "/installed-agent/skills/spreadsheet-recovery/SKILL.md" in (
        projected[0]["content"]
    )
    assert "Must not be injected" not in projected[0]["content"]
    assert projected[1] == {"role": "user", "content": "repair the workbook"}
    assert agent._inject_active_skill_instructions(projected) == projected


def test_mounted_task_skill_requires_real_tool_evidence_before_completion() -> None:
    agent = _agent(policy=GenerationBudgetPolicy(total_timeout_seconds=5))
    agent.skill_configs = {
        "spreadsheet-recovery": {
            "active": True,
            "skill_path": (
                "/installed-agent/aworld-authorized-skills/"
                "spreadsheet-recovery/SKILL.md"
            ),
            "usage": "Repair and validate the workbook.",
        }
    }
    context = _message("task-skill-evidence").context

    feedback = agent._task_skill_tool_evidence_feedback_if_unsatisfied(context)

    assert feedback is not None
    assert "no Tool action has executed" in feedback


def test_builtin_skill_does_not_force_task_skill_tool_evidence() -> None:
    agent = _agent(policy=GenerationBudgetPolicy(total_timeout_seconds=5))
    agent.skill_configs = {
        "long-running-agent": {
            "active": True,
            "skill_path": "/opt/aworld/builtin/long-running-agent/SKILL.md",
            "usage": "Continue until complete.",
        }
    }
    context = _message("builtin-skill-evidence").context

    assert agent._task_skill_tool_evidence_feedback_if_unsatisfied(context) is None


@pytest.mark.asyncio
async def test_textual_dsml_tool_call_is_recovered_against_live_surface():
    response = ModelResponse(
        id="dsml-terminal",
        model="fake",
        content=(
            '<｜DSML｜tool_calls><｜DSML｜invoke name="terminal">'
            '<｜DSML｜parameter name="command" string="true">'
            "ls -la /workspace/"
            "</｜DSML｜parameter>"
            '<｜DSML｜parameter name="description" string="true">'
            "Inspect workspace"
            "</｜DSML｜parameter>"
            "</｜DSML｜invoke></｜DSML｜tool_calls>"
            "Work remains in progress."
        ),
    )
    surface = ModelToolCallSurface(
        agent_id="Aworld",
        task_id="task-1",
        task_epoch=0,
        tool_names=frozenset({"terminal"}),
    )

    parsed = await LlmOutputParser().parse(
        response,
        agent_id="Aworld",
        tool_call_surface=surface,
    )

    assert parsed.is_call_tool is True
    assert len(parsed.actions) == 1
    assert parsed.actions[0].tool_name == "terminal"
    assert parsed.actions[0].params == {
        "command": "ls -la /workspace/",
        "description": "Inspect workspace",
    }
    assert parsed.actions[0].policy_info == "Work remains in progress."


@pytest.mark.asyncio
async def test_reasoning_dsml_false_string_mode_recovers_before_final_text():
    response = ModelResponse(
        id="dsml-reasoning-terminal",
        model="fake",
        reasoning_content=(
            '<｜DSML｜tool_calls><｜DSML｜invoke name="terminal">'
            '<｜DSML｜parameter name="commands" string="false">'
            "find /installed-agent/aworld-authorized-skills -name SKILL.md"
            "</｜DSML｜parameter>"
            '<｜DSML｜parameter name="workdir" string="true">/root'
            "</｜DSML｜parameter>"
            "</｜DSML｜invoke></｜DSML｜tool_calls>"
        ),
        content="No tool access is available in this recovery turn.",
    )
    surface = ModelToolCallSurface(
        agent_id="Aworld",
        task_id="task-1",
        task_epoch=0,
        tool_names=frozenset({"terminal"}),
    )

    parsed = await LlmOutputParser().parse(
        response,
        agent_id="Aworld",
        tool_call_surface=surface,
    )

    assert parsed.is_call_tool is True
    assert parsed.actions[0].tool_name == "terminal"
    assert parsed.actions[0].params == {
        "commands": (
            "find /installed-agent/aworld-authorized-skills -name SKILL.md"
        ),
        "workdir": "/root",
    }
    assert parsed.actions[0].policy_info == (
        "No tool access is available in this recovery turn."
    )


@pytest.mark.asyncio
async def test_textual_terminal_alias_normalizes_to_live_run_code_contract():
    response = ModelResponse(
        id="dsml-terminal-alias",
        model="fake",
        reasoning_content=(
            '<｜DSML｜tool_calls><｜DSML｜invoke name="terminal">'
            '<｜DSML｜parameter name="cmd" string="true">'
            "ls -la /installed-agent/aworld-authorized-skills"
            "</｜DSML｜parameter>"
            '<｜DSML｜parameter name="timeout" string="false">20000'
            "</｜DSML｜parameter>"
            '<｜DSML｜parameter name="description" string="true">'
            "Inspect active task skills"
            "</｜DSML｜parameter>"
            "</｜DSML｜invoke></｜DSML｜tool_calls>"
        ),
    )
    surface = ModelToolCallSurface(
        agent_id="Aworld",
        task_id="task-1",
        task_epoch=0,
        tool_names=frozenset({"run_code"}),
    )

    parsed = await LlmOutputParser().parse(
        response,
        agent_id="Aworld",
        tool_call_surface=surface,
    )

    assert parsed.is_call_tool is True
    assert parsed.actions[0].tool_name == "run_code"
    assert parsed.actions[0].params == {
        "code": "ls -la /installed-agent/aworld-authorized-skills",
        "timeout": 20.0,
    }


@pytest.mark.asyncio
async def test_textual_nested_terminal_run_code_wrapper_is_recovered():
    response = ModelResponse(
        id="dsml-nested-terminal",
        model="fake",
        reasoning_content=(
            '<｜DSML｜tool_calls><｜DSML｜invoke name="terminal">'
            '<｜DSML｜invoke name="run_code">'
            '<｜DSML｜parameter name="code" string="true">'
            "find /root -maxdepth 2 -type f"
            "</｜DSML｜parameter>"
            # This is the exact provider shape observed in production: it opens
            # a terminal wrapper and an inner run_code call, but emits only one
            # invoke close. The chain is still singular and unambiguous.
            "</｜DSML｜invoke></｜DSML｜tool_calls>"
        ),
    )
    surface = ModelToolCallSurface(
        agent_id="Aworld",
        task_id="task-1",
        task_epoch=0,
        tool_names=frozenset({"run_code"}),
    )

    parsed = await LlmOutputParser().parse(
        response,
        agent_id="Aworld",
        tool_call_surface=surface,
    )

    assert parsed.is_call_tool is True
    assert parsed.actions[0].tool_name == "run_code"
    assert parsed.actions[0].params == {
        "code": "find /root -maxdepth 2 -type f"
    }


@pytest.mark.asyncio
async def test_textual_terminal_commands_list_is_joined_without_evaluation():
    response = ModelResponse(
        id="dsml-terminal-commands",
        model="fake",
        content=(
            '<｜DSML｜tool_calls><｜DSML｜invoke name="terminal">'
            '<｜DSML｜parameter name="commands" string="false">'
            "['pwd', 'find . -name SKILL.md']"
            "</｜DSML｜parameter>"
            '<｜DSML｜parameter name="workdir" string="true">/root'
            "</｜DSML｜parameter>"
            "</｜DSML｜invoke></｜DSML｜tool_calls>"
        ),
    )
    surface = ModelToolCallSurface(
        agent_id="Aworld",
        task_id="task-1",
        task_epoch=0,
        tool_names=frozenset({"run_code"}),
    )

    parsed = await LlmOutputParser().parse(
        response,
        agent_id="Aworld",
        tool_call_surface=surface,
    )

    assert parsed.is_call_tool is True
    assert parsed.actions[0].params == {
        "code": "pwd\nfind . -name SKILL.md",
        "cwd": "/root",
    }


@pytest.mark.asyncio
async def test_textual_dsml_uses_unique_live_surface_suffix_alias():
    response = ModelResponse(
        id="dsml-decision",
        model="fake",
        content=(
            '<｜DSML｜tool_calls><｜DSML｜invoke name="execution_decision">'
            '<｜DSML｜parameter name="payload" string="true">continue'
            "</｜DSML｜parameter>"
            "</｜DSML｜invoke></｜DSML｜tool_calls>"
        ),
    )
    surface = ModelToolCallSurface(
        agent_id="Aworld",
        task_id="task-1",
        task_epoch=0,
        tool_names=frozenset({"aworld__execution_decision"}),
    )

    parsed = await LlmOutputParser().parse(
        response,
        agent_id="Aworld",
        tool_call_surface=surface,
    )

    assert parsed.is_call_tool is True
    assert parsed.actions[0].tool_name == "aworld"
    assert parsed.actions[0].action_name == "execution_decision"
    assert parsed.actions[0].params == {"payload": "continue"}


@pytest.mark.asyncio
async def test_textual_dsml_unknown_tool_is_a_typed_non_executable_error():
    raw = (
        '<｜DSML｜tool_calls><｜DSML｜invoke name="forbidden">'
        '<｜DSML｜parameter name="command" string="true">id'
        "</｜DSML｜parameter>"
        "</｜DSML｜invoke></｜DSML｜tool_calls>"
    )
    response = ModelResponse(id="dsml-forbidden", model="fake", content=raw)
    surface = ModelToolCallSurface(
        agent_id="Aworld",
        task_id="task-1",
        task_epoch=0,
        tool_names=frozenset({"terminal"}),
    )

    with pytest.raises(ToolCallBatchParseError) as raised:
        await LlmOutputParser().parse(
            response,
            agent_id="Aworld",
            tool_call_surface=surface,
        )

    assert raised.value.issues[0].code is (
        ToolCallParseIssueCode.MALFORMED_TEXTUAL_TOOL_BATCH
    )


@pytest.mark.parametrize(
    "content",
    (
        "I need to see the rest of sim.c first. Let me read the rest of the file.",
        "The download is at 86%. Let me wait for it to complete.",
        "Next, I will run the focused tests.",
        "I am going to continue with the implementation",
    ),
)
def test_final_response_wording_does_not_override_model_completion(content):
    response = ModelResponse(
        id="future-work", model="fake", content=content, finish_reason="stop"
    )

    assert (
        module.LLMAgent._incomplete_model_response_reason(response)
        is None
    )


@pytest.mark.parametrize(
    "content",
    (
        "Implemented the change and all focused tests pass.",
        "The next step for an operator is deployment.",
        "Everything is complete. Let me know if you need anything else.",
        "The phrase `Let me read the file` is an example of unfinished work.",
        "```text\nLet me read the file.\n```\nThe analysis is complete.",
    ),
)
def test_completion_detector_does_not_reject_reports_or_conversational_closers(content):
    response = ModelResponse(
        id="complete", model="fake", content=content, finish_reason="stop"
    )

    assert module.LLMAgent._incomplete_model_response_reason(response) is None


@pytest.mark.asyncio
async def test_model_stop_does_not_trigger_a_wording_based_retry(monkeypatch):
    calls = []

    async def response(*args, **kwargs):
        calls.append(kwargs["messages"])
        if len(calls) == 1:
            return ModelResponse(
                id="unfinished",
                model="fake",
                content="The download is at 86%. Let me wait for it to complete.",
                finish_reason="stop",
            )
        return ModelResponse(
            id="complete",
            model="fake",
            content="The download completed and the requested artifacts were generated.",
            finish_reason="stop",
        )

    monkeypatch.setattr(module, "acall_llm_model", response)
    agent = _agent(policy=GenerationBudgetPolicy(total_timeout_seconds=5), attempts=2)
    message = _message("future-work-recovery")

    result = await agent.invoke_model(
        [{"role": "user", "content": "finish the download"}],
        message=message,
        stream=False,
    )

    assert len(calls) == 1
    assert result.content.startswith("The download is at 86%")


@pytest.mark.asyncio
async def test_stream_length_response_is_recovered_before_tool_execution(monkeypatch):
    calls = []
    async def stream(*args, **kwargs):
        calls.append(kwargs["messages"])
        if len(calls) == 1:
            yield ModelResponse(id="cut", model="fake", content="I will write ",
                                tool_calls=[tool('{"path":"out.txt"}')])
            yield ModelResponse(id="cut", model="fake", finish_reason="length")
        else:
            yield ModelResponse(id="complete", model="fake", tool_calls=[tool('{"path":"out.txt","text":"done"}')])
            yield ModelResponse(id="complete", model="fake", finish_reason="tool_calls")
    monkeypatch.setattr(module, "acall_llm_model_stream", stream)
    agent = _agent(policy=GenerationBudgetPolicy(total_timeout_seconds=5), attempts=2)
    message = _message("length-recovery")
    result = await agent.invoke_model([{"role": "user", "content": "write"}], message=message, stream=True)
    assert len(calls) == 2
    assert result.finish_reason == "tool_calls"
    assert json.loads(result.tool_calls[0].function.arguments)["text"] == "done"
    assert calls[1][-2]["role"] == "assistant"
    assert "I will write" in calls[1][-2]["content"]
    assert "not a final answer or executable action" in calls[1][-2]["content"]
    assert "No tool calls" in calls[1][-1]["content"]
    assert get_execution_state(message.context)["status"] == "running"


@pytest.mark.asyncio
async def test_provider_resolution_uses_request_start_blocker_watermark(monkeypatch):
    calls = 0
    agent = _agent(policy=GenerationBudgetPolicy(total_timeout_seconds=5), attempts=1)
    message = _message("provider-watermark")

    async def response(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            from aworld.core.context.execution_state import record_execution_state

            # Simulate a newer concurrent provider boundary becoming incomplete
            # while this older request is still in flight.
            record_execution_state(
                message.context,
                agent.id(),
                "incomplete",
                "model_output_truncated",
            )
        return ModelResponse(
            id=f"complete-{calls}",
            model="fake",
            tool_calls=[tool('{"path":"out.txt","text":"done"}', f"call-{calls}")],
            finish_reason="tool_calls",
        )

    monkeypatch.setattr(module, "acall_llm_model", response)

    await agent.invoke_model(
        [{"role": "user", "content": "write"}], message=message, stream=False
    )
    assert get_execution_state(message.context)["status"] == "incomplete"

    await agent.invoke_model(
        [{"role": "user", "content": "continue"}], message=message, stream=False
    )
    assert get_execution_state(message.context)["status"] == "running"


@pytest.mark.asyncio
async def test_internal_control_provider_action_does_not_resolve_solver_blocker(
    monkeypatch,
):
    from aworld.core.context.execution_state import record_execution_state

    agent = _agent(policy=GenerationBudgetPolicy(total_timeout_seconds=5), attempts=1)
    message = _message("internal-control-watermark")
    record_execution_state(
        message.context,
        agent.id(),
        "incomplete",
        "model_output_truncated",
    )

    async def response(*args, **kwargs):
        return ModelResponse(
            id="internal-control",
            model="fake",
            tool_calls=[tool('{"path":"decision.json","text":"continue"}')],
            finish_reason="tool_calls",
        )

    monkeypatch.setattr(module, "acall_llm_model", response)
    await agent.invoke_model(
        [{"role": "user", "content": "internal checkpoint"}],
        message=message,
        stream=False,
        _execution_state_resolution_mode="control",
    )

    state = get_execution_state(message.context)
    assert state["status"] == "incomplete"
    assert state["reason"] == "model_output_truncated"


@pytest.mark.asyncio
async def test_whitespace_only_provider_response_is_incomplete(monkeypatch):
    async def response(*args, **kwargs):
        return ModelResponse(
            id="whitespace",
            model="fake",
            content="  \n\t  ",
            finish_reason="stop",
        )

    monkeypatch.setattr(module, "acall_llm_model", response)
    agent = _agent(policy=GenerationBudgetPolicy(total_timeout_seconds=5), attempts=1)
    message = _message("whitespace-response")

    result = await agent.invoke_model(
        [{"role": "user", "content": "finish"}], message=message, stream=False
    )

    assert result.message["aworld_incomplete_reason"] == "empty_model_response"
    assert get_execution_state(message.context)["status"] == "incomplete"
    agent.context = message.context
    parsed = await LlmOutputParser().parse(result, agent_id=agent.id())
    assert agent.is_agent_finished(result, parsed) is False


def test_contract_resolution_uses_pre_assessment_watermark() -> None:
    from aworld.core.agent.base import AgentResult
    from aworld.core.common import ActionModel
    from aworld.core.context.compiler import CompletionContract, CompletionMode
    from aworld.core.context.execution_state import record_execution_state

    agent = _agent(policy=GenerationBudgetPolicy(total_timeout_seconds=5), attempts=1)
    context = _message("assessment-watermark").context
    context.configure_completion_contract(
        CompletionContract(
            required_artifacts=(),
            immutable_inputs=(),
            validation_commands=(),
            max_evidence_age_seconds=None,
            required_final_evidence=(),
        ),
        mode=CompletionMode.ENFORCE,
    )
    original_assessment = context.assess_completion_contract

    def assess(**kwargs):
        record_execution_state(
            context,
            agent.id(),
            "incomplete",
            "completion_contract_unsatisfied",
        )
        return original_assessment(**kwargs)

    context.assess_completion_contract = assess
    agent.context = context
    response = ModelResponse(
        id="complete",
        model="fake",
        content="done",
        finish_reason="stop",
    )
    parsed = AgentResult(
        actions=[ActionModel(agent_name=agent.id(), policy_info="done")],
        current_state=None,
        is_call_tool=False,
    )

    assert agent.is_agent_finished(response, parsed) is True
    state = get_execution_state(context)
    assert state["status"] == "incomplete"
    assert state["reason"] == "completion_contract_unsatisfied"


@pytest.mark.asyncio
async def test_reasoning_only_recovery_retains_a_bounded_working_tail(monkeypatch):
    calls = []
    reasoning = "discarded-prefix-" + "R" * 9000

    async def response(*args, **kwargs):
        calls.append(kwargs["messages"])
        if len(calls) == 1:
            return ModelResponse(
                id="reasoning",
                model="fake",
                reasoning_content=reasoning,
                finish_reason="stop",
            )
        return ModelResponse(
            id="complete",
            model="fake",
            tool_calls=[tool('{"path":"out.txt","text":"done"}')],
            finish_reason="tool_calls",
        )

    monkeypatch.setattr(module, "acall_llm_model", response)
    agent = _agent(policy=GenerationBudgetPolicy(total_timeout_seconds=5), attempts=2)
    message = _message("reasoning-tail-recovery")

    result = await agent.invoke_model(
        [{"role": "user", "content": "write"}],
        message=message,
        stream=False,
    )

    retained = calls[1][-2]
    assert retained["role"] == "assistant"
    assert "discarded-prefix" not in retained["content"]
    assert retained["content"].endswith("R" * 128)
    assert len(retained["content"]) < len(reasoning)
    assert "Return one complete, minimal Tool call" in calls[1][-1]["content"]
    assert result.finish_reason == "tool_calls"


@pytest.mark.asyncio
async def test_required_stream_terminal_reason_retries_implicit_eof(monkeypatch):
    calls = 0

    async def stream(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield ModelResponse(id="cut", model="fake", content="I will write")
            return
        yield ModelResponse(
            id="complete",
            model="fake",
            tool_calls=[tool('{"path":"out.txt","text":"done"}')],
        )
        yield ModelResponse(
            id="complete", model="fake", finish_reason="tool_calls"
        )

    monkeypatch.setenv("AWORLD_REQUIRE_STREAM_FINISH_REASON", "true")
    monkeypatch.setattr(module, "acall_llm_model_stream", stream)
    agent = _agent(policy=GenerationBudgetPolicy(total_timeout_seconds=5), attempts=2)
    message = _message("implicit-eof")

    result = await agent.invoke_model(
        [{"role": "user", "content": "write"}], message=message, stream=True
    )

    assert calls == 2
    assert result.finish_reason == "tool_calls"
    assert json.loads(result.tool_calls[0].function.arguments)["text"] == "done"


@pytest.mark.asyncio
@pytest.mark.parametrize("bad,reason", [
    (ModelResponse(id="x", model="f", content="Unfinished plan", finish_reason="length"), "model_output_truncated"),
    (ModelResponse(id="x", model="f", reasoning_content="Need more work", finish_reason="stop"), "reasoning_only_response"),
    (ModelResponse(id="x", model="f", reasoning_content="Need more work", finish_reason="length"), "reasoning_only_response"),
    (ModelResponse(id="x", model="f", tool_calls=[tool('{"path":')], finish_reason="tool_calls"), "incomplete_tool_arguments"),
    (ModelResponse(id="x", model="f", tool_calls=[tool('{}'), tool('[]', 'call-2')]), "invalid_tool_arguments"),
])
async def test_unusable_response_exhausts_bounded_recovery_truthfully(monkeypatch, bad, reason):
    calls = 0
    provider_calls = []
    async def response(*args, **kwargs):
        nonlocal calls
        calls += 1
        provider_calls.append(kwargs)
        return bad
    monkeypatch.setattr(module, "acall_llm_model", response)
    agent = _agent(policy=GenerationBudgetPolicy(total_timeout_seconds=5), attempts=3)
    message = _message(reason)
    result = await agent.invoke_model([{"role": "user", "content": "work"}], message=message)
    # One bounded action-projection continuation is enough for this logical
    # turn. Higher provider retry settings do not repeat the same long analysis;
    # the outer task loop retains authority to continue with a fresh Tool turn.
    assert calls == 2
    assert "tool_choice" not in provider_calls[0]
    assert provider_calls[1]["tool_choice"] == "required"
    assert result.tool_calls == []
    assert result.message["aworld_incomplete_reason"] == reason
    assert get_execution_state(message.context)["status"] == "incomplete"
    assert get_execution_state(message.context)["recoverable"] is True
    agent.context = message.context
    parsed = await LlmOutputParser().parse(result, agent_id=agent.id())
    assert not agent.is_agent_finished(result, parsed)


@pytest.mark.asyncio
async def test_deployed_probe_composition_never_finishes_reasoning_length_response(monkeypatch):
    # Same three boundaries as the deployed AST probe, now with real classes.
    agent = _agent(policy=GenerationBudgetPolicy(total_timeout_seconds=5))
    agent.context = _message("probe").context
    async def stream(*args, **kwargs):
        yield ModelResponse(id="probe", model="fake", reasoning_content="Need to implement before finishing.")
        yield ModelResponse(id="probe", model="fake", finish_reason="length")
    monkeypatch.setattr(module, "acall_llm_model_stream", stream)
    from aworld.core.context.generation_budget import GenerationBudgetController
    message = _message("probe")
    agent.context = message.context
    response = await agent._consume_model_stream(messages=[], message=message, tools=[],
        float_temperature=0.1, prompt_tokens_est=0,
        controller=GenerationBudgetController(GenerationBudgetPolicy(total_timeout_seconds=5)), request_kwargs={})
    assert response.finish_reason == "length"
    parsed = await LlmOutputParser().parse(response, agent_id=agent.id())
    assert not agent.is_agent_finished(response, parsed)
    assert get_execution_state(agent.context)["status"] == "incomplete"


@pytest.mark.asyncio
async def test_stream_chunks_do_not_emit_agent_info_logs(monkeypatch):
    agent = _agent(policy=GenerationBudgetPolicy(total_timeout_seconds=5))
    message = _message("bounded stream log")
    secret = "private-stream-payload"

    async def stream(*args, **kwargs):
        yield ModelResponse(
            id="bounded-log",
            model="fake",
            content=secret,
            reasoning_content=secret * 2,
            finish_reason="stop",
        )

    log_messages = []
    monkeypatch.setattr(module, "acall_llm_model_stream", stream)
    monkeypatch.setattr(module.logger, "info", log_messages.append)

    response = await agent._consume_model_stream(
        messages=[],
        message=message,
        tools=[],
        float_temperature=0.1,
        prompt_tokens_est=0,
        controller=module.GenerationBudgetController(
            GenerationBudgetPolicy(total_timeout_seconds=5)
        ),
        request_kwargs={},
    )

    assert response.content == secret
    assert response.reasoning_content == secret * 2
    assert log_messages == []


@pytest.mark.asyncio
async def test_loop_summary_cannot_turn_budget_stop_into_success():
    agent = _agent(policy=GenerationBudgetPolicy(total_timeout_seconds=5))
    message = _message("budget")
    message.context.context_info[f"agent_loop_budget_finalized:{agent.id()}"] = True
    await agent._resolve_completion_at_loop_budget(message)
    state = get_execution_state(message.context)
    assert state["status"] == "budget_exhausted"
    assert state["recoverable"] is True


@pytest.mark.asyncio
async def test_validation_repair_exhaustion_is_incomplete():
    from aworld.core.tool.base import Observation
    agent = _agent(policy=GenerationBudgetPolicy(total_timeout_seconds=5))
    message = _message("validation")
    agent._collect_result_validation_evidence = lambda context: {}
    message.context.context_info[agent._result_validation_retry_key(agent.id())] = 1
    await agent._retry_for_result_validation(validation_feedback="required artifact missing",
        observation=Observation(observer=agent.id(), content=""), info={}, message=message, kwargs={})
    assert get_execution_state(message.context)["status"] == "incomplete"


@pytest.mark.asyncio
async def test_public_contract_advisory_exhaustion_submits_preserved_candidate():
    from dataclasses import replace

    from aworld.core.common import ActionModel
    from aworld.core.execution_protocol import (
        ExecutionProtocolPolicy,
        ExecutionProtocolStore,
        ProtocolMode,
        ProtocolPhase,
    )
    from aworld.core.tool.base import Observation
    from aworld.runners.execution_protocol import (
        configure_execution_protocol,
        store_candidate_fallback,
    )
    from aworld.core.context.compiler.frozen_json import canonical_json_hash

    agent = _agent(policy=GenerationBudgetPolicy(total_timeout_seconds=5))
    message = _message("public-contract-advisory")
    protocol = ExecutionProtocolPolicy(
        mode=ProtocolMode.GUIDE,
        terminal_review_gate_enabled=True,
        independent_acceptance_enabled=False,
    )
    configure_execution_protocol(message.context, agent.id(), protocol)
    store = ExecutionProtocolStore(message.context, agent.id(), protocol)
    store.save(
        replace(
            store.load(),
            phase=ProtocolPhase.REVIEW,
            candidate_present=False,
            candidate_checkpoint_recorded=False,
            candidate_final_count=1,
            finalization_entered=True,
        )
    )
    candidate = [
        ActionModel(agent_name=agent.id(), policy_info="best current result")
    ]
    store_candidate_fallback(message.context, agent.id(), candidate)
    feedback = "The public contract evidence ledger is not closed."
    message.context.context_info[
        f"public_contract_validation_feedback:{agent.id()}"
    ] = canonical_json_hash({"feedback": feedback})
    message.context.context_info[
        f"public_contract_validation_retry_count:{agent.id()}"
    ] = 1

    result = await agent._retry_for_result_validation(
        validation_feedback=feedback,
        observation=Observation(observer=agent.id(), content=""),
        info={},
        message=message,
        kwargs={},
        candidate_actions=candidate,
    )

    assert result == candidate
    assert get_execution_state(message.context)["status"] == "succeeded"
    assert store.load().phase is ProtocolPhase.COMPLETE


@pytest.mark.asyncio
async def test_public_contract_cannot_bypass_explicit_completion_enforcement():
    from aworld.core.common import ActionModel
    from aworld.core.context.compiler import CompletionContract, CompletionMode
    from aworld.core.context.compiler.frozen_json import canonical_json_hash
    from aworld.core.tool.base import Observation

    agent = _agent(policy=GenerationBudgetPolicy(total_timeout_seconds=5))
    message = _message("enforced-public-contract")
    message.context.configure_completion_contract(
        CompletionContract(
            required_artifacts=(),
            immutable_inputs=(),
            validation_commands=(),
            max_evidence_age_seconds=None,
            required_final_evidence=(),
            max_repairs=1,
        ),
        mode=CompletionMode.ENFORCE,
    )
    message.context.context_info["completion_enforcement_explicit"] = True
    feedback = "The public contract evidence ledger is not closed."
    message.context.context_info[
        f"public_contract_validation_feedback:{agent.id()}"
    ] = canonical_json_hash({"feedback": feedback})
    message.context.context_info[
        f"public_contract_validation_retry_count:{agent.id()}"
    ] = 1

    result = await agent._retry_for_result_validation(
        validation_feedback=feedback,
        observation=Observation(observer=agent.id(), content=""),
        info={},
        message=message,
        kwargs={},
        candidate_actions=[
            ActionModel(agent_name=agent.id(), policy_info="candidate")
        ],
    )

    assert "not claiming success" in result[0].policy_info
    assert get_execution_state(message.context)["status"] == "incomplete"
