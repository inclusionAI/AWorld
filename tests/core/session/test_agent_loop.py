"""Execute the new loop and subagent tool without a provider or event bus."""

import asyncio
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from aworld.core.agent import Agent, Skill
from aworld.core.agent.messages import AssistantMessage, ToolCall, ToolResultMessage, UserMessage
from aworld.core.context import Context
from aworld.core.context.simple import ContextEntry
from aworld.core.session import RunStatus, create_session
from aworld.core.tool import Tool
from aworld.core.tool.subagent import subagent_tool


class ScriptedModel:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        return next(self.responses)


async def add(arguments, context):
    return arguments["a"] + arguments["b"]


def add_tool(function=add):
    return Tool("add", "Add two values", {"type": "object"}, function)


class AgentLoopTests(unittest.IsolatedAsyncioTestCase):
    async def execute(self, agent, input="compute", context=None):
        session = await create_session(agent=agent, context=context)
        run = await session.submit(input)
        result = await asyncio.wait_for(run.result(), 2)
        return session, run, result

    async def test_model_tool_model_chain_and_context_are_the_single_history(self):
        model = ScriptedModel([
            AssistantMessage(tool_calls=(ToolCall("call-1", "add", {"a": 2, "b": 3}),)),
            AssistantMessage("5"),
        ])
        context = Context()
        session, run, result = await self.execute(Agent(model=model, tools=[add_tool()]), context=context)
        self.assertEqual(result.output, "5")
        self.assertIs(session.context, context)
        self.assertEqual(await session.history(), context.history())
        self.assertEqual([entry.kind for entry in context.history()], ["input", "assistant", "tool.result", "assistant", "output"])
        self.assertEqual([message.role for message in model.requests[1].messages], ["user", "assistant", "tool"])
        self.assertEqual(model.requests[1].messages[-1].content, 5)
        events = [event.type async for event in run.events()]
        self.assertEqual(events, ["run.started", "model.started", "model.finished", "tool.started", "tool.finished", "model.started", "model.finished", "run.finished"])

    async def test_model_without_tool_call_ends_after_one_request(self):
        model = ScriptedModel([AssistantMessage("answer")])
        _, _, result = await self.execute(Agent(model=model))
        self.assertEqual(result.output, "answer")
        self.assertEqual(len(model.requests), 1)

    async def test_tool_failure_and_unknown_tool_are_returned_to_model_for_repair(self):
        async def fail(arguments, context):
            raise ValueError("bad arguments")
        model = ScriptedModel([
            AssistantMessage(tool_calls=(ToolCall("bad", "add", {}), ToolCall("unknown", "missing", {}))),
            AssistantMessage("recovered"),
        ])
        _, _, result = await self.execute(Agent(model=model, tools=[add_tool(fail)]))
        self.assertEqual(result.status, RunStatus.COMPLETED)
        tool_results = model.requests[1].messages[-2:]
        self.assertTrue(all(message.is_error for message in tool_results))
        self.assertEqual(tool_results[0].content["message"], "bad arguments")
        self.assertIn("Unknown tool", tool_results[1].content["message"])

    async def test_duplicate_call_ids_reject_batch_before_any_tool_effect(self):
        called = []
        async def effect(arguments, context):
            called.append(arguments)
        model = ScriptedModel([AssistantMessage(tool_calls=(ToolCall("same", "add", {}), ToolCall("same", "add", {})))])
        session, _, result = await self.execute(Agent(model=model, tools=[add_tool(effect)]))
        self.assertEqual(result.status, RunStatus.FAILED)
        self.assertEqual(called, [])
        self.assertEqual([entry.kind for entry in await session.history()], ["input", "model.error"])

    async def test_turn_budget_stops_repeat_requests_without_losing_confirmed_results(self):
        model = ScriptedModel([AssistantMessage("progress", (ToolCall("call-1", "add", {"a": 2, "b": 3}),))])
        session, _, result = await self.execute(Agent(model=model, tools=[add_tool()], max_turns=1))
        self.assertEqual(result.status, RunStatus.FAILED)
        self.assertEqual(result.output, "progress")
        self.assertIn("max_turns_exceeded", result.error.message)
        self.assertEqual(len(model.requests), 1)
        self.assertEqual((await session.history())[-1].kind, "tool.result")

    async def test_request_mutation_cannot_change_history_or_tool_configuration(self):
        class MutatingModel:
            async def complete(self, request):
                request.tools[0].parameters["mutated"] = True
                if isinstance(request.messages[-1], ToolResultMessage):
                    request.messages[-1].content.append("mutated")
                    return AssistantMessage("done")
                return AssistantMessage(tool_calls=(ToolCall("call-1", "list", {}),))
        async def value(arguments, context):
            return ["original"]
        tool = Tool("list", "Return list", {"type": "object"}, value)
        session, _, _ = await self.execute(Agent(model=MutatingModel(), tools=[tool]))
        self.assertNotIn("mutated", tool.parameters)
        result = [entry.data for entry in await session.history() if entry.kind == "tool.result"][0]
        self.assertEqual(result["content"], ["original"])

    async def test_context_cannot_be_shared_by_two_sessions(self):
        context = Context()
        agent = Agent(model=ScriptedModel([]))
        await create_session(agent=agent, context=context)
        with self.assertRaises(ValueError):
            await create_session(agent=agent, context=context)

    async def test_policy_is_applied_before_each_model_request(self):
        class CountingPolicy:
            id, version = "counting", "1"
            def __init__(self):
                self.calls = []
            async def prepare(self, history):
                self.calls.append(len(history))
                return history
        policy = CountingPolicy()
        model = ScriptedModel([AssistantMessage(tool_calls=(ToolCall("a", "add", {"a": 1, "b": 2}),)), AssistantMessage("3")])
        await self.execute(Agent(model=model, tools=[add_tool()]), context=Context(policy=policy))
        self.assertEqual(policy.calls, [1, 3])

    async def test_cancelled_tool_turn_is_retained_and_omitted_only_from_next_request(self):
        started = asyncio.Event()
        async def block(arguments, context):
            started.set()
            await asyncio.Event().wait()
        model = ScriptedModel([AssistantMessage(tool_calls=(ToolCall("cancelled", "block", {}),)), AssistantMessage("next answer")])
        session = await create_session(agent=Agent(model=model, tools=[Tool("block", "Wait", {}, block)]))
        first = await session.submit("first")
        await asyncio.wait_for(started.wait(), 2)
        await first.cancel()
        self.assertEqual((await asyncio.wait_for(first.result(), 2)).status, RunStatus.CANCELLED)
        second = await session.submit("second")
        self.assertEqual((await asyncio.wait_for(second.result(), 2)).output, "next answer")
        self.assertEqual([message.content for message in model.requests[-1].messages], ["first", "second"])
        self.assertTrue(any(entry.kind == "assistant" and entry.data["tool_calls"] for entry in session.context.history()))

    async def test_subagent_tool_runs_isolated_child_loop_and_returns_result(self):
        child_model = ScriptedModel([AssistantMessage("child answer")])
        child = Agent(model=child_model)
        parent_model = ScriptedModel([AssistantMessage(tool_calls=(ToolCall("delegate", "spawn_subagent", {"input": "child task"}),)), AssistantMessage("parent answer")])
        session, run, result = await self.execute(Agent(model=parent_model, tools=[subagent_tool(child)]), "private parent history")
        self.assertEqual(result.output, "parent answer")
        self.assertEqual([m.content for m in child_model.requests[0].messages], ["child task"])
        delegated = parent_model.requests[1].messages[-1].content
        self.assertEqual(delegated["output"], "child answer")
        self.assertNotEqual(delegated["session_id"], session.id)
        self.assertEqual(sum(entry.kind == "assistant" for entry in session.context.history()), 2)
        self.assertIn("subagent.finished", [event.type async for event in run.events()])

    async def test_parent_cancel_waits_for_child_cleanup(self):
        started, cleaned = asyncio.Event(), asyncio.Event()
        class ChildModel:
            async def complete(self, request):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cleaned.set()
        parent = Agent(model=ScriptedModel([AssistantMessage(tool_calls=(ToolCall("delegate", "spawn_subagent", {"input": "child task"}),))]), tools=[subagent_tool(Agent(model=ChildModel()))])
        session = await create_session(agent=parent)
        run = await session.submit("parent task")
        await asyncio.wait_for(started.wait(), 2)
        await run.cancel()
        result = await asyncio.wait_for(run.result(), 2)
        self.assertEqual(result.status, RunStatus.CANCELLED)
        self.assertTrue(cleaned.is_set())
        self.assertIsNone((await session.snapshot()).active_run_id)

    async def test_parallel_subagents_use_independent_loops_without_bus_dispatch(self):
        class ConcurrentChildModel:
            def __init__(self):
                self.started = 0
                self.ready = asyncio.Event()

            async def complete(self, request):
                self.started += 1
                if self.started == 2:
                    self.ready.set()
                await self.ready.wait()
                return AssistantMessage(request.messages[-1].content)

        child_model = ConcurrentChildModel()
        delegate = subagent_tool(Agent(model=child_model))
        async def parallel(arguments, parent):
            return await asyncio.gather(*(delegate.execute({"input": value}, parent) for value in arguments["inputs"]))
        model = ScriptedModel([
            AssistantMessage(tool_calls=(ToolCall("parallel", "spawn_parallel", {"inputs": ["A", "B"]}),)),
            AssistantMessage("combined"),
        ])
        await self.execute(Agent(model=model, tools=[Tool("spawn_parallel", "Delegate concurrently", {}, parallel)]))
        results = model.requests[1].messages[-1].content
        self.assertEqual([result["output"] for result in results], ["A", "B"])
        self.assertNotEqual(results[0]["session_id"], results[1]["session_id"])


class ImportBoundaryTests(unittest.TestCase):
    def test_new_agent_context_tool_imports_require_no_event_bus_or_memory(self):
        code = """
import sys
sys.path.insert(0, sys.argv[1])
from aworld.core.agent import Agent, Skill
from aworld.core.context import Context
from aworld.core.tool import Tool
from aworld.core.tool.subagent import subagent_tool
from aworld.core.agent.messages import AssistantMessage
import asyncio
from aworld.core.session import create_session
class Model:
    async def complete(self, request):
        return AssistantMessage('done')
async def check():
    session = await create_session(agent=Agent(model=Model(), skills=[Skill('sample', 'Sample skill', 'Be concise.')]), context=Context())
    run = await session.submit('hello')
    assert (await run.result()).output == 'done'
asyncio.run(check())
for prefix in ('aworld.core.event', 'aworld.events', 'aworld.runners',
               'aworld.memory', 'aworld.agents.llm_agent', 'aworld.core.context.base',
               'aworld.core.context.amni', 'aworld.core.context.compiler', 'aworld.sandbox',
               'aworld.skills', 'yaml', 'tiktoken'):
    assert not any(name == prefix or name.startswith(prefix + '.') for name in sys.modules), prefix
"""
        result = subprocess.run([sys.executable, "-I", "-S", "-c", code, str(Path(__file__).resolve().parents[3])], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
