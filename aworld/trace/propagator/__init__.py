# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from contextvars import ContextVar, Token
from aworld.trace.base import TraceContext
from aworld.trace.propagator.w3c import W3CTraceContextPropagator

_GLOBAL_TRACE_PROPAGATOR = W3CTraceContextPropagator()


def get_global_trace_propagator():
    return _GLOBAL_TRACE_PROPAGATOR


class TraceContextHolder:
    def __init__(self):
        self._var = ContextVar("current_trace_context", default=None)

    def set(self, trace_context: TraceContext) -> Token:
        token = self._var.set(trace_context)
        return token

    def get_and_clear(self) -> TraceContext:
        try:
            value = self._var.get()
        except LookupError:
            return self._var.get(None)
        finally:
            self._var.set(None)
        return value

    def get(self) -> TraceContext:
        return self._var.get()

    def reset(self, token: Token):
        self._var.reset(token)


_GLOBAL_TRACE_CONTEXT = TraceContextHolder()


def get_global_trace_context():
    return _GLOBAL_TRACE_CONTEXT
