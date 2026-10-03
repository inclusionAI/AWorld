"""Verify child ownership, bounded concurrency, and background lifetimes."""

import asyncio
import unittest

from aworld.core.agent import Agent
from aworld.core.agent.messages import AssistantMessage
from aworld.core.session import RunStatus, create_session
from aworld.core.tool import ToolRegistry
from aworld.core.tool.subagent import subagent_tools


class FunctionAgent:
    def __init__(self, function):
        self.run = function

    def validate_input(self, input):
        if not isinstance(input, str) or not input:
            raise ValueError("Expected text")


async def submit(session, input):
    run = await session.submit(input)
    return await asyncio.wait_for(run.result(), 3)


class SubagentToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_parallel_limit_and_separate_child_contexts(self):
        class ChildModel:
            def __init__(self):
                self.active = self.maximum = 0
            async def complete(self, request):
                self.active += 1
                self.maximum = max(self.maximum, self.active)
                try:
                    await asyncio.sleep(.02)
                    return AssistantMessage(request.messages[-1].content)
                finally:
                    self.active -= 1
        model = ChildModel()
        registry = ToolRegistry(subagent_tools({"worker": Agent(model=model, tools=[])}, max_concurrent=2))
        async def parent(input, context):
            return await registry.execute("parallel_subagents", {"tasks": [
                {"agent": "worker", "input": str(i)} for i in range(5)
            ]}, context)
        session = await create_session(agent=FunctionAgent(parent))
        result = await submit(session, "parallel")
        self.assertEqual([child["output"] for child in result.output], [str(i) for i in range(5)])
        self.assertEqual(len({child["session_id"] for child in result.output}), 5)
        self.assertEqual(model.maximum, 2)
        self.assertEqual([entry.kind for entry in await session.history()], ["input", "output"])
        await session.close()

    async def test_background_survives_parent_run_and_wait_returns_only_result(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def child(input, context):
            started.set()
            await release.wait()
            context.append("child-only", "private")
            return "finished"
        registry = ToolRegistry(subagent_tools({"worker": FunctionAgent(child)}))
        handles = []
        async def parent(input, context):
            if input == "start":
                result = await registry.execute("spawn_subagent", {"agent": "worker", "input": "work", "background": True}, context)
                handles.append(result["task_id"])
                return result
            return await registry.execute(input, {"task_id": handles[0]}, context)
        session = await create_session(agent=FunctionAgent(parent))
        self.assertEqual((await submit(session, "start")).status, RunStatus.COMPLETED)
        await asyncio.wait_for(started.wait(), 1)
        self.assertEqual((await submit(session, "check_subagent")).output["status"], "running")
        waiting = await session.submit("wait_subagent")
        await asyncio.sleep(0)
        self.assertFalse((await waiting.snapshot()).status.is_terminal)
        release.set()
        result = await asyncio.wait_for(waiting.result(), 2)
        self.assertEqual(result.output["output"], "finished")
        self.assertNotIn("child-only", [entry.kind for entry in await session.history()])
        await session.close()

    async def test_background_wait_cancellation_preserves_child_and_explicit_cancel_cleans(self):
        started, cleaned = asyncio.Event(), asyncio.Event()
        async def child(input, context):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(.01)
                cleaned.set()
        registry = ToolRegistry(subagent_tools({"worker": FunctionAgent(child)}))
        handle = None
        async def parent(input, context):
            nonlocal handle
            if input == "start":
                handle = (await registry.execute("spawn_subagent", {"agent": "worker", "input": "work", "background": True}, context))["task_id"]
                return handle
            return await registry.execute(input, {"task_id": handle}, context)
        session = await create_session(agent=FunctionAgent(parent))
        await submit(session, "start")
        await asyncio.wait_for(started.wait(), 1)
        waiting = await session.submit("wait_subagent")
        await asyncio.sleep(.01)
        await waiting.cancel()
        self.assertEqual((await waiting.result()).status, RunStatus.CANCELLED)
        self.assertFalse(cleaned.is_set())
        self.assertEqual((await submit(session, "check_subagent")).output["status"], "running")
        result = await submit(session, "cancel_subagent")
        self.assertEqual(result.output["status"], "cancelled")
        self.assertTrue(cleaned.is_set())
        await session.close()

    async def test_session_close_cancels_children_and_rejects_new_submissions(self):
        started, cleaned = asyncio.Event(), asyncio.Event()
        async def child(input, context):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(.01)
                cleaned.set()
        registry = ToolRegistry(subagent_tools({"worker": FunctionAgent(child)}))
        async def parent(input, context):
            return await registry.execute("spawn_subagent", {"agent": "worker", "input": "work", "background": True}, context)
        session = await create_session(agent=FunctionAgent(parent))
        await submit(session, "start")
        await asyncio.wait_for(started.wait(), 1)
        await asyncio.gather(session.close(), session.close())
        self.assertTrue(cleaned.is_set())
        with self.assertRaises(RuntimeError):
            await session.submit("again")

    async def test_task_handles_cannot_be_used_from_another_session(self):
        async def child(input, context):
            return "done"
        registry = ToolRegistry(subagent_tools({"worker": FunctionAgent(child)}))
        handle = None
        async def parent(input, context):
            if input == "start":
                return await registry.execute("spawn_subagent", {"agent": "worker", "input": "work", "background": True}, context)
            return await registry.execute("check_subagent", {"task_id": handle}, context)
        agent = FunctionAgent(parent)
        first, second = await create_session(agent=agent), await create_session(agent=agent)
        handle = (await submit(first, "start")).output["task_id"]
        result = await submit(second, "check")
        self.assertEqual(result.status, RunStatus.FAILED)
        self.assertIn("in this session", result.error.message)
        await first.close()
        await second.close()

    async def test_parallel_validates_all_inputs_before_starting_any_child(self):
        executed = []
        async def child(input, context):
            executed.append(input)
        registry = ToolRegistry(subagent_tools({"worker": FunctionAgent(child)}))
        async def parent(input, context):
            return await registry.execute("parallel_subagents", {"tasks": [
                {"agent": "worker", "input": "valid"}, {"agent": "worker", "input": None}
            ]}, context)
        session = await create_session(agent=FunctionAgent(parent))
        self.assertEqual((await submit(session, "start")).status, RunStatus.FAILED)
        self.assertEqual(executed, [])
        await session.close()

    async def test_queued_child_cancel_before_admission(self):
        started = asyncio.Event()
        inputs = []
        async def child(input, context):
            inputs.append(input)
            started.set()
            await asyncio.Event().wait()
        registry = ToolRegistry(subagent_tools({"worker": FunctionAgent(child)}, max_concurrent=1))
        async def parent(input, context):
            first = await registry.execute("spawn_subagent", {"agent": "worker", "input": "first", "background": True}, context)
            await started.wait()
            second = await registry.execute("spawn_subagent", {"agent": "worker", "input": "second", "background": True}, context)
            return await registry.execute("cancel_subagent", {"task_id": second["task_id"]}, context)
        session = await create_session(agent=FunctionAgent(parent))
        result = await submit(session, "start")
        self.assertEqual(result.output["status"], "cancelled")
        self.assertIsNone(result.output["run_id"])
        self.assertEqual(inputs, ["first"])
        await session.close()


if __name__ == "__main__":
    unittest.main()
