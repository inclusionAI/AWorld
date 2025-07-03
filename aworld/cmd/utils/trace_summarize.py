import os
import logging
import traceback
import asyncio
import re
import json
import pickle
from asyncio.tasks import Task
from aworld.config.conf import AgentConfig
from aworld.agents.llm_agent import Agent
from typing import Dict, Union

from aworld.core.context.base import Context
from aworld.utils.exec_util import exec_tasks
from examples.tools.trace.trace_tool import TraceTool

logger = logging.getLogger(__name__)


class SimpleSummaryCache():
    def __init__(self) -> None:
        self._cache_file = os.path.join(os.curdir, "data", "trace_summary_cache.pkl")
        self._cache: Dict[str, str] = {}
        self._load_cache()

    def _load_cache(self):
        if os.path.exists(self._cache_file):
            try:
                with open(self._cache_file, 'rb') as f:
                    self._cache = pickle.load(f)
            except (pickle.PickleError, EOFError):
                logger.warning("Cache file is corrupted, creating new cache")
                if self._cache_file.exists():
                    self._cache_file.unlink()

    def _save_cache(self):
        serializable_cache = {k: v for k, v in self._cache.items() if not isinstance(v, Task)}
        try:
            with open(self._cache_file, 'wb') as f:
                pickle.dump(serializable_cache, f)
        except pickle.PickleError:
            logger.error("Failed to save cache")

    def add_to_cache(self, trace_id: str, value: Union[str, Task]):
        self._cache[trace_id] = value
        if not isinstance(value, Task):
            self._save_cache()

    def get_value(self, trace_id: str) -> Union[str, Task]:
        return self._cache.get(trace_id)

    def trace_exists(self, trace_id: str) -> bool:
        return trace_id in self._cache


# _trace_summary_cache: Dict[str, Union[str, Task]] = {}
_trace_summary_cache = SimpleSummaryCache()

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
    Here is the trace_id: {task}
    """

agent_config = None


async def _do_summarize_trace(trace_id: str):
    logger.info(f"_do_summarize_trace trace_id: {trace_id}")

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
        summary = _fetch_json_from_result(res.answer)
        _trace_summary_cache.add_to_cache(trace_id, summary)
        return summary
    except Exception as e:
        logger.error(traceback.format_exc())


def summarize_trace(trace_id: str):
    agent_config = AgentConfig(
        llm_provider=os.getenv("LLM_PROVIDER_TRACE", "openai"),
        llm_model_name=os.getenv("LLM_MODEL_NAME_TRACE", None),
        llm_base_url=os.getenv("LLM_BASE_URL_TRACE", None),
        llm_api_key=os.getenv("LLM_API_KEY_TRACE", None),
    )
    if not _trace_summary_cache.trace_exists(trace_id):
        if agent_config.llm_api_key is None or not agent_config.llm_base_url or not agent_config.llm_model_name:
            logger.warning(
                "LLM_MODEL_NAME_TRACE, LLM_BASE_URL_TRACE, LLM_API_KEY_TRACE is not set, trace summarize will not be executed.")
            return

        task = asyncio.create_task(_do_summarize_trace(trace_id))
        _trace_summary_cache.add_to_cache(trace_id, task)


async def get_summarize_trace(trace_id: str):
    if not _trace_summary_cache.trace_exists(trace_id):
        return None
    cached_value = _trace_summary_cache.get_value(trace_id)
    if isinstance(cached_value, Task):
        # try:
        #     result = await cached_value
        #     if isinstance(result, Task):
        #         result = await result
        #     _trace_summary_cache[trace_id] = _fetch_json_from_result(result)
        # except Exception as e:
        #     logger.error(traceback.format_exc())
        return None
    return cached_value


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
