import os
import logging
import traceback
import asyncio
import re
import json
from asyncio.tasks import Task
from aworld.config.conf import AgentConfig
from aworld.agents.llm_agent import Agent
from typing import Dict, Union

from aworld.core.context.base import Context
from aworld.utils.exec_util import exec_tasks

logger = logging.getLogger(__name__)

_trace_summary_cache: Dict[str, Union[str, Task]] = {}

trace_sys_prompt = "You are a helpful trace summary agent."

trace_prompt = """
    Please act as a trace summary agent, Using the provided trace_id and trace tool to fetch trace data, summarize the main tasks completed by each agent and their token usage,
    whether the run_type attribute of span is an agent or a large model call: 
        run_type=AGNET and is_event=True represents the agent, use event.id as agent name. 
        run_type=LLM and is_event=False represents the large model call.
        run_type=TOOL and is_event=True represents the tool call.
    The tool call and large model call of agent are manifested as the nearest child span of AGENT Span.
    Please summarize and output separately for agents with different event.ids.
    Please output in the following standard JSON format without any additional explanatory text:
    [{{"agent":"947cc4c1b7ed406ab7fbf38b9d2b1f5a",,"summary":"xxx","token_usage":"xxx","input_tokens":"xxx","output_tokens":"xxx","use_tools":["xxx"]}},{{}}]
    Here are the trace_id: {task}
    """

async def _do_summarize_trace(trace_id: str):
    logger.info(f"_do_summarize_trace trace_id: {trace_id}")
    model_name = os.getenv("LLM_MODEL_NAME_TRACE", None)
    base_url = os.getenv("LLM_BASE_URL_TRACE", None)
    api_key = os.getenv("LLM_API_KEY_TRACE", None)
    if not model_name or not base_url or not api_key:
        logger.warning(
            "LLM_MODEL_NAME_TRACE, LLM_BASE_URL_TRACE, LLM_API_KEY_TRACE is not set, trace summarize will not be executed.")
        return ""
    agent_config = AgentConfig(
        llm_provider=os.getenv("LLM_PROVIDER_TRACE", "openai"),
        llm_model_name=model_name,
        llm_base_url=base_url,
        llm_api_key=api_key,
    )
    trace_agent = Agent(
        conf=agent_config,
        name="trace_agent",
        system_prompt=trace_sys_prompt,
        agent_prompt=trace_prompt,
        tool_names=["trace"],
        feedback_tool_result=True
    )

    if trace_agent.conf.llm_api_key is None:
        logger.warning(
            "LLM_API_KEY_TRACE is not set, trace summarize will not be executed.")
        return ""
    try:
        res = await exec_tasks(trace_id, [trace_agent], Context())
        res = res[0]
        _trace_summary_cache[trace_id] = _fetch_json_from_result(res.answer)
        return _trace_summary_cache[trace_id]
    except Exception as e:
        logger.error(traceback.format_exc())


def summarize_trace(trace_id: str):
    if trace_id not in _trace_summary_cache:
        task = asyncio.create_task(_do_summarize_trace(trace_id))
        _trace_summary_cache[trace_id] = task


async def get_summarize_trace(trace_id: str):
    if trace_id not in _trace_summary_cache:
        return None
    cached_value = _trace_summary_cache[trace_id]
    if isinstance(cached_value, Task):
        # try:
        #     result = await cached_value
        #     if isinstance(result, Task):
        #         result = await result
        #     _trace_summary_cache[trace_id] = _fetch_json_from_result(result)
        # except Exception as e:
        #     logger.error(traceback.format_exc())
        return None
    return _trace_summary_cache[trace_id]


def _fetch_json_from_result(input_str):
    json_match = re.search(r'\[.*\]', input_str, re.DOTALL)
    if json_match:
        json_str = json_match.group(0)
        try:
            json.loads(json_str)
            return json_str
        except json.JSONDecodeError as e:
            logger.warning(
                f"_fetch_json_from_result json_str: {json_str} error: {e}")
    return ""
