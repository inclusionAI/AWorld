"""Span lifetime of the ``uni_llmmodel`` streaming wrapper.

``WrappedGeneratorResponse`` is what ``LLMModel.stream_completion`` and
``LLMModel.astream_completion`` return while tracing is on. It finalized its span
only from the branch that observes the end of the stream, so a caller that stopped
early -- leaving a ``with`` block, or closing the stream after a partial read --
had no way to finalize the span at all: the wrapper exposed neither the context
manager protocol nor ``close()``/``aclose()``, and the span stayed open.
"""
from __future__ import annotations

import pytest

from aworld.trace.instrumentation import uni_llmmodel


def _noop(*args, **kwargs) -> None:
    return None


def _empty(*args, **kwargs):
    return {}


def _token_usage(*args, **kwargs):
    return 0, 0


class _FakeSpan:
    """The wrapper only needs ``is_recording`` / ``set_attributes`` / ``end``."""

    def __init__(self) -> None:
        self._recording = True
        self.end_calls = 0

    def is_recording(self) -> bool:
        return self._recording

    def set_attributes(self, attributes) -> None:
        pass

    def end(self) -> None:
        self.end_calls += 1
        self._recording = False


class _FakeAsyncStream:
    def __init__(self, items) -> None:
        self._items = list(items)
        self.closed = False

    async def __anext__(self):
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)

    async def aclose(self) -> None:
        self.closed = True


class _ClosableStream:
    """A sync stream that reports whether its own ``close`` was reached."""

    def __init__(self) -> None:
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        raise StopIteration

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _stub_collaborators(monkeypatch):
    """Keep the test on span lifetime, not on attribute or metric computation."""
    monkeypatch.setattr(uni_llmmodel, "accumulate_stream_response", _noop)
    monkeypatch.setattr(uni_llmmodel, "record_streaming_time_to_first_token", _noop)
    monkeypatch.setattr(uni_llmmodel, "record_stream_token_usage", _token_usage)
    monkeypatch.setattr(uni_llmmodel, "get_common_attributes_from_response", _empty)
    monkeypatch.setattr(uni_llmmodel, "parse_response_message", _empty)
    monkeypatch.setattr(uni_llmmodel, "record_chat_response_metric", _noop)
    monkeypatch.setattr(uni_llmmodel, "record_streaming_time_to_generate", _noop)


def _wrap(span, response):
    return uni_llmmodel.WrappedGeneratorResponse(
        span=span,
        response=response,
        instance=None,
        start_time=1.0,
        request_kwargs={"messages": []},
    )


def test_exhausting_the_sync_stream_ends_the_span() -> None:
    span = _FakeSpan()
    stream = _wrap(span, iter(["a", "b"]))

    assert list(stream) == ["a", "b"]
    assert span.end_calls == 1


def test_leaving_a_with_block_early_ends_the_span() -> None:
    span = _FakeSpan()

    with _wrap(span, iter(["a", "b"])) as stream:
        assert next(stream) == "a"

    assert span.end_calls == 1


def test_close_after_a_partial_read_ends_the_span() -> None:
    span = _FakeSpan()
    stream = _wrap(span, iter(["a", "b"]))

    assert next(stream) == "a"
    stream.close()

    assert span.end_calls == 1


def test_close_forwards_to_the_wrapped_stream() -> None:
    span = _FakeSpan()
    inner = _ClosableStream()

    _wrap(span, inner).close()

    assert inner.closed is True
    assert span.end_calls == 1


def test_exhausting_then_closing_ends_the_span_once() -> None:
    span = _FakeSpan()
    stream = _wrap(span, iter(["a"]))

    assert list(stream) == ["a"]
    stream.close()

    assert span.end_calls == 1


async def test_leaving_an_async_with_block_early_ends_the_span() -> None:
    span = _FakeSpan()

    async with _wrap(span, _FakeAsyncStream(["a", "b"])) as stream:
        assert await anext(stream) == "a"

    assert span.end_calls == 1


async def test_aclose_after_a_partial_async_read_ends_the_span() -> None:
    span = _FakeSpan()
    inner = _FakeAsyncStream(["a", "b"])
    stream = _wrap(span, inner)

    assert await anext(stream) == "a"
    await stream.aclose()

    assert inner.closed is True
    assert span.end_calls == 1
