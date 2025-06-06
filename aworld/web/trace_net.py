import requests
import json
import os
from pyvis.network import Network
from aworld.logs.util import logger

run_type_colors = {
    "LLM": "#E6E6FA",
    "TOOL": "#99FF99",   # green
    "MCP": "#99FF99",
    "AGENT": "#9999FF",  # blue
    "OTHER": "#FFFF99"  # yellow
}


def get_span_color(span):
    run_type = span.get('run_type', 'OTHER')
    return run_type_colors.get(run_type, run_type_colors['OTHER'])


def fetch_trace_data(trace_id=None):
    try:
        if trace_id:
            response = requests.get(
                f'http://localhost:7079/api/traces/{trace_id}')
            response.raise_for_status()
            return response.json() or {"root_span": []}
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching trace data: {e}")
        return {"root_span": []}


def flatten_spans(node, result=None):
    if result is None:
        result = []
    result.append(node)
    for child in node.get('children', []):
        flatten_spans(child, result)
    return result


def generate_trace_graph(trace_id=None):
    trace_data = fetch_trace_data(trace_id)
    net = Network(height="200px", width="100%",
                  notebook=False, cdn_resources='in_line')

    net.set_options("""
    {
      "nodes": {
        "font": {"size": 8}
      },
      "physics": {
        "enabled": true,
        "hierarchicalRepulsion": {
          "centralGravity": 0.5,
          "springLength": 150,
          "nodeDistance": 120
        }
      },
      "layout": {
        "hierarchical": {
          "enabled": true,
          "direction": "LR",
          "sortMethod": "directed",
          "nodeSpacing": 50,
          "levelSeparation": 100,
          "blockShifting": true,
          "edgeMinimization": true
        }
      }
    }
    """)
    root_spans = trace_data['root_span']
    all_spans = []
    for span in root_spans:
        all_spans.extend(flatten_spans(span))

    spans = {span['span_id']: span for span in all_spans}
    for span_id, span in spans.items():
        title = f"{span['name']}\n({span['run_type']})"
        net.add_node(span_id, label=title, title=title,
                     shape='box', color=get_span_color(span))
        if span.get('parent_id') and span['parent_id'] in spans:
            net.add_edge(span['parent_id'], span_id, arrows='to')

    net.show('trace_graph.html', notebook=False)
    with open('trace_graph.html', 'a') as f:
        f.write(f"""
        <style>
            #fullscreenBtn {{
                position: absolute;
                top: 10px;
                right: 10px;
                padding: 8px 12px;
                background: #4CAF50;
                color: white;
                border: none;
                border-radius: 4px;
                cursor: pointer;
                z-index: 1000;
            }}
        </style>
        <div style="margin-top: 20px; padding: 20px; border-top: 1px solid #eee;">
            <h3>Span Details</h3>
            <pre id="spanDetails" style="max-height: 600px; overflow-y: auto;">Click on a node to view details</pre>
        </div>
        <script>
            function moveNodesOnce() {{
                var nodes = network.body.nodes;
                var offsetX = 100;
                var offsetY = 80;
                for (var nodeId in nodes) {{
                    if (nodes.hasOwnProperty(nodeId)) {{
                        var node = nodes[nodeId];
                        network.moveNode(nodeId, node.x + offsetX, node.y + offsetY);
                    }}
                }}
                network.off("afterDrawing", moveNodesOnce);
            }}
            network.on("afterDrawing", moveNodesOnce);

            network.on("click", function(params) {{
                var nodeId = params.nodes[0];
                var spanData = {json.dumps(spans)}[nodeId];
                var displayData = {{}};
                for (var key in spanData) {{
                    if (key !== 'children') {{
                        displayData[key] = spanData[key];
                    }}
                }}
                document.getElementById("spanDetails").innerHTML = 
                    JSON.stringify(displayData, null, 2);
            }});
        </script>
        """)


def generate_trace_graph_full(trace_id=None, folder_name="", file_name="trace_graph_full.html"):
    trace_data = fetch_trace_data(trace_id)
    net = Network(height="100%", width="100%",
                  notebook=False, cdn_resources='in_line')

    net.set_options("""
    {
      "nodes": {
        "font": {"size": 8}
      },
      "physics": {
        "enabled": true,
        "hierarchicalRepulsion": {
          "centralGravity": 0.5,
          "springLength": 150,
          "nodeDistance": 120
        }
      },
      "layout": {
        "hierarchical": {
          "enabled": true,
          "direction": "LR",
          "sortMethod": "directed",
          "nodeSpacing": 50,
          "levelSeparation": 100,
          "blockShifting": true,
          "edgeMinimization": true
        }
      }
    }
    """)

    root_spans = trace_data['root_span']
    all_spans = []
    for span in root_spans:
        all_spans.extend(flatten_spans(span))

    spans = {span['span_id']: span for span in all_spans}
    for span_id, span in spans.items():
        title = f"{span['name']}\n({span['run_type']})"
        net.add_node(span_id, label=title, title=title,
                     shape='box', color=get_span_color(span))
        if span.get('parent_id') and span['parent_id'] in spans:
            net.add_edge(span['parent_id'], span_id, arrows='to')

    folder_path = os.path.join(os.getcwd(), folder_name)
    os.makedirs(folder_path, exist_ok=True)
    file_path = os.path.join(folder_path, file_name)
    net.show(file_path, notebook=False)

    with open(file_path, 'a') as f:
        f.write(f"""
        <style>
            body {{ margin: 0; padding: 0; }}
            #mynetwork {{
                width: 100vw;
                height: 100vh;
            }}
            #spanDetails {{
                position: absolute;
                right: 0;
                top: 0;
                width: 30%;
                height: 100%;
                padding: 20px;
                background: white;
                border-left: 1px solid #eee;
                overflow-y: auto;
                z-index: 1000;
            }}
        </style>
        <div>
            <h3>Span Details</h3>
            <pre id="spanDetails" style="max-height: 100%; overflow-y: auto;">Click on a node to view details</pre>
        </div>
        <script>
            function moveNodesOnce() {{
                var nodes = network.body.nodes;
                var offsetX = -250;
                var offsetY = 0;
                for (var nodeId in nodes) {{
                    if (nodes.hasOwnProperty(nodeId)) {{
                        var node = nodes[nodeId];
                        network.moveNode(nodeId, node.x + offsetX, node.y + offsetY);
                    }}
                }}
                network.off("afterDrawing", moveNodesOnce);
            }}
            network.on("afterDrawing", moveNodesOnce);
            network.on("click", function(params) {{
                var nodeId = params.nodes[0];
                var spanData = {json.dumps(spans)}[nodeId];
                var displayData = {{}};
                for (var key in spanData) {{
                    if (key !== 'children') {{
                        displayData[key] = spanData[key];
                    }}
                }}
                document.getElementById("spanDetails").innerHTML = 
                    JSON.stringify(displayData, null, 2);
            }});
        </script>
        """)
