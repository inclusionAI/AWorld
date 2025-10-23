import base64


def build_node_id(id: str) -> str:
    encoded = base64.b64encode(id.encode()).decode()
    return f"n_{encoded[:10]}"  # Limit length to 8, plus prefix "n_" for total 10 characters


def build_edge_id(id: str) -> str:
    encoded = base64.b64encode(id.encode()).decode()
    return f"e_{encoded[:10]}"  # Limit length to 8, plus prefix "e_" for total 10 characters

def get_name_by_node_id(node_id: str) -> str:
    """Normalize node ID to ensure special characters are handled correctly in Cypher queries"""
    # Escape backslashes and quotes
    normalized_id = node_id.replace("\\", "\\\\").replace('"', '\\"')
    return normalized_id


