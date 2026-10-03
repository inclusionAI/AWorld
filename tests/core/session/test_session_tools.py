"""Exercise cross-session access, filters, pagination and scope restrictions."""

import asyncio
import unittest

from aworld.core.agent import Agent
from aworld.core.agent.messages import AssistantMessage, ToolCall
from aworld.core.session import InMemorySessionStore, RunStatus, create_session
from aworld.core.tool import ToolRegistry, session_tools


class EchoModel:
    def __init__(self):
        self.calls = 0
    async def complete(self, request):
        self.calls += 1
        return AssistantMessage("answer: " + request.messages[-1].content)


class FunctionAgent:
    def __init__(self, function):
        self.run = function
    def validate_input(self, input):
        pass


async def execute(session, input):
    return await (await session.submit(input)).result()


class SessionToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = InMemorySessionStore()
        self.model = EchoModel()
        self.source = await create_session(agent=Agent(model=self.model, tools=[]), store=self.store, metadata={"project": "aworld", "title": "context design"})
        await execute(self.source, "Context design decisions")

    async def test_default_tools_query_and_read_another_session_without_reexecution(self):
        class ReaderModel:
            def __init__(self, source_id):
                self.requests = []
                self.responses = iter([
                    AssistantMessage(tool_calls=(ToolCall("query", "session_query", {"query": {"metadata": {"project": "aworld"}, "text": "design"}}),)),
                    AssistantMessage(tool_calls=(ToolCall("read", "read_session", {"session_id": source_id, "limit": 1}),)),
                    AssistantMessage("read complete"),
                ])
            async def complete(self, request):
                self.requests.append(request)
                return next(self.responses)
        model = ReaderModel(self.source.id)
        reader = await create_session(agent=Agent(model=model), store=self.store)
        result = await execute(reader, "find context")
        self.assertEqual(result.output, "read complete")
        hit = model.requests[1].messages[-1].content["matches"][0]
        self.assertEqual(hit["session_id"], self.source.id)
        self.assertEqual(hit["matched_offset"], 1)
        page = model.requests[2].messages[-1].content
        self.assertEqual(page["entries"][0]["data"], "Context design decisions")
        self.assertEqual(page["next_offset"], 2)
        self.assertEqual(self.model.calls, 1)
        self.assertEqual(len(await self.source.history()), 3)

    async def test_explicit_allowlist_limits_search_query_and_read(self):
        hidden = await create_session(agent=Agent(model=EchoModel(), tools=[]), store=self.store, metadata={"project": "aworld"})
        await execute(hidden, "hidden design")
        registry = ToolRegistry(session_tools(self.store, allowed_session_ids=[self.source.id]))
        async def parent(input, context):
            return await registry.execute(input["tool"], input["arguments"], context)
        reader = await create_session(agent=FunctionAgent(parent), store=self.store)
        for tool, arguments in [("search_sessions", {"query": "DESIGN"}), ("session_query", {"query": {}})]:
            result = await execute(reader, {"tool": tool, "arguments": arguments})
            self.assertEqual([item["session_id"] for item in result.output["matches"]], [self.source.id])
        result = await execute(reader, {"tool": "read_session", "arguments": {"session_id": hidden.id}})
        self.assertEqual(result.status, RunStatus.FAILED)
        self.assertIn("unavailable", result.error.message)
        source_before = await self.source.history()
        page = await registry.execute("read_session", {"session_id": self.source.id}, None)
        page["entries"][0]["data"] = "mutated"
        page["metadata"]["project"] = "mutated"
        self.assertEqual(await self.source.history(), source_before)
        self.assertEqual((await self.source.snapshot()).metadata["project"], "aworld")

    async def test_default_scope_does_not_cross_stores(self):
        registry = ToolRegistry(session_tools())
        async def parent(input, context):
            return await registry.execute("read_session", {"session_id": self.source.id}, context)
        reader = await create_session(agent=FunctionAgent(parent))
        result = await execute(reader, "read")
        self.assertEqual(result.status, RunStatus.FAILED)
        self.assertIn("unavailable", result.error.message)

    async def test_query_combined_state_ids_metadata_and_pagination(self):
        started = asyncio.Event()
        async def running(input, context):
            started.set()
            await asyncio.Event().wait()
        active = await create_session(agent=FunctionAgent(running), store=self.store, metadata={"project": "aworld"})
        run = await active.submit("work")
        await asyncio.wait_for(started.wait(), 1)
        registry = ToolRegistry(session_tools(self.store))
        result = await registry.execute("session_query", {"query": {"state": "active", "metadata": {"project": "aworld"}, "session_ids": [self.source.id, active.id]}}, None)
        self.assertEqual([item["session_id"] for item in result["matches"]], [active.id])
        first = await registry.execute("session_query", {"query": {}, "limit": 1}, None)
        second = await registry.execute("session_query", {"query": {}, "offset": first["next_offset"], "limit": 1}, None)
        self.assertEqual(first["matches"][0]["session_id"], self.source.id)
        self.assertEqual(second["matches"][0]["session_id"], active.id)
        for query in ({"state": "bad"}, {"raw_sql": "anything"}, {"metadata": []}, {"text": None}):
            with self.assertRaises(ValueError):
                await registry.execute("session_query", {"query": query}, None)
        await run.cancel()
        await run.result()
        await active.close()


if __name__ == "__main__":
    unittest.main()
