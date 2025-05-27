import requests
import json
import aworld.trace
from pyvis.network import Network


def fetch_trace_data(trace_id=None):
    if trace_id:
        response = requests.get(f'http://localhost:8000/api/traces/{trace_id}')
        if response.status_code != 200 or not response.json():
            return {"root_span": []}
        return response.json()
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
        "font": {"size": 8},
        "physics": false
      },
      "edges": {
        "smooth": false
      },
      "layout": {
        "hierarchical": {
          "enabled": true,
          "direction": "LR",
          "sortMethod": "directed",
          "nodeSpacing": 80,
          "levelSeparation": 150
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
        net.add_node(span_id, label=span['name'], title=span['name'],
                     shape='box', color='#97c2fc')
        if span.get('parent_id') and span['parent_id'] in spans:
            net.add_edge(span['parent_id'], span_id, arrows='to')

    net.show('trace_graph.html', notebook=False)

    with open('trace_graph.html', 'a') as f:
        f.write(f"""
        <div style="margin-top: 20px; padding: 20px; border-top: 1px solid #eee;">
            <h3>Span Details</h3>
            <pre id="spanDetails" style="max-height: 300px; overflow-y: auto;">Click on a node to view details</pre>
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
                document.getElementById("spanDetails").innerHTML = 
                    JSON.stringify(spanData, null, 2);
            }});
        </script>
        """)


if __name__ == "__main__":
    generate_trace_graph()
