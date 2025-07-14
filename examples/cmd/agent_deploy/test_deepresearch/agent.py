import logging
import os
from pathlib import Path
import sys

from aworld.core.context.base import Context
from aworld.memory.models import MemorySystemMessage, MessageMetadata
from aworld.planner.plan import DefaultPlanner, PlannerOutputParser

from aworld.core.agent.swarm import TeamSwarm
from aworld.runner import Runners
from examples.tools.common import Tools

from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig, ModelConfig

from aworld.cmd import BaseAWorldAgent, ChatCompletionRequest
from aworld.config.conf import AgentConfig, ModelConfig, TaskConfig
from aworld.agents.llm_agent import Agent
from aworld.core.task import Task
from aworld.runner import Runners
from .prompts import *

logger = logging.getLogger(__name__)

# os.environ["LLM_MODEL_NAME"] = "qwen/qwen3-8b"
# os.environ["LLM_BASE_URL"] = "http://localhost:1234/v1"
os.environ["LLM_MODEL_NAME"] = "DeepSeek-V3"
os.environ["LLM_BASE_URL"] = "https://agi.alipay.com/api"
os.environ["LLM_API_KEY"] = "sk-5d0c421b87724cdd883cfa8e883998da"

class PlanAgent(Agent):

    # multi turn system prompt generation
    async def _add_system_message_to_memory(self, context: Context, content: str):
        session_id = context.get_task().session_id
        task_id = context.get_task().id
        user_id = context.get_task().user_id

        if not self.system_prompt:
            return
        content = await self.custom_system_prompt(context=context, content=content)
        logger.info(f'system prompt content: {content}')

        self.memory.add(MemorySystemMessage(
            content=content,
            metadata=MessageMetadata(
                session_id=session_id,
                user_id=user_id,
                task_id=task_id,
                agent_id=self.id(),
                agent_name=self.name(),
            )
        ), agent_memory_config=self.memory_config)
        logger.info(
            f"🧠 [MEMORY:short-term] Added system input to agent memory:  Agent#{self.id()}, 💬 {content[:100]}...")


def get_deepresearch_swarm(user_input):

    agent_config = AgentConfig(
        llm_config=ModelConfig(
            llm_model_name=os.getenv("LLM_MODEL_NAME"),
            llm_base_url=os.getenv("LLM_BASE_URL"),
            llm_api_key=os.getenv("LLM_API_KEY")
        ),
        use_vision=False
    )

    agent_id = "test_deepresearch_agent"
    plan_agent = PlanAgent(
        agent_id=agent_id,
        name="planner_agent",
        desc="planner_agent",
        conf=agent_config,
        use_tools_in_prompt=True,
        resp_parse_func=PlannerOutputParser(agent_id).parse,
        system_prompt_template=plan_sys_prompt,
    )

    web_search_agent = Agent(
        name="web_search_agent",
        desc="web_search_agent",
        conf=agent_config,
        system_prompt_template=search_sys_prompt,
        tool_names=[Tools.SEARCH_API.value]
    )
    
    reporting_agent = Agent(
        name="reporting_agent",
        desc="reporting_agent",
        conf=agent_config,
        system_prompt_template=reporting_sys_prompt,
    )

    return TeamSwarm(plan_agent, web_search_agent, reporting_agent, max_steps=1)
    

class AWorldAgent(BaseAWorldAgent):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def name(self):
        return "Test Deepresearch Agent"

    def description(self):
        return "Test Deepresearch Agent"

    async def run(self, prompt: str = None, request: ChatCompletionRequest = None):

        if prompt is None and request is not None:
            prompt = request.messages[-1].content
        
        swarm = get_deepresearch_swarm(prompt)

        task = Task(
            input=prompt,
            swarm=swarm,
            conf=TaskConfig(max_steps=20),
            session_id=request.session_id,
            endless_threshold=50,
        )

        async for output in Runners.streamed_run_task(task).stream_events():
            logger.info(f"Agent Ouput: {output}")
            yield output
