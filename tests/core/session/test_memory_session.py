"""Exercise live ownership, cancellation, context and event delivery without an LLM."""

import asyncio
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from aworld.core.session import (
    Context, InputRejectedError, InMemorySessionStore, RunInactiveError, RunNotFoundError,
    RunOptions, RunStatus, RunStopReason, SessionBusyError, SessionNotFoundError,
    create_session, load_session,
)


class EchoAgent:
    def validate_input(self, input):
        if input is None:
            raise ValueError("input is required")

    async def run(self, input, context):
        context.emit("agent.output", input)
        return input


class GateAgent(EchoAgent):
    def __init__(self, *, cleanup=False, suppress_cancel=False):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.stopping = asyncio.Event()
        self.cleaned = asyncio.Event()
        self.cleanup = cleanup
        self.suppress_cancel = suppress_cancel
        self.context = None

    async def run(self, input, context):
        self.context = context
        context.set_output({"partial": input})
        context.emit("agent.started", input)
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.stopping.set()
            if self.cleanup:
                await self.cleaned.wait()
            if not self.suppress_cancel:
                raise
        return input


async def collect(run, after_seq=0):
    return [event async for event in run.events(after_seq=after_seq)]


class MemorySessionTests(unittest.IsolatedAsyncioTestCase):
    async def outcome(self, run):
        return await asyncio.wait_for(run.result(), 2)

    async def test_load_continues_same_history_and_run_registry(self):
        store = InMemorySessionStore()
        session = await create_session(agent=EchoAgent(), store=store)
        first = await session.submit("first")
        self.assertEqual((await self.outcome(first)).output, "first")
        loaded = await load_session(session.id, store=store)
        self.assertIs(loaded, session)
        self.assertIs(await loaded.get_run(first.id), first)
        second = await loaded.submit("second")
        await self.outcome(second)
        self.assertEqual([entry.data for entry in await session.history()],
                         ["first", "first", "second", "second"])
        with self.assertRaises(SessionNotFoundError):
            await load_session("missing", store=store)
        other = await create_session(agent=EchoAgent(), store=store)
        with self.assertRaises(RunNotFoundError):
            await other.get_run(first.id)

    async def test_busy_and_invalid_submissions_do_not_change_history(self):
        agent = GateAgent()
        session = await create_session(agent=agent)
        with self.assertRaises(InputRejectedError):
            await session.submit(None)
        self.assertEqual(await session.history(), ())
        run = await session.submit("accepted")
        with self.assertRaises(SessionBusyError) as error:
            await session.submit("rejected")
        self.assertEqual(error.exception.active_run_id, run.id)
        self.assertEqual(len(await session.history()), 1)
        await run.cancel()
        await self.outcome(run)
        agent.release.set()
        await self.outcome(await session.submit("next"))

    async def test_separate_sessions_can_execute_concurrently(self):
        store = InMemorySessionStore()
        agents = [GateAgent(), GateAgent()]
        sessions = [await create_session(agent=agent, store=store) for agent in agents]
        runs = [await session.submit(i) for i, session in enumerate(sessions)]
        await asyncio.wait_for(asyncio.gather(*(agent.started.wait() for agent in agents)), 2)
        for agent in agents:
            agent.release.set()
        self.assertEqual([result.output for result in await asyncio.gather(*(self.outcome(r) for r in runs))], [0, 1])
        self.assertEqual([entry.data for entry in await sessions[0].history()], [0, 0])

    async def test_two_live_subscribers_replay_and_resume_without_gaps(self):
        agent = GateAgent()
        run = await (await create_session(agent=agent)).submit("value")
        listeners = [asyncio.create_task(collect(run)) for _ in range(2)]
        await asyncio.wait_for(agent.started.wait(), 2)
        agent.release.set()
        result = await self.outcome(run)
        streams = await asyncio.wait_for(asyncio.gather(*listeners), 2)
        self.assertEqual(streams[0], streams[1])
        events = streams[0]
        self.assertEqual([event.seq for event in events], [1, 2, 3])
        self.assertEqual(events[-1].type, "run.finished")
        self.assertEqual(events[-1].data, result)
        self.assertEqual(await collect(run, 1), events[1:])
        self.assertEqual(await collect(run, 3), [])
        for cursor in (-1, 4, True, "1"):
            with self.assertRaises(ValueError):
                run.events(after_seq=cursor)

    async def test_cancellation_before_execution_is_terminal_without_invoking_agent(self):
        agent = GateAgent()
        session = await create_session(agent=agent)
        run = await session.submit("input")
        await run.cancel()
        await run.cancel()
        result = await self.outcome(run)
        self.assertEqual(result.status, RunStatus.CANCELLED)
        self.assertFalse(agent.started.is_set())
        self.assertEqual([event.type for event in await collect(run)], ["run.finished"])
        self.assertIsNone((await session.snapshot()).active_run_id)

    async def test_cleanup_retains_slot_and_fences_context_until_agent_stops(self):
        agent = GateAgent(cleanup=True)
        session = await create_session(agent=agent)
        run = await session.submit("input")
        await asyncio.wait_for(agent.started.wait(), 2)
        await run.cancel()
        await asyncio.wait_for(agent.stopping.wait(), 2)
        self.assertEqual((await run.snapshot()).status, RunStatus.CANCELLING)
        with self.assertRaises(SessionBusyError):
            await session.submit("premature")
        with self.assertRaises(RunInactiveError):
            agent.context.append("tool.result", "late write")
        agent.cleaned.set()
        result = await self.outcome(run)
        self.assertEqual(result.output, {"partial": "input"})
        self.assertIsNone((await session.snapshot()).active_run_id)

    async def test_swallowed_cancel_cannot_turn_into_success(self):
        agent = GateAgent(suppress_cancel=True)
        session = await create_session(agent=agent)
        run = await session.submit("input")
        await asyncio.wait_for(agent.started.wait(), 2)
        await run.cancel()
        self.assertEqual((await self.outcome(run)).status, RunStatus.CANCELLED)
        self.assertEqual([entry.kind for entry in await session.history()], ["input"])

    async def test_deadline_failure_keeps_partial_output_and_releases_slot(self):
        agent = GateAgent()
        session = await create_session(agent=agent)
        run = await session.submit("input", options=RunOptions(timeout_seconds=0.05))
        result = await self.outcome(run)
        self.assertEqual(result.status, RunStatus.FAILED)
        self.assertEqual(result.stop_reason, RunStopReason.DEADLINE_EXCEEDED)
        self.assertEqual(result.error.code, "deadline_exceeded")
        self.assertEqual(result.output, {"partial": "input"})
        self.assertIsNone((await session.snapshot()).active_run_id)

    async def test_agent_failure_is_a_result_and_context_cannot_write_afterwards(self):
        class BrokenAgent(EchoAgent):
            async def run(self, input, context):
                self.context = context
                context.set_output("progress")
                raise ValueError("tool failed")
        agent = BrokenAgent()
        session = await create_session(agent=agent)
        result = await self.outcome(await session.submit("input"))
        self.assertEqual(result.status, RunStatus.FAILED)
        self.assertEqual(result.error.message, "tool failed")
        self.assertEqual(result.output, "progress")
        with self.assertRaises(RunInactiveError):
            agent.context.emit("agent.late")
        self.assertIsNone((await session.snapshot()).active_run_id)

    async def test_cancelled_waiter_and_closed_subscription_do_not_stop_run(self):
        agent = GateAgent()
        run = await (await create_session(agent=agent)).submit("input")
        waiter = asyncio.create_task(run.result())
        stream = run.events()
        self.assertEqual((await asyncio.wait_for(anext(stream), 2)).type, "run.started")
        await stream.aclose()
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        await asyncio.wait_for(agent.started.wait(), 2)
        agent.release.set()
        self.assertEqual((await self.outcome(run)).status, RunStatus.COMPLETED)

    async def test_mutable_input_metadata_results_events_and_history_are_detached(self):
        agent = GateAgent()
        metadata = {"nested": [1]}
        session = await create_session(agent=agent, metadata=metadata)
        value = {"nested": [2]}
        run = await session.submit(value)
        value["nested"].append(3)
        metadata["nested"].append(4)
        snap = await session.snapshot()
        snap.metadata["nested"].append(5)
        self.assertEqual((await session.snapshot()).metadata["nested"], [1])
        await asyncio.wait_for(agent.started.wait(), 2)
        agent.release.set()
        result = await self.outcome(run)
        self.assertEqual(result.output, {"nested": [2]})
        result.output["nested"].append(6)
        history = await session.history()
        history[0].data["nested"].append(7)
        events = await collect(run)
        events[-1].data.output["nested"].append(8)
        self.assertEqual((await run.result()).output, {"nested": [2]})
        self.assertEqual((await session.history())[0].data, {"nested": [2]})
        self.assertEqual((await collect(run))[-1].data.output, {"nested": [2]})

    async def test_policy_changes_request_view_without_trimming_canonical_history(self):
        class LastInputPolicy:
            id, version = "last-input", "1"
            async def prepare(self, history):
                return tuple(entry for entry in history[-1:] if entry.kind == "input")
        class ViewAgent(EchoAgent):
            async def run(self, input, context):
                view = await context.get_context()
                context.append("tool.result", {"seen": len(view)})
                return [entry.data for entry in view]
        session = await create_session(agent=ViewAgent(), context=Context(policy=LastInputPolicy()))
        await self.outcome(await session.submit("first"))
        self.assertEqual((await self.outcome(await session.submit("second"))).output, ["second"])
        self.assertEqual(len(await session.history()), 6)
        self.assertEqual((await session.snapshot()).context_policy_id, "last-input")

    async def test_output_storage_failure_becomes_terminal_failure(self):
        from aworld.core.context.storage import InMemoryContextStorage
        class BrokenHistory(InMemoryContextStorage):
            def append(self, session_id, entry):
                if entry.kind == "output":
                    raise OSError("disk full")
                super().append(session_id, entry)
        session = await create_session(agent=EchoAgent(), context=Context(storage=BrokenHistory()))
        result = await self.outcome(await session.submit("input"))
        self.assertEqual(result.status, RunStatus.FAILED)
        self.assertEqual(result.error.message, "disk full")
        self.assertIsNone((await session.snapshot()).active_run_id)


if __name__ == "__main__":
    unittest.main()
