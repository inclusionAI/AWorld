import base64


def build_node_id(id: str) -> str:
    encoded = base64.b64encode(id.encode()).decode()
    return f"n_{encoded[:10]}"  # 限制长度为8，加上前缀"n_"总共10个字符


def build_edge_id(id: str) -> str:
    encoded = base64.b64encode(id.encode()).decode()
    return f"e_{encoded[:10]}"  # 限制长度为8，加上前缀"e_"总共10个字符

def get_name_by_node_id(node_id: str) -> str:
    """标准化节点ID，确保特殊字符在Cypher查询中正确处理"""
    # 转义反斜杠和引号
    normalized_id = node_id.replace("\\", "\\\\").replace('"', '\\"')
    return normalized_id


