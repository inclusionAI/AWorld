import os
import uuid
from typing import Dict, Any, List
import json
import re
import datetime

from aworld.config.conf import AgentConfig
from aworld.agents.llm_agent import Agent
from aworld.core.agent.base import AgentFactory
from aworld.core.common import Observation, ActionModel
from aworld.core.context.base import Context
from aworld.core.event import eventbus
from aworld.core.event.base import Constants, Message
from aworld.core.task import Task
from aworld.config import ModelConfig, TaskConfig
from aworld.output import Output

from ..deepresearch_sub_agent_v1.tools_and_schemas import parse_json_to_model, SearchQueryList
from ..deepresearch_prompt import *
from aworld.runner import Runners

from aworld.models.llm import acall_llm_model

prompt = """Your goal is to generate sophisticated and diverse web search queries. These queries are intended for an advanced automated web research tool capable of analyzing complex results, following links, and synthesizing information.

Instructions:
- Always prefer a single search query, only add another query if the original question requests multiple aspects or elements and one query is not enough.
- Each query should focus on one specific aspect of the original question.
- Don't produce more than {number_queries} queries.
- Queries should be diverse, if the topic is broad, generate more than 1 query.
- Don't generate multiple similar queries, 1 is enough.
- Query should ensure that the most current information is gathered. The current date is {current_date}.
- 输出中文结果
Format: 
- Format your response as a JSON object with ALL three of these exact keys:
   - "rationale": Brief explanation of why these queries are relevant
   - "query": A list of search queries

Example:

Topic: What revenue grew more last year apple stock or the number of people buying an iphone
```
{{
    "rationale": "To answer this comparative growth question accurately, we need specific data points on Apple's stock performance and iPhone sales metrics. These queries target the precise financial information needed: company revenue trends, product-specific unit sales figures, and stock price movement over the same fiscal period for direct comparison.",
    "query": ["Apple total revenue growth fiscal year 2024", "iPhone unit sales growth fiscal year 2024", "Apple stock price growth fiscal year 2024"],
}}
```

Context: {research_topic}"""

class PlanAgent(Agent):

    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {}, **kwargs) -> List[ActionModel]:
        # 1.构造 message
        messages = [{
            "role": "user",
            "content": prompt.format(
                current_date=datetime.datetime.now().strftime("%Y-%m-%d"),
                research_topic=observation.content,
                number_queries=3,
            )
        }]

        await eventbus.publish(Message(
            category=Constants.OUTPUT,
            payload=Output(data=f'已收到你的任务，正在进行问题规划分析：\n\n'),
            sender=self.id(),
            session_id=Context.instance().session_id
        ))
        # 2.call llm
        llm_response = await acall_llm_model(
            self.llm,
            messages=messages,
            model=self.model_name,
            temperature=self.conf.llm_config.llm_temperature,
            tools=self.tools if self.use_tools_in_prompt and self.tools else None
        )

        # 3. 解析LLM响应，提取query列表
        content = llm_response.get_message()['content']
        query_list = parse_json_to_model(content, SearchQueryList)
        print(f"query_list: {query_list.query}")
        await eventbus.publish(Message(
            category=Constants.OUTPUT,
            payload=Output(data=f'任务分拆为搜索下述问题：{query_list.query}'),
            sender=self.id(),
            session_id=Context.instance().session_id
        ))
        # 4. 转交给 web_search_agent 处理
        return [ActionModel(
            agent_name=self.id(),
            policy_info=query_list.query)]