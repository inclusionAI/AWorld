import uuid
import datetime
from dataclasses import Field
from typing import Union, Dict, Any, List

from aworld.core.context.base import Context
from aworld.core.event import eventbus
from aworld.core.event.base import Message, Constants
from aworld.output import Output
from pydantic import BaseModel

from aworld.agents.llm_agent import Agent
from aworld.config import ConfigDict, AgentConfig
from aworld.core.agent.base import AgentFactory
from aworld.core.common import Observation, ActionModel
from aworld.models.llm import acall_llm_model

from ..deepresearch_sub_agent_v1.tools_and_schemas import parse_json_to_model, Reflection

prompt = """You are an expert research assistant analyzing summaries about "{research_topic}".

Instructions:
- Identify knowledge gaps or areas that need deeper exploration and generate a follow-up query. (1 or multiple).
- If provided summaries are sufficient to answer the user's question, don't generate a follow-up query.
- If there is a knowledge gap, generate a follow-up query that would help expand your understanding.
- Focus on technical details, implementation specifics, or emerging trends that weren't fully covered.
- 输出中文结果
Requirements:
- Ensure the follow-up query is self-contained and includes necessary context for web search.

Output Format:
- Format your response as a JSON object with these exact keys:
   - "is_sufficient": true or false
   - "knowledge_gap": Describe what information is missing or needs clarification
   - "follow_up_queries": Write a specific question to address this gap

Example:
```
{{
    "is_sufficient": true, // or false
    "knowledge_gap": "The summary lacks information about performance metrics and benchmarks", // "" if is_sufficient is true
    "follow_up_queries": ["What are typical performance benchmarks and metrics used to evaluate [specific technology]?"] // [] if is_sufficient is true
}}
```

Reflect carefully on the Summaries to identify knowledge gaps and produce a follow-up query. Then, produce your output following this JSON format:

Summaries:
{summaries}
"""


# @AgentFactory.register(name='reasoning_loop_agent', desc="reasoning_loop_agent")
class ReasoningLoopAgent(Agent):

    def __init__(self, conf: Union[Dict[str, Any], ConfigDict, AgentConfig], **kwargs):
        super().__init__(conf, **kwargs)
        # 初始化一个 reasoning_loop_count 变量来存储 reasoning_loop 的轮数
        self.context.context_info['reasoning_loop_count'] = 0
        self.context.context_info['max_reasoning_loop'] = 2

    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {}, **kwargs) -> List[ActionModel]:
        print("[reasoning_loop_agent]receive from web_search_agent")
        self.context.context_info['reasoning_loop_count'] += 1
        max_reasoning_loop = self.context.context_info['max_reasoning_loop']

        reasoning_loop_count = self.context.context_info['reasoning_loop_count']

        search_result = observation.content['search_result']
        search_topics = observation.content['search_topics']
        search_summary = observation.content['search_summary']

        # 1. 构造message
        messages = [{
            "role": "user",
            "content": prompt.format(
                current_date=datetime.datetime.now().strftime("%Y-%m-%d"),
                research_topic=search_topics,
                summaries=search_summary
            )
        }]

        # 2. call llm
        llm_response = await acall_llm_model(
            self.llm,
            messages=messages,
            model=self.model_name,
            temperature=self.conf.llm_config.llm_temperature,
            tools=self.tools if self.use_tools_in_prompt and self.tools else None
        )
        content = llm_response.get_message()['content']
        await eventbus.publish(Message(
            category=Constants.OUTPUT,
            payload=Output(data=f'\n\n{content}'),
            sender=self.id(),
            session_id=Context.instance().session_id
        ))
        # 3. 解析结果，判断是否ok，如果ok 就给reporting，如果不ok 就再转给web_search
        reflection = parse_json_to_model(content, Reflection)

        print("reasoning_loop_count:", reasoning_loop_count)
        print("is_sufficient:", reflection.is_sufficient)
        if (reasoning_loop_count >= max_reasoning_loop) or reflection.is_sufficient:
            web_search_topics = self.context.context_info.get('web_search_topics')
            web_search_summaries = self.context.context_info.get('web_search_summaries')
            return [ActionModel(
                agent_name=self.id(),
                tool_name=self.receiver("reporting_agent"),
                policy_info={
                    "research_topic": web_search_topics,
                    "summaries": web_search_summaries
                })]
        else:
            return [ActionModel(
                agent_name=self.id(),
                tool_name=self.receiver("web_search_agent"),
                policy_info=reflection.follow_up_queries)]

    def receiver(self, name):
        agent = self.context.swarm.find_agents_by_prefix(name)
        if agent:
            return agent[0].id()
        return ''