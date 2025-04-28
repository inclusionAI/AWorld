from collections import defaultdict
from opentelemetry.sdk.trace import Span
from opentelemetry.sdk.trace.export import SpanExporter

class InMemoryStorage:
    """
    In-memory storage for spans.
    """
    def __init__(self):
        self._traces = defaultdict(list)
        # {trace_id: [span1, span2, ...]}

    def add_span(self, span: Span):
        trace_id = f"{span.get_span_context().trace_id:032x}"
        self._traces[trace_id].append(span)

    def get_all_traces(self):
        return list(self._traces.keys())

    def get_all_spans(self, trace_id):
        return self._traces.get(trace_id, [])

    def clear_traces(self):
        self._traces = []


class InMemorySpanExporter(SpanExporter):
    """
    Span exporter that stores spans in memory.
    """
    def __init__(self, storage: InMemoryStorage):
        self._storage = storage

    def export(self, spans):
        for span in spans:
            self._storage.add_span(span)

    def shutdown(self):
        pass
