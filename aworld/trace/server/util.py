from aworld.logs.util import logger
from aworld.trace.opentelemetry.memory_storage import SpanModel


def build_trace_tree(spans: list[SpanModel]):
    spans_dict = {span.span_id: span.dict() for span in spans}
    for span in list(spans_dict.values()):
        parent_id = span['parent_id'] if span['parent_id'] else None
        if parent_id:
            parent_span = spans_dict.get(parent_id)
            if not parent_span:
                logger.warning(f"span[{parent_id}] not be exported")
                parent_span = {
                    'span_id': parent_id,
                    'trace_id': span['trace_id'],
                    'name': 'Pengding-Span',
                    'start_time': span['start_time'],
                    'end_time': span['end_time'],
                    'duration_ms': span['duration_ms'],
                    'attributes': {},
                    'status': {},
                    'parent_id': None,
                    'run_type': 'OTHER'
                }
                spans_dict[parent_id] = parent_span
            if 'children' not in parent_span:
                parent_span['children'] = []
            parent_span['children'].append(span)

    root_spans = [span for span in spans_dict.values()
                  if span['parent_id'] is None]
    return root_spans
