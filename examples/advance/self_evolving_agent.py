import logging
import os
from datetime import datetime
from typing import List, Dict, Any, Optional

from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig, TaskConfig
from aworld.core.common import ActionModel, Observation
from aworld.core.context.base import Context
from aworld.core.event.base import Message
from aworld.core.memory import MemoryConfig, LongTermConfig, MemoryItem
from aworld.core.task import Task
from aworld.memory.main import MemoryFactory
from aworld.memory.models import LongTermMemoryTriggerParams, MemoryAIMessage, MessageMetadata, UserProfile, \
    MemoryHumanMessage
from aworld.memory.utils import build_history_context
from aworld.output import PrinterAworldUI, AworldUI
from aworld.runner import Runners
from aworld.utils.common import load_mcp_config

self_evolving_agent_prompt = """
        You are a highly capable and intelligent AI assistant with the following key traits:
        1. Adaptability - You learn from past interactions and experiences to continuously improve
        2. Helpfulness - You aim to provide useful and actionable assistance to users
        3. Professionalism - You maintain a polite and professional tone while being friendly
        4. Knowledge - You leverage your broad knowledge base to provide accurate information
        5. Problem-solving - You break down complex problems and find effective solutions
        6. Memory - You remember context from conversations to provide personalized help
        7. Clarity - You communicate clearly and ensure users understand your responses
       
         <tips>
        1. Use filesystem tool to generate files
        2. When user query cannot be answered directly, use search tool to retrieve relevant information
        3. agent_experiences contains steps of your historical task handling, structured as:
           - skill: skill name
           - actions: list of specific actions executed
        4. user_profiles contains user characteristics, structured as:
           - key: user attributes (like needs, preferences, behaviors etc.)
           - value: corresponding attribute value
        5. history contains interaction history between user and agent
        6. cur_time is current timestamp
        7. similar_messages_history contains historical conversations similar to current user input
        8. knowledge_base contains relevant information retrieved from knowledge base based on current user input
        </tips>

        <agent_experiences>
        {agent_experiences}
        </agent_experiences>

        <history>
        {history}
        </history>
        
        <cur_time>
        {cur_time}
        </cur_time>
"""

self_evolving_user_input_rewrite_prompt = """

<user_profiles>
{user_profiles}
</user_profiles>

<similar_messages_history>
{similar_messages_history}
</similar_messages_history>

<knowledge_base>
</knowledge_base>

{user_input}
"""


class SuperAgent:
    """
    Super agent
    """

    def __init__(self, id: str, name: str, **kwargs):
        self.memory_config = MemoryConfig(
            provider="inmemory",
            enable_long_term=True,
            long_term_config=LongTermConfig.create_simple_config(
                enable_user_profiles=True,
                enable_agent_experiences=False,
                message_threshold=6
            )
        )
        self.memory = MemoryFactory.instance()

        agent_config = AgentConfig(
            llm_provider="openai",
            llm_model_name=os.environ["LLM_MODEL_NAME"],
            llm_api_key=os.environ["LLM_API_KEY"],
            llm_base_url=os.environ["LLM_BASE_URL"]
        )
        self.sub_agent = SelfEvolvingAgent(
            conf=agent_config,
            name="self_evolving_agent",
            system_prompt=self_evolving_agent_prompt,
            mcp_servers=["aworldsearch-server", "filesystem"],
            history_messages=100,
            mcp_config=load_mcp_config(),
            memory_config=MemoryConfig(
                provider="inmemory",
                enable_long_term=True,
                long_term_config=LongTermConfig.create_simple_config(
                    enable_user_profiles=False,
                    enable_agent_experiences=True
                )
            )
        )
        self.id = id
        self.name = name

    async def async_run(self, user_id, session_id, task_id, user_input):
        """
        Run task
        """
        task_context = await self.get_history_context(user_id, session_id, task_id, user_input)

        await self.add_human_input(user_id, session_id, task_id, user_input)

        result = await self.run_task(user_id, session_id, task_id, user_input, task_context)

        await self.add_ai_message(user_id, session_id, task_id, result)

        await self.post_run(user_id, session_id, task_id, task_context)

    async def add_ai_message(self, user_id, session_id, task_id, result):
        self.memory.add(MemoryAIMessage(
            content=result,
            metadata=MessageMetadata(
                user_id=user_id,
                session_id=session_id,
                task_id=task_id,
                agent_id=self.id,
                agent_name=self.name
            )
        ), memory_config=self.memory_config)

    async def add_human_input(self, user_id, session_id, task_id, user_input):
        self.memory.add(MemoryHumanMessage(
            content=user_input,
            metadata=MessageMetadata(
                user_id=user_id,
                session_id=session_id,
                task_id=task_id,
                agent_id=self.id,
                agent_name=self.name
            )
        ), memory_config=self.memory_config)

    async def run_task(self, user_id, session_id, task_id, user_input, task_context):
        user_input = await self.rewrite_user_input(user_id, user_input, task_context)
        task = Task(
            id=task_id,
            session_id=session_id,
            user_id=user_id,
            input=user_input,
            agent=self.sub_agent,
            conf=TaskConfig(),
            context=task_context
        )
        logging.info(f"[SuperAgent] run task start, task_id = {task.id} input = {input}")
        rich_ui = PrinterAworldUI()
        result = ""
        async for output in Runners.streamed_run_task(task).stream_events():
            res = await AworldUI.parse_output(output, rich_ui)
            result += res
        logging.info(f"[SuperAgent] run task finished, task_id = {task.id} result = {result}")
        return result

    async def rewrite_user_input(self, user_id, user_input, task_context):
        """
        Rewrite user input
        """
        user_profiles = await self.retrival_user_profile(user_id, user_input)
        logging.info(f"[SuperAgent] rewrite_user_input user_profiles = {user_profiles}")
        similar_messages_history = await self.retrival_similar_messages_history(user_id, user_input)
        logging.info(f"[SuperAgent] rewrite_user_input similar_messages_history = {similar_messages_history}")
        return self_evolving_user_input_rewrite_prompt.format(user_input=user_input, user_profiles=user_profiles,
                                                              similar_messages_history=similar_messages_history)

    async def get_history_context(self, user_id, session_id, task_id, user_input):
        # get cur session history
        history_messages = self.memory.get_last_n(10, filters={
            "user_id": user_id,
            "session_id": session_id,
            "agent_id": self.id
        })
        task_context = Context()
        task_context.context_info["history"] = build_history_context(history_messages)

        # get cur user profile
        user_profiles = await self.retrival_user_profile(user_id, user_input)
        task_context.context_info["user_profiles"] = user_profiles

        # get similar messages_history
        similar_messages_history = await self.retrival_similar_messages_history(user_id, user_input)
        task_context.context_info["similar_messages_history"] = similar_messages_history

        return task_context

    async def post_run(self, user_id, session_id, task_id, task_context):
        """
        Post run
        """
        logging.info(f"[SuperAgent] post_run user_id = {user_id}, session_id = {session_id}, task_id = {task_id}")
        await self.extract_user_profile(user_id, session_id, task_id)
        await self.sub_agent.evolving(user_id, session_id, task_id)

    async def extract_user_profile(self, user_id, session_id, task_id):
        await self.memory.trigger_short_term_memory_to_long_term(LongTermMemoryTriggerParams(
            agent_id=self.id,
            session_id=session_id,
            task_id=task_id,
            user_id=user_id,
            force=True
        ), self.memory_config)

    async def gen_long_term_memory(self, user_id, session_id, task_id):
        """
        Gen long-term memory
        """
        await self.memory.trigger_short_term_memory_to_long_term(LongTermMemoryTriggerParams(
            agent_id=self.id,
            session_id=session_id,
            task_id=task_id,
            user_id=user_id
        ), self.memory_config)

    async def retrival_user_profile(self, user_id, user_input) -> Optional[list[UserProfile]]:
        """
        Retrieve similar user profiles from long-term storage for context.
        """
        return await self.memory.retrival_user_profile(user_id, user_input)

    async def retrival_similar_messages_history(self, user_id, user_input) -> Optional[List[MemoryItem]]:
        """
        Retrieve similar messages history from long-term storage for context.
        """
        return await self.memory.retrival_similar_user_messages_history(user_id, user_input)


class SelfEvolvingAgent(Agent):
    """
    Self-evolving agent
    """

    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {}, message: Message = None,
                           **kwargs) -> List[ActionModel]:
        return await super().async_policy(observation, info, message, **kwargs)

    async def async_post_run(self, policy_result: List[ActionModel], policy_input: Observation) -> Message:
        """
        custom it
        """
        return await super().async_post_run(policy_result, policy_input)

    async def evolving(self, user_id, session_id, task_id):
        """
        Evolving agent experience
        """
        logging.info(
            f"[SelfEvolvingAgent] evolving_agent_experience user_id = {user_id}, session_id = {session_id}, task_id = {task_id}")
        await self.memory.trigger_short_term_memory_to_long_term(LongTermMemoryTriggerParams(
            agent_id=self.id(),
            session_id=session_id,
            task_id=task_id,
            user_id=user_id,
            force=True
        ), self.memory_config)

    async def custom_system_prompt(self, context: Context):
        """
        custom it
        """
        agent_experiences = await self.memory.retrival_agent_experience(self.id(), context.get_task().input)
        logging.info(f"[SelfEvolvingAgent] custom_system_prompt agent_experiences = {agent_experiences}")
        return self.system_prompt.format(history=context.context_info.get("history", ""),
                                         agent_experiences=agent_experiences,
                                         cur_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
