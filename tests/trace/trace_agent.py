import traceback
from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig
from typing import List
from aworld.trace.config import ObservabilityConfig
from aworld.logs.util import logger
from aworld.core.agent.swarm import Swarm
from aworld.runner import Runners
from aworld.trace.server import get_trace_server
from aworld.runners.state_manager import RuntimeStateManager, RunNode
import aworld.trace as trace

trace.configure(ObservabilityConfig(trace_server_enabled=True,
                                    metrics_provider="otlp",
                                    metrics_backend="antmonitor",
                                    metrics_base_url="https://antcollector.alipay.com/namespace/aworld/task/aworld/otlp/api/v1/metrics"))

search_sys_prompt = "You are a helpful search agent."
search_prompt = """
    Please act as a search agent, constructing appropriate keywords and searach terms, using search toolkit to collect relevant information, including urls, webpage snapshots, etc.

    Here are the question: {task}

    pleas only use one action complete this task, at least results 6 pages.
    """

summary_sys_prompt = "You are a helpful general summary agent."

summary_prompt = """
Summarize the following text in one clear and concise paragraph, capturing the key ideas without missing critical points. 
Ensure the summary is easy to understand and avoids excessive detail.

Here are the content: 
{task}
"""

trace_sys_prompt = "You are a helpful trace summary agent."

trace_prompt = """
    Please act as a trace summary agent, Using the provided trace data, summarize the main tasks completed by each agent and their token usage,
    whether the run_type attribute of span is an agent or a large model call: 
        run_type=AGNET and is_event=True represents the agent, 
        run_type=LLM and is_event=False represents the large model call.
        run_type=TOOL and is_event=True represents the tool call.
    The tool call and large model call of agent are manifested as the nearest child span of AGENT Span.
    Please output in the following standard JSON format without any additional explanatory text:
    [{{"agent":"xxx","summary":"xxx","token_usage":"xxx","input_tokens":"xxx","output_tokens":"xxx","use_tools":["xxx"]}}]
    Here are the trace data: {task}
    """


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
                        ("    " if is_last else "│   "), i == len(children) - 1)


def run():
    agent_config = AgentConfig(
        llm_provider="openai",
        llm_model_name="claude-3-7-sonnet-20250219",
        llm_base_url="xxx",
        llm_api_key="xxx",
    )

    search = Agent(
        conf=agent_config,
        name="search_agent",
        system_prompt=search_sys_prompt,
        agent_prompt=search_prompt,
        tool_names=["search_api"]
    )

    summary = Agent(
        conf=agent_config,
        name="summary_agent",
        system_prompt=summary_sys_prompt,
        agent_prompt=summary_prompt
    )

    # default is sequence swarm mode
    swarm = Swarm(search, summary, max_steps=1, event_driven=True)

    prefix = "search baidu:"
    # can special search google, wiki, duck go, or baidu. such as:
    # prefix = "search wiki: "
    try:
        res = Runners.sync_run(
            input=prefix + """What is an agent.""",
            swarm=swarm,
            session_id="123"
        )
        print(res.answer)
    except Exception as e:
        logger.error(traceback.format_exc())

    state_manager = RuntimeStateManager.instance()
    nodes = state_manager.get_nodes("123")
    logger.info(f"session 123 nodes: {nodes}")
    build_run_flow(nodes)
    get_trace_server().join()


# if __name__ == "__main__":
#     run()
