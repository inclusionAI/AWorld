from aworld.runners.state_manager import RuntimeStateManager, RunNodeBusiType, RunNodeStatus, RunNode
from typing import List


def test_runtime_state_manager():
    state_manager = RuntimeStateManager()
    session_id = "1"

    state_manager.create_node(busi_type=RunNodeBusiType.TASK,
                              busi_id="1", session_id=session_id, msg_id="1")

    state_manager.run_node(busi_type=RunNodeBusiType.TASK, busi_id="1")
    node = state_manager.get_node(busi_type=RunNodeBusiType.TASK, busi_id="1")
    assert node.status == RunNodeStatus.RUNNING

    state_manager.break_node(busi_type=RunNodeBusiType.TASK, busi_id="1")
    node = state_manager.get_node(busi_type=RunNodeBusiType.TASK, busi_id="1")
    assert node.status == RunNodeStatus.BREAKED

    state_manager.run_succeed(busi_type=RunNodeBusiType.TASK, busi_id="1")
    node = state_manager.get_node(busi_type=RunNodeBusiType.TASK, busi_id="1")
    assert node.status == RunNodeStatus.SUCCESS

    state_manager.create_node(busi_type=RunNodeBusiType.TASK,
                              busi_id="2", session_id=session_id, msg_id="2", msg_from="1")

    state_manager.run_node(busi_type=RunNodeBusiType.TASK, busi_id="2")
    state_manager.run_failed(busi_type=RunNodeBusiType.TASK, busi_id="2")
    node = state_manager.get_node(busi_type=RunNodeBusiType.TASK, busi_id="2")
    assert node.status == RunNodeStatus.FAILED

    state_manager.create_node(busi_type=RunNodeBusiType.TASK,
                              busi_id="3", session_id=session_id, msg_id="3", msg_from="1")
    state_manager.run_node(busi_type=RunNodeBusiType.TASK, busi_id="3")
    state_manager.run_timeout(busi_type=RunNodeBusiType.TASK, busi_id="3")
    node = state_manager.get_node(busi_type=RunNodeBusiType.TASK, busi_id="3")
    assert node.status == RunNodeStatus.TIMEOUNT

    state_manager.create_node(busi_type=RunNodeBusiType.TASK,
                              busi_id="4", session_id=session_id, msg_id="4", msg_from="3")
    state_manager.run_succeed(busi_type=RunNodeBusiType.TASK, busi_id="1")

    nodes = state_manager.get_nodes(session_id=session_id)
    build_run_flow(nodes)


def build_run_flow(nodes: List[RunNode]):
    graph = {}
    start_nodes = []

    for node in nodes:
        if hasattr(node, 'parent_node_id') and node.parent_node_id:
            if node.parent_node_id not in graph:
                graph[node.parent_node_id] = []
            graph[node.parent_node_id].append(node.node_id)
        else:
            start_nodes.append(node.node_id)

    for start in start_nodes:
        print("-----------------------------------")
        _print_tree(graph, start, "", True)
        print("-----------------------------------")


def _print_tree(graph, node_id, prefix, is_last):
    print(prefix + ("└── " if is_last else "├── ") + node_id)
    if node_id in graph:
        children = graph[node_id]
        for i, child in enumerate(children):
            _print_tree(graph, child, prefix +
                        ("    " if is_last else "│   "), i == len(children)-1)


if __name__ == "__main__":
    test_runtime_state_manager()
