"""Native monotonic metrics survive errors, cancellation and session reuse."""
import asyncio
from types import SimpleNamespace

import pytest

from aworld.cli.trajectory import build_trajectory
from aworld.core.agent import Agent
from aworld.core.agent.messages import AssistantMessage, ToolCall
from aworld.core.agent.usage import TokenUsage
from aworld.core.session import create_session, RunOptions
from aworld.core.session.models import RunEvent
from aworld.core.tool.function import Tool


async def export(session, run, agent):
    result = await run.result()
    history = await session.history()
    events = []
    for run_id in dict.fromkeys(e.run_id for e in history):
        handle = await session.get_run(run_id)
        events.extend([event async for event in handle.events()])
    return build_trajectory(history, events=events, result=result, agent=agent)


def test_per_tool_metrics_are_real_attempts_and_current_run_is_not_session_total():
    async def check():
        class Model:
            count = 0
            async def complete(self, request):
                await asyncio.sleep(.015)
                self.count += 1
                if self.count % 2:
                    return AssistantMessage(tool_calls=tuple(ToolCall(f'{self.count}-{i}', 'bash', {'fail': i == 1})
                        for i in range(2)), usage=TokenUsage(20, 2))
                return AssistantMessage('done', usage=TokenUsage(30, 3))
        async def tool(arguments, context):
            await asyncio.sleep(.02)
            if arguments['fail']:
                raise ValueError('expected tool failure')
            return 'ok'
        agent = Agent(model=Model(), tools=[Tool('bash', '', {}, tool)])
        session = await create_session(agent=agent)
        try:
            await (await session.submit('first')).result()
            run = await session.submit('second')
            t = await export(session, run, agent)
            m = t['extra']['run_metrics']; total = t['final_metrics']['extra']
            assert (m['llm_request_count'], m['tool_call_count']) == (2, 2)
            assert (total['llm_request_count'], total['tool_call_count']) == (4, 4)
            tool = m['tool_metrics'][0]
            assert tool['tool_name'] == 'bash'
            assert (tool['completed_count'], tool['failed_count'], tool['interrupted_count']) == (1, 1, 0)
            assert tool['total_duration_ms'] >= 35
            assert m['total_model_wall_duration_ms'] >= 25
            assert m['total_run_duration_ms'] >= m['total_model_wall_duration_ms'] + tool['total_duration_ms']
            calls = [c for s in t['steps'] for c in s.get('tool_calls', [])]
            assert len(calls) == 4 and all(c['extra']['duration_ms'] >= 15 for c in calls)
            assert [c['extra']['status'] for c in calls] == ['completed', 'failed'] * 2
            times = [e.elapsed_ms async for e in run.events()]
            assert times == sorted(times) and all(v >= 0 for v in times)
        finally:
            await session.close()
    asyncio.run(check())


@pytest.mark.parametrize('during_tool', [False, True])
def test_deadline_retains_interrupted_call_without_fabricating_response(during_tool):
    async def check():
        class Model:
            async def complete(self, request):
                if not during_tool:
                    await asyncio.sleep(10)
                return AssistantMessage(tool_calls=(ToolCall('pending', 'bash', {}),), usage=TokenUsage(10, 1))
        async def tool(arguments, context):
            await asyncio.sleep(10)
        agent = Agent(model=Model(), tools=[Tool('bash', '', {}, tool)])
        session = await create_session(agent=agent)
        try:
            t = await export(session, await session.submit('timeout', options=RunOptions(timeout_seconds=.05)), agent)
            m = t['extra']['run_metrics']
            assert m['interrupted_call_count'] == 1
            assert m['llm_request_count'] == 1
            if during_tool:
                call = next(s for s in t['steps'] if s.get('tool_calls'))
                assert 'observation' not in call
                assert call['tool_calls'][0]['extra']['status'] == 'interrupted'
                assert call['tool_calls'][0]['extra']['duration_ms'] >= 35
                assert m['tool_metrics'][0]['interrupted_count'] == 1
            else:
                step = next(s for s in t['steps'] if s['source'] == 'agent')
                assert step['extra']['diagnostic']
                assert step['metrics']['extra']['model_wall_duration_ms'] >= 35
                assert m['model_calls'] == 1 and m['input_tokens'] is None
        finally:
            await session.close()
    asyncio.run(check())


def test_failed_model_attempt_has_timing_without_known_token_usage():
    async def check():
        class Model:
            async def complete(self, request):
                await asyncio.sleep(.02)
                raise ValueError('provider failed')
        agent = Agent(model=Model(), tools=[])
        session = await create_session(agent=agent)
        try:
            t = await export(session, await session.submit('failure'), agent)
            step = next(s for s in t['steps'] if s['source'] == 'agent')
            assert step['metrics']['extra']['status'] == 'failed'
            assert step['metrics']['extra']['model_duration_ms'] >= 15
            assert step['metrics'].get('prompt_tokens') is None
            assert t['extra']['run_metrics']['llm_request_count'] == 1
        finally:
            await session.close()
    asyncio.run(check())


def test_compaction_attempts_are_timed_separately_from_agent_turn_numbers():
    from aworld.cli.trajectory import _call_timings, _attach_timings, _timing_summary
    events = [RunEvent('r', 's', 1, 'context.summary.started', {'attempt': 1}, elapsed_ms=10),
        RunEvent('r', 's', 2, 'context.summary.finished', {'attempt': 1}, elapsed_ms=60),
        RunEvent('r', 's', 3, 'model.started', {'turn': 1}, elapsed_ms=65),
        RunEvent('r', 's', 4, 'model.finished', {'turn': 1}, elapsed_ms=100),
        RunEvent('r', 's', 5, 'run.finished', SimpleNamespace(stop_reason='end_turn'), elapsed_ms=105)]
    records, runs = _call_timings(events)
    steps = [{'source': 'agent', 'extra': {'run_id': 'r', 'purpose': 'compaction'}},
             {'source': 'agent', 'extra': {'run_id': 'r'}}]
    assert _attach_timings(steps, records) == []
    assert [s['metrics']['extra']['model_wall_duration_ms'] for s in steps] == [50, 35]
    assert _timing_summary(records, runs)['total_model_wall_duration_ms'] == 85
