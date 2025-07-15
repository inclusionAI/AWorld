import uuid
import datetime
import asyncio
from dataclasses import Field
from typing import Union, Dict, Any, List

from pydantic import BaseModel

from aworld.agents.llm_agent import Agent
from aworld.config import ConfigDict, AgentConfig
from aworld.core.agent.base import AgentFactory
from aworld.core.common import Observation, ActionModel
from aworld.core.memory import MemoryItem
from aworld.models.llm import acall_llm_model

from ..deepresearch_sub_agent_v1.tools_and_schemas import parse_json_to_model, parse_json_to_model_list, AworldSearch

prompt = """Conduct targeted aworld_search tools to gather the most recent, credible information on "{research_topic}" and synthesize it into a verifiable text artifact.

Instructions:
- Query should ensure that the most current information is gathered. The current date is {current_date}.
- Conduct multiple, diverse searches to gather comprehensive information.
- Consolidate key findings while meticulously tracking the source(s) for each specific piece of information.
- The output should be a well-written summary or report based on your search findings. 
- Only include the information found in the search results, don't make up any information.
- 输出中文结果
- 搜索工具的入参数量为1，结果数也为1
Research Topic:
{research_topic}
"""

# @AgentFactory.register(name='web_search_agent', desc="web_search_agent")
class WebSearchAgent(Agent):
    def __init__(self, conf: Union[Dict[str, Any], ConfigDict, AgentConfig], **kwargs):
        super().__init__(conf, **kwargs)
        # 初始化一个 web_search_nums 变量用来记录 search 的次数
        self.context.context_info['web_search_nums'] = 0
        # 添加异步锁来保护计数器
        self._counter_lock = asyncio.Lock()

        # 汇总所有的搜索topic
        self.context.context_info['web_search_topics'] = []
        # 汇总所有的搜索结果
        self.context.context_info['web_search_summaries'] = []

    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {}, **kwargs) -> List[ActionModel]:
        # 来源可能是 plan_agent 也 可能是 reasoning_loop_agent
        print("[web_search_agent]receive from", observation.from_agent_name)
        print("[web_search_agent]receive content", observation.content)

        # 记录搜索结果
        search_result = []

        # 处理工具初始化
        await self.async_desc_transform()
        
        # 创建异步任务处理函数，并行执行搜索
        async def process_research_topic(research_topic):
            try:
                if hasattr(observation, 'context') and observation.context:
                    self.task_histories = observation.context
                self._finished = False
                images = observation.images if self.conf.use_vision else None
                if self.conf.use_vision and not images and observation.image:
                    images = [observation.image]

                # 1. 构造message
                messages = [{
                    "role": "user",
                    "content": prompt.format(
                        current_date=datetime.datetime.now().strftime("%Y-%m-%d"),
                        research_topic=research_topic
                    )
                }]

                self._log_messages(messages)
                await self.memory.add(MemoryItem(
                    content=messages[-1]['content'],
                    metadata={
                        "role": messages[-1]['role'],
                        "agent_name": self.name(),
                        "tool_call_id": messages[-1].get("tool_call_id")
                    }
                ))

                # 2.call llm and tools
                llm_response = await self.llm_and_tool_execution(
                    observation=observation,
                    messages=messages,
                    tools=self.tools if not self.use_tools_in_prompt and self.tools else None,
                )

                # 使用锁来安全地更新计数器
                async with self._counter_lock:
                    self.context.context_info['web_search_nums'] += 1
                    print("web_search_nums", self.context.context_info['web_search_nums'])

                if isinstance(llm_response[0], ActionModel):
                    aworld_search_list = parse_json_to_model_list(llm_response[0].policy_info, AworldSearch)
                    if len(aworld_search_list) > 0:
                        return aworld_search_list[0].doc
                    else:
                        print("[web_search_agent] llm_response is null")
                        return None
                return None
            except Exception as e:
                print(f"[web_search_agent] Error processing research_topic '{research_topic}': {e}")
                return None

        # 创建任务列表并执行
        tasks = []
        for research_topic in observation.content:
            task = asyncio.create_task(process_research_topic(research_topic))
            tasks.append(task)
        
        # 使用gather执行，并添加异常处理
        try:
            # 使用return_exceptions=True来避免单个任务异常影响其他任务
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # 处理结果，过滤掉异常和None值
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    print(f"[web_search_agent] Task {i} failed with exception: {result}")
                elif result is not None:
                    search_result.append(result)
                    
        except Exception as e:
            print(f"[web_search_agent] Unexpected error in gather: {e}")
            # 尝试取消所有未完成的任务
            for task in tasks:
                if not task.done():
                    task.cancel()
            
            # 等待所有任务完成或取消
            try:
                await asyncio.gather(*tasks, return_exceptions=True)
            except Exception:
                pass  # 忽略取消时的异常

        # 收集和汇总结果然后给 reasoning_loop_agent
        web_search_topics = self.context.context_info.get('web_search_topics')
        web_search_topics.extend(observation.content)
        web_search_summaries = self.context.context_info.get('web_search_summaries')
        web_search_summaries.extend(search_result)

        return [ActionModel(
            agent_name=self.id(),
            policy_info={
                "search_result": search_result,
                "search_summary": search_result,
                "search_topics": observation.content
            })]