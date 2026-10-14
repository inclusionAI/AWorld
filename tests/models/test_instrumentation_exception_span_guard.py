"""The instrumentors' exception helpers must skip a span that is not recording.

``Span.is_recording`` is a method. Each helper guarded its ``record_exception`` call
with ``span.is_recording`` -- the bound method, never called -- so the condition was
always true and a non-recording span was passed to ``record_exception`` anyway. The
helper records its metric *after* that call, inside one blanket ``except``: a span
implementation that refuses ``record_exception`` while not recording took the metric
down with it and left only a warning behind.
"""
from __future__ import annotations

import pytest

from aworld.trace.instrumentation import agent as agent_instrumentation
from aworld.trace.instrumentation import tool as tool_instrumentation
from aworld.trace.instrumentation import uni_llmmodel as uni_llmmodel_instrumentation


class _NonRecordingSpan:
    """A span that reports it is not recording."""

    def __init__(self, reject: bool = False) -> None:
        self.recorded = []
        self._reject = reject

    def is_recording(self) -> bool:
        return False

    def record_exception(
        self,
        exception,
        attributes=None,
        timestamp=None,
        escaped=False,
    ) -> None:
        self.recorded.append(exception)
        if self._reject:
            raise RuntimeError("span is not recording")


_CASES = [
    pytest.param(
        uni_llmmodel_instrumentation, "record_exception", False,
        "record_exception_metric", id="uni_llmmodel",
    ),
    pytest.param(
        tool_instrumentation, "_record_exception", True, "_record_metric", id="tool",
    ),
    pytest.param(
        agent_instrumentation, "_record_exception", True, "_record_metric", id="agent",
    ),
]


def _call_helper(module, helper_name, takes_attributes, span, exception):
    helper = getattr(module, helper_name)
    if takes_attributes:
        helper(span, 1.0, exception, {"k": "v"})
    else:
        helper(span, 1.0, exception)


@pytest.mark.parametrize(
    ("module", "helper_name", "takes_attributes", "metric_name"), _CASES
)
def test_a_non_recording_span_is_not_asked_to_record(
    monkeypatch, module, helper_name, takes_attributes, metric_name
) -> None:
    metric_calls = []
    monkeypatch.setattr(module, metric_name, lambda *a, **k: metric_calls.append((a, k)))
    span = _NonRecordingSpan()

    _call_helper(module, helper_name, takes_attributes, span, RuntimeError("boom"))

    assert span.recorded == []
    assert len(metric_calls) == 1


@pytest.mark.parametrize(
    ("module", "helper_name", "takes_attributes", "metric_name"), _CASES
)
def test_a_span_that_refuses_recording_does_not_take_the_metric_with_it(
    monkeypatch, module, helper_name, takes_attributes, metric_name
) -> None:
    metric_calls = []
    monkeypatch.setattr(module, metric_name, lambda *a, **k: metric_calls.append((a, k)))
    span = _NonRecordingSpan(reject=True)

    _call_helper(module, helper_name, takes_attributes, span, RuntimeError("boom"))

    assert len(metric_calls) == 1
