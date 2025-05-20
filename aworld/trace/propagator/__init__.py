# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from aworld.trace.propagator.w3c import W3CTraceContextPropagator

_GLOBAL_TRACE_PROPAGATOR = W3CTraceContextPropagator()

def get_global_trace_propagator():
    return _GLOBAL_TRACE_PROPAGATOR
