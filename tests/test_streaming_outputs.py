import asyncio
import contextlib

import pytest

from aworld.output.outputs import StreamingOutputs


class DummyOutput:
    def output_type(self):
        return "dummy"


@pytest.mark.anyio
async def test_streaming_outputs_cancels_producer_when_consumer_stops_early():
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def producer():
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    outputs = StreamingOutputs(cancel_run_impl_task_on_cleanup=True)
    outputs._run_impl_task = asyncio.create_task(producer())
    await started.wait()
    await outputs.add_output(DummyOutput())

    stream = outputs.stream_events()
    await stream.__anext__()
    await stream.aclose()

    await asyncio.wait_for(cancelled.wait(), timeout=1.0)
    with pytest.raises(asyncio.CancelledError):
        await outputs._run_impl_task


@pytest.mark.anyio
async def test_streaming_outputs_preserves_producer_when_cleanup_cancellation_disabled():
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def producer():
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    outputs = StreamingOutputs(cancel_run_impl_task_on_cleanup=False)
    outputs._run_impl_task = asyncio.create_task(producer())
    await started.wait()
    await outputs.add_output(DummyOutput())

    stream = outputs.stream_events()
    await stream.__anext__()
    await stream.aclose()
    await asyncio.sleep(0)

    assert not cancelled.is_set()
    assert not outputs._run_impl_task.cancelled()

    outputs._run_impl_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await outputs._run_impl_task


@pytest.mark.anyio
async def test_streaming_outputs_does_not_cancel_finishing_producer_after_mark_completed():
    async def producer():
        await asyncio.sleep(0.1)
        return {"task-1": "ok"}

    outputs = StreamingOutputs(task_id="task-1", cancel_run_impl_task_on_cleanup=True)
    outputs._run_impl_task = asyncio.create_task(producer())
    await outputs.mark_completed()

    stream = outputs.stream_events()
    with pytest.raises(StopAsyncIteration):
        await stream.__anext__()

    await asyncio.wait_for(outputs._run_impl_task, timeout=1.0)
    assert not outputs._run_impl_task.cancelled()


@pytest.mark.anyio
async def test_streaming_outputs_ignores_expected_cancelled_producer_after_consumer_interrupt():
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def producer():
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def consume(outputs: StreamingOutputs):
        async for _ in outputs.stream_events():
            pass

    outputs = StreamingOutputs(task_id="task-1", cancel_run_impl_task_on_cleanup=True)
    outputs._run_impl_task = asyncio.create_task(producer())

    await started.wait()
    consumer_task = asyncio.create_task(consume(outputs))
    await asyncio.sleep(0)
    consumer_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await consumer_task

    await asyncio.wait_for(cancelled.wait(), timeout=1.0)
    assert outputs._run_impl_task.cancelled()

    outputs._check_errors()
    assert outputs._stored_exception is None
