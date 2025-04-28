import time
from flask import Flask, render_template, jsonify
from opentelemetry.sdk.trace import Span
from opentelemetry.trace import SpanContext
from typing import Union
from aworld.trace.opentelemetry.memory_storage import InMemoryStorage

app = Flask(__name__, template_folder='templates')

def span_to_dict(span):
    start_timestamp = span.start_time / 1e9
    end_timestamp = span.end_time / 1e9
    start_ms = int((span.start_time % 1e9) / 1e6)
    end_ms = int((span.end_time % 1e9) / 1e6)
    return {
        "trace_id": f"{span.get_span_context().trace_id:032x}",
        "span_id": get_span_id(span),
        "name": span.name,
        "start_time": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(start_timestamp)) + f'.{start_ms:03d}',
        "end_time": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(end_timestamp)) + f'.{end_ms:03d}',
        "duration_ms": (span.end_time - span.start_time)/1e6,
        "attributes": {
            k: v for k, v in span.attributes.items()
        },
        "status": {
            "code": str(span.status.status_code),
            "description": span.status.description
        },
        "parent_id": get_span_id(span.parent) if span.parent else None
    }

def get_span_id(span: Union[Span, SpanContext]):
    if isinstance(span, SpanContext):
        return f"{span.span_id:016x}"
    return f"{span.get_span_context().span_id:016x}"

def build_trace_tree(spans):
    spans_dict = {get_span_id(span): span_to_dict(span) for span in spans}
    root_spans = [span for span in spans_dict.values() if span.get("parent_id") is None]
    for span in spans_dict.values():
        parent_id = span.get("parent_id") if span.get("parent_id") else None
        if parent_id:
            parent_span = spans_dict[parent_id]
            if 'children' not in parent_span:
                parent_span['children'] = []
            parent_span['children'].append(span)
    return root_spans


def setup_routes(storage: InMemoryStorage):

    @app.route('/')
    def index():
        return render_template('index.html')

    @app.route('/api/traces')
    def traces():
        trace_data = []
        for trace_id in storage.get_all_traces():
            spans = storage.get_all_spans(trace_id)
            spans_sorted = sorted(spans, key=lambda x: x.start_time)
            trace_tree = build_trace_tree(spans_sorted)
            trace_data.append({
                'trace_id': trace_id,
                'root_span': trace_tree,
            })
        return jsonify(trace_data)
    
    return app
