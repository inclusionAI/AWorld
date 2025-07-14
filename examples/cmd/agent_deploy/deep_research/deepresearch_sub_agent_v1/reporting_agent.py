import os
import uuid
import datetime
from typing import Dict, Any, List

from aworld.config.conf import AgentConfig
from aworld.agents.llm_agent import Agent
from aworld.core.agent.base import AgentFactory
from aworld.core.common import Observation, ActionModel
from aworld.core.context.base import Context
from aworld.core.event import eventbus
from aworld.core.event.base import Message, Constants
from aworld.core.task import Task
from aworld.config import ModelConfig, TaskConfig
from aworld.output import Output

from ..deepresearch_prompt import *
from aworld.runner import Runners
from aworld.models.llm import acall_llm_model


prompt = """Generate a high-quality answer to the user's question based on the provided summaries.

Instructions:
- The current date is {current_date}.
- You are the final step of a multi-step research process, don't mention that you are the final step. 
- You have access to all the information gathered from the previous steps.
- You have access to the user's question.
- Generate a high-quality answer to the user's question based on the provided summaries and the user's question.
- you MUST include all the citations from the summaries in the answer correctly.
- 用HTML结构化规范输出结果
User Context:
- {research_topic}

Summaries:
{summaries}"""

# @AgentFactory.register(name='reporting_agent', desc="reporting_agent")
class ReportingAgent(Agent):

    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {}, **kwargs) -> List[ActionModel]:
        print("[reporting_agent]receive from reasoning_loop_agent:", observation.content)

        # 1.构造 message
        messages = [{
            "role": "user",
            "content": prompt.format(
                current_date=datetime.datetime.now().strftime("%Y-%m-%d"),
                research_topic=observation.content['research_topic'],
                summaries=observation.content['summaries'],
            )
        }]

        # 2.call llm
        await eventbus.publish(Message(
            category=Constants.OUTPUT,
            payload=Output(data=f'\n\n正在生成报告，请等待...'),
            sender=self.id(),
            session_id=Context.instance().session_id
        ))

        llm_response = await acall_llm_model(
            self.llm,
            messages=messages,
            model=self.model_name,
            temperature=self.conf.llm_config.llm_temperature,
            tools=self.tools if self.use_tools_in_prompt and self.tools else None
        )
        await eventbus.publish(Message(
            category=Constants.OUTPUT,
            payload=Output(data=f'\n\n已经完成报告生成，请查看'),
            sender=self.id(),
            session_id=Context.instance().session_id
        ))
        content = llm_response.get_message()['content']
        await eventbus.publish(Message(
            category=Constants.OUTPUT,
            payload=Output(data=f'\n\n{content}'),
            sender=self.id(),
            session_id=Context.instance().session_id
        ))

        return [ActionModel(
            agent_name=self.id(),
            policy_info=content)]