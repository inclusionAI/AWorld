import os
import logging
import traceback
from aworld.config.conf import AgentConfig
from aworld.agents.llm_agent import Agent
from typing import Dict
from aworld.runner import Runners
from examples.tools.common import Tools

logger = logging.getLogger(__name__)

_trace_summary_cache: Dict[str, str] = {}

trace_sys_prompt = "You are a helpful trace summary agent."

trace_prompt = """
    Please act as a trace summary agent, Using the provided trace_id and trace tool to fetch trace data, summarize the main tasks completed by each agent and their token usage,
    whether the run_type attribute of span is an agent or a large model call: 
        run_type=AGNET and is_event=True represents the agent, 
        run_type=LLM and is_event=False represents the large model call.
        run_type=TOOL and is_event=True represents the tool call.
    The tool call and large model call of agent are manifested as the nearest child span of AGENT Span.
    Please output in the following standard JSON format without any additional explanatory text:
    [{{"agent":"xxx","summary":"xxx","token_usage":"xxx","input_tokens":"xxx","output_tokens":"xxx","use_tools":["xxx"]}}]
    Here are the trace_id: {task}
    """

agent_config = AgentConfig(
    llm_provider=os.getenv("LLM_PROVIDER_TRACE", "openai"),
    llm_model_name=os.getenv("LLM_MODEL_NAME_TRACE"),
    llm_base_url=os.getenv("LLM_BASE_URL_TRACE"),
    llm_api_key=os.getenv("LLM_API_KEY_TRACE")
)

trace_agent = Agent(
    conf=agent_config,
    name="trace_agent",
    system_prompt=trace_sys_prompt,
    agent_prompt=trace_prompt,
    tool_names=["trace"]
)


async def summarize_trace(trace_id: str):
    if trace_agent.conf.llm_api_key is None:
        logger.warning(
            "LLM_API_KEY_TRACE is not set, trace summarize will not be executed.")
        return ""
    if trace_id in _trace_summary_cache:
        return _trace_summary_cache[trace_id]
    try:
        res = await Runners.run(
            input=trace_id,
            agent=trace_agent
        )
        logger.info(res.answer)
        _trace_summary_cache[trace_id] = res.answer
        return res.answer
    except Exception as e:
        logger.error(traceback.format_exc())
