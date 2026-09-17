from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from openai.types.chat import ChatCompletionChunk

from aworld.core.context.generation_budget import (
    GenerationBudgetExceeded,
    GenerationBudgetPolicy,
    GenerationStopReason,
)
from aworld.models.llm import LLMModel
from aworld.models.openai_provider import OpenAIProvider
from tests.core.agent.test_generation_action_budget import _agent, _message


def _chunk(delta, *, finish_reason=None, usage=None):
    return {
        "id": "response-1",
        "model": "fake-model",
        "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        "usage": usage,
    }


class _RawStream:
    def __init__(self, chunks, *, interval=0.05, repeat=False, sdk_chunks=False):
        self.chunks = iter(chunks)
        self.interval = interval
        self.repeat = repeat
        self.last = None
        self.closed = asyncio.Event()
        self.started = asyncio.Event()
        self.received = 0
        self.sdk_chunks = sdk_chunks

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.sleep(self.interval)
        try:
            self.last = next(self.chunks)
        except StopIteration:
            if not self.repeat:
                raise StopAsyncIteration
        self.received += 1
        self.started.set()
        if self.sdk_chunks:
            return ChatCompletionChunk.model_validate({**self.last, "created": 0})
        return self.last

    async def close(self):
        self.closed.set()


def _model(stream, *, transport="sdk"):
    provider = object.__new__(OpenAIProvider)
    provider.model_name = "fake-model"
    provider.kwargs = {}
    provider.is_http_provider = transport == "http"
    provider.stream_tool_buffer = []

    async def create(**kwargs):
        assert kwargs["stream"] is True
        return stream

    provider.async_provider = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    if transport == "http":

        async def async_stream_call(*args, **kwargs):
            try:
                async for chunk in stream:
                    yield chunk
            finally:
                await stream.close()

        provider.http_provider = SimpleNamespace(async_stream_call=async_stream_call)
    return LLMModel(custom_provider=provider)


def _stream_agent(stream, *, total=2.0, idle=0.25, with_tools=True, transport="sdk"):
    agent = _agent(
        policy=GenerationBudgetPolicy(
            total_timeout_seconds=total,
            stream_idle_timeout_seconds=idle,
            active_tool_free_timeout_seconds=0.3,
            action_repair_enabled=False,
        ),
        with_tools=with_tools,
    )
    agent._llm = _model(stream, transport=transport)
    return agent


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["sdk", "http"])
async def test_buffered_tool_arguments_keep_generation_live_until_complete(transport):
    fragments = ['{"text":"', "a", "b", "c", "d", "e", "f", '"}']
    chunks = [
        _chunk(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "workspace__write",
                            "arguments": fragments[0],
                        },
                    }
                ]
            }
        )
    ]
    chunks.extend(
        _chunk({"tool_calls": [{"index": 0, "function": {"arguments": fragment}}]})
        for fragment in fragments[1:]
    )
    chunks.append(
        _chunk(
            {},
            finish_reason="tool_calls",
            usage={"prompt_tokens": 7, "completion_tokens": 9, "total_tokens": 16},
        )
    )
    stream = _RawStream(chunks, sdk_chunks=transport == "sdk")
    agent = _stream_agent(stream, transport=transport)
    message = _message("buffered-tools")

    response = await agent.invoke_model(
        messages=[{"role": "user", "content": "write"}], message=message, stream=True
    )

    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].id == "call-1"
    assert response.tool_calls[0].function.arguments == "".join(fragments)
    assert response.usage["total_tokens"] == 16
    assert not message.context.context_info.get("generation_budget_events")
    assert stream.closed.is_set()
    record = message.context.get_llm_calls()[-1]
    assert record["usage_available"] is True
    assert record["usage_normalized"]["total_tokens"] == 16


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["sdk", "http"])
async def test_reasoning_only_deltas_are_progress_without_becoming_answer_text(
    transport,
):
    stream = _RawStream(
        [_chunk({"reasoning_content": "working "}) for _ in range(8)]
        + [
            _chunk({"content": "done"}),
            _chunk({"reasoning_content": "finished"}, finish_reason="stop"),
        ],
        sdk_chunks=transport == "sdk",
    )
    agent = _stream_agent(stream, with_tools=False, transport=transport)
    response = await agent.invoke_model(
        messages=[{"role": "user", "content": "reason"}],
        message=_message("reasoning-deltas"),
        stream=True,
    )

    assert response.content == "done"
    assert response.reasoning_content == "working " * 8 + "finished"
    assert stream.closed.is_set()


@pytest.mark.asyncio
async def test_empty_transport_chunks_do_not_extend_idle_deadline():
    stream = _RawStream([_chunk({"role": "assistant"})], repeat=True, interval=0.005)
    agent = _stream_agent(stream, total=0.5, idle=0.045)

    with pytest.raises(GenerationBudgetExceeded) as raised:
        await agent.invoke_model(
            messages=[{"role": "user", "content": "work"}],
            message=_message("heartbeat"),
            stream=True,
        )

    assert raised.value.reason is GenerationStopReason.IDLE_TIMEOUT
    assert stream.received > 1
    assert stream.closed.is_set()


@pytest.mark.asyncio
async def test_live_tool_arguments_cannot_extend_total_deadline():
    stream = _RawStream(
        [
            _chunk(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call-1",
                            "function": {"name": "workspace__write", "arguments": "x"},
                        }
                    ]
                }
            )
        ],
        repeat=True,
        interval=0.01,
    )
    agent = _stream_agent(stream, total=0.14, idle=0.045)

    with pytest.raises(GenerationBudgetExceeded) as raised:
        await agent.invoke_model(
            messages=[{"role": "user", "content": "write"}],
            message=_message("total-budget"),
            stream=True,
        )

    assert raised.value.reason is GenerationStopReason.CALL_DEADLINE_EXCEEDED
    assert not raised.value.partial_response.tool_calls
    assert stream.closed.is_set()


@pytest.mark.asyncio
async def test_caller_cancellation_closes_provider_transport():
    stream = _RawStream(
        [_chunk({"content": "still working"})], repeat=True, interval=0.01
    )
    agent = _stream_agent(stream, with_tools=False)
    running = asyncio.create_task(
        agent.invoke_model(
            messages=[{"role": "user", "content": "work"}],
            message=_message("caller-cancel"),
            stream=True,
        )
    )
    await asyncio.wait_for(stream.started.wait(), timeout=1)
    running.cancel()

    with pytest.raises(asyncio.CancelledError):
        await running

    assert stream.closed.is_set()


@pytest.mark.asyncio
async def test_closing_model_generator_closes_provider_transport_immediately():
    stream = _RawStream([_chunk({"content": "partial"})], repeat=True)
    model_stream = _model(stream).astream_completion(
        messages=[{"role": "user", "content": "work"}]
    )
    await model_stream.__anext__()
    await model_stream.aclose()
    assert stream.closed.is_set()


@pytest.mark.asyncio
async def test_interleaved_requests_keep_tool_argument_buffers_isolated():
    def raw_stream(name):
        return _RawStream(
            [
                _chunk(
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": name,
                                "function": {"name": name, "arguments": '{"value":"'},
                            }
                        ]
                    }
                ),
                _chunk(
                    {
                        "tool_calls": [
                            {"index": 0, "function": {"arguments": name + '"}'}}
                        ]
                    }
                ),
                _chunk({}, finish_reason="tool_calls"),
            ]
        )

    streams = {name: raw_stream(name) for name in ("first", "second")}
    model = _model(streams["first"])

    async def create(**kwargs):
        return streams[kwargs["messages"][0]["content"]]

    model.provider.async_provider.chat.completions.create = create

    async def consume(name):
        calls = []
        async for chunk in model.astream_completion(
            messages=[{"role": "user", "content": name}]
        ):
            calls.extend(chunk.tool_calls or [])
        return calls

    first, second = await asyncio.gather(consume("first"), consume("second"))
    for name, calls in (("first", first), ("second", second)):
        assert len(calls) == 1
        assert calls[0].id == name
        assert calls[0].function.arguments == '{"value":"' + name + '"}'
        assert streams[name].closed.is_set()


@pytest.mark.asyncio
async def test_interrupted_stream_without_usage_does_not_report_known_zero():
    stream = _RawStream([_chunk({"content": "partial"})], repeat=True)
    model = _model(stream)
    context = _message("usage-unknown").context
    model_stream = model.astream_completion(
        messages=[{"role": "user", "content": "work"}], context=context
    )
    await model_stream.__anext__()
    await model_stream.aclose()
    assert context.get_llm_calls()[-1]["usage_available"] is False
