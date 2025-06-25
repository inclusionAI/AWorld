from aworld.trace.opentelemetry.memory_storage import TraceStorage
from aworld.utils.import_package import import_package
from aworld.trace.server.util import build_trace_tree

import_package('flask')  # noqa
from flask import Flask, render_template, jsonify  # noqa

app = Flask(__name__, template_folder='../../web/templates')
current_storage = None
routes_setup = False


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
