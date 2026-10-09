"""Recover only empty final replies, without replaying effects or losing receipts."""
import asyncio
from functools import wraps

import pytest

from aworld.cli.trajectory import build_trajectory
from aworld.core.agent import Agent
from aworld.core.agent.messages import AssistantMessage, ToolCall
from aworld.core.agent.usage import ModelResponseError, TokenUsage
from aworld.core.session import RunOptions, create_session
from aworld.core.tool import Tool
from aworld.models.chat_completions import parse_response


def run_async(fn):
    @wraps(fn)
    def run(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return run


def empty(reason='stop', **fields):
    return {'usage': {'prompt_tokens': 10, 'completion_tokens': 12}, 'choices': [
        {'finish_reason': reason, 'message': {'role': 'assistant', 'content': None, **fields}}]}


class Model:
    def __init__(self, values):
        self.values = iter(values)
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        value = next(self.values)
        if isinstance(value, BaseException):
            raise value
        return parse_response(value) if isinstance(value, dict) else value


async def execute(model, **kwargs):
    agent = Agent(model=model, tools=[], response_retry_delay=0, **kwargs)
    session = await create_session(agent=agent)
    run = await session.submit('Deliver the file')
    result = await run.result()
    return agent, session, run, result


@run_async
async def test_retry_keeps_usage_and_diagnostics_without_synthetic_history():
    model = Model([empty(reasoning_content='private reasoning'), AssistantMessage('done', usage=TokenUsage(20, 3))])
    agent, session, run, result = await execute(model)
    assert result.status.value == 'completed'
    assert len(model.requests) == 2
    assert model.requests[0].messages == model.requests[1].messages
    assert 'previous response' in model.requests[1].system_prompt
    events = [e async for e in run.events()]
    assert sum(e.type == 'model.retry.scheduled' for e in events) == 1
    trajectory = build_trajectory(await session.history(), result=result, agent=agent, events=events)
    assert trajectory['extra']['run_metrics']['total_tokens'] == 45
    assert trajectory['extra']['run_metrics']['llm_request_count'] == 2
    failed = next(s for s in trajectory['steps'] if s.get('extra', {}).get('error_code'))
    assert failed['extra']['will_retry'] is True
    assert failed['extra']['diagnostics']['finish_reason'] == 'stop'
    assert failed['extra']['diagnostics']['reasoning_chars'] == 17
    assert 'private reasoning' not in str(trajectory)


@run_async
async def test_recovery_does_not_replay_completed_tool_calls():
    effects = []
    async def effect(arguments, context):
        effects.append(arguments)
        return 'written'
    model = Model([AssistantMessage(tool_calls=(ToolCall('write-once', 'save', {'x': 1}),)),
                   empty(), AssistantMessage('done')])
    agent = Agent(model=model, tools=[Tool('save', 'Save', {}, effect)], response_retry_delay=0)
    session = await create_session(agent=agent)
    result = await (await session.submit('save')).result()
    assert result.status.value == 'completed'
    assert effects == [{'x': 1}]
    assert model.requests[1].messages == model.requests[2].messages
    assert model.requests[2].messages[-1].role == 'tool'


@run_async
@pytest.mark.parametrize('retries,turns,calls', [(2, 20, 3), (0, 20, 1), (2, 1, 1), (2, 2, 2)])
async def test_retries_and_model_turns_are_both_bounded(retries, turns, calls):
    model = Model([empty()] * 10)
    _, _, _, result = await execute(model, response_retries=retries, max_turns=turns)
    assert result.status.value == 'failed'
    assert len(model.requests) == calls
    assert 'empty final response' in result.error.message


@run_async
@pytest.mark.parametrize('value', [empty('length'), empty(refusal='refused'),
    empty('tool_calls'), {'choices': []}, RuntimeError('network failure')])
async def test_non_empty_response_errors_are_not_retried(value):
    model = Model([value, AssistantMessage('must not run')])
    _, _, _, result = await execute(model)
    assert result.status.value == 'failed'
    assert len(model.requests) == 1


@run_async
async def test_generic_model_empty_message_uses_same_recovery():
    model = Model([AssistantMessage('  '), AssistantMessage('done')])
    _, _, _, result = await execute(model)
    assert result.output == 'done'
    assert len(model.requests) == 2


@run_async
async def test_success_resets_consecutive_empty_retry_budget():
    model = Model([empty(), AssistantMessage(tool_calls=(ToolCall('a', 'absent', {}),)),
                   empty(), AssistantMessage('done')])
    _, _, _, result = await execute(model, response_retries=1)
    assert result.output == 'done'
    assert len(model.requests) == 4


@run_async
async def test_cancellation_interrupts_retry_backoff():
    model = Model([empty(), AssistantMessage('must not run')])
    agent = Agent(model=model, tools=[], response_retry_delay=30)
    session = await create_session(agent=agent)
    run = await session.submit('task')
    async for event in run.events():
        if event.type == 'model.retry.scheduled':
            await run.cancel()
            break
    result = await asyncio.wait_for(run.result(), 1)
    assert result.status.value == 'cancelled'
    assert len(model.requests) == 1


@run_async
async def test_remaining_deadline_prevents_retry_backoff():
    model = Model([empty(), AssistantMessage('must not run')])
    agent = Agent(model=model, tools=[], response_retry_delay=30)
    session = await create_session(agent=agent)
    result = await (await session.submit('task', options=RunOptions(timeout_seconds=1))).result()
    assert result.status.value == 'failed'
    assert len(model.requests) == 1


@pytest.mark.parametrize('kwargs', [{'response_retries': True}, {'response_retries': 6},
    {'response_retry_delay': float('nan')}, {'response_retry_delay': -1}])
def test_invalid_recovery_policy_rejected_before_execution(kwargs):
    with pytest.raises(ValueError):
        Agent(model=Model([]), **kwargs)


def test_error_diagnostics_never_store_response_body_or_refusal():
    with pytest.raises(ModelResponseError) as error:
        parse_response(empty(refusal='private text', reasoning_content='secret chain'))
    assert error.value.code == 'invalid_response'
    assert error.value.diagnostics['refusal'] is True
    assert 'private text' not in str(error.value.diagnostics)
    assert 'secret chain' not in str(error.value.diagnostics)
