from aworld.logs.util import logger
from flask import Flask, render_template, jsonify
from aworld.trace.opentelemetry.memory_storage import SpanModel, TraceStorage

app = Flask(__name__, template_folder='../../web/templates')
current_storage = None
routes_setup = False



def build_trace_tree(spans: list[SpanModel]):
    spans_dict = {span.span_id: span.dict() for span in spans}
    root_spans = [span for span in spans_dict.values()
                  if span['parent_id'] is None]
    for span in spans_dict.values():
        parent_id = span['parent_id'] if span['parent_id'] else None
        if parent_id:
            parent_span = spans_dict.get(parent_id)
            if not parent_span:
                logger.warning(f"span[{parent_id}] not be exported")
                continue
            if 'children' not in parent_span:
                parent_span['children'] = []
            parent_span['children'].append(span)
    return root_spans


def setup_routes(storage: TraceStorage):
    global current_storage
    current_storage = storage

    global routes_setup

    if routes_setup:
        return app

    @app.route('/')
    def index():
        return render_template('trace_ui.html')

    @app.route('/api/traces')
    def traces():
        trace_data = []
        for trace_id in current_storage.get_all_traces():
            spans = current_storage.get_all_spans(trace_id)
            spans_sorted = sorted(spans, key=lambda x: x.start_time)
            trace_tree = build_trace_tree(spans_sorted)
            trace_data.append({
                'trace_id': trace_id,
                'root_span': trace_tree,
            })
        return jsonify(trace_data)

    @app.route('/api/traces/<trace_id>')
    def get_trace(trace_id):
        spans = current_storage.get_all_spans(trace_id)
        spans_sorted = sorted(spans, key=lambda x: x.start_time)
        trace_tree = build_trace_tree(spans_sorted)
        return jsonify({
            'trace_id': trace_id,
            'root_span': trace_tree,
        })

    routes_setup = True
    return app
