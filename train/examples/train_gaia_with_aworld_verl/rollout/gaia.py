# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import os
import uuid
from datetime import datetime

from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig, ConfigDict, TaskConfig, SummaryPromptConfig, AgentMemoryConfig
from aworld.core.agent.swarm import Swarm
from aworld.core.context.amni import TaskInput, ApplicationContext
from aworld.core.context.amni.config import get_default_config, init_middlewares, AgentContextConfig
from aworld.core.task import Task
from aworld.dataset.trajectory_strategy import MemoryTrajectoryStrategy
from aworld.logs.util import logger
# from train.adapter.verl.aworld_agent_loop import AworldAgentLoop
from aworld.memory.main import AWORLD_MEMORY_EXTRACT_NEW_SUMMARY
# Import from prompts module directly to avoid circular import
# (rollout/__init__.py imports this file at the top)
from train.examples.train_gaia_with_aworld_verl.rollout.prompts import (
    GAIA_SYSTEM_PROMPT,
    episode_memory_summary_rule,
    working_memory_summary_rule,
    working_memory_summary_schema,
    tool_memory_summary_rule,
    tool_memory_summary_schema,
    episode_memory_summary_schema,
)

def is_summary():
    return os.getenv("GAIA_AGENT_CONTEXT", 'common') == 'amni'

def build_gaia_agent(llm_model_name, llm_base_url, llm_api_key, mcp_config, server_manager = None, tokenizer = None):

    # init middlewares
    init_middlewares()

    # 1. config agent context
    conf=AgentConfig(
        llm_config=ConfigDict(
            llm_model_name=llm_model_name,
            llm_base_url=llm_base_url,
            llm_api_key=llm_api_key,
            llm_provider="openai",
            llm_temperature=1.0,
            top_k=20,
            timeout=7200,
            params={
                "client": server_manager,
                "tokenizer": tokenizer,
                "request_id": uuid.uuid4().hex,
                "tool_parser": "hermes"
            }
        ),
        memory_config=AgentMemoryConfig()
    )

    # 2. init agent
    return Agent(
        conf=conf,
        name="gaia_super_agent",
        system_prompt=GAIA_SYSTEM_PROMPT,
        # MCP tool configuration for the agent
        mcp_config=mcp_config,
        mcp_servers=list(server_name for server_name in mcp_config.get("mcpServers", {}).keys()),
        direct_memory_call=True
    )



async def build_gaia_task(user_input: str, target: [Agent, Swarm], timeout, session_id: str = None, task_id: str = None):
    # 1. init middlewares
    init_middlewares()

    # 2. build context config
    context_config = get_default_config()
    context_config.agent_config = AgentContextConfig()
    if is_summary():
        context_config.agent_config = AgentContextConfig(
            history_rounds= 100,
            enable_summary= True,
            summary_rounds= 30,
            summary_context_length= 40960,
            summary_role= "assistant",
            summary_prompts=[
                SummaryPromptConfig(template=AWORLD_MEMORY_EXTRACT_NEW_SUMMARY,
                                    summary_rule=episode_memory_summary_rule,
                                    summary_schema=episode_memory_summary_schema),
                SummaryPromptConfig(template=AWORLD_MEMORY_EXTRACT_NEW_SUMMARY,
                                    summary_rule=working_memory_summary_rule,
                                    summary_schema=working_memory_summary_schema),
                SummaryPromptConfig(template=AWORLD_MEMORY_EXTRACT_NEW_SUMMARY,
                                    summary_rule=tool_memory_summary_rule,
                                    summary_schema=tool_memory_summary_schema)
            ],
        )

    # 3. build context
    if not session_id:
        session_id = f"session_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    if not task_id:
        task_id = f"task_{datetime.now().strftime('%Y%m%d%H%M%S')}"

    task_input = TaskInput(
        user_id=f"user",
        session_id=session_id,
        task_id=task_id,
        task_content=user_input,
        origin_user_input=user_input
    )

    context = await ApplicationContext.from_input(task_input=task_input, context_config=context_config)


    # 4. build swarm
    if isinstance(target, Swarm):
        swarm = target
        return Task(
            id=context.task_id,
            user_id=context.user_id,
            session_id=context.session_id,
            input=context.task_input,
            endless_threshold=5,
            swarm=swarm,
            context=context,
            conf=TaskConfig(
                stream=False,
                exit_on_failure=True,
                trajectory_strategy=MemoryTrajectoryStrategy
            ),
            timeout=timeout
        )
    else:
        target.task = user_input
        return Task(
            id=context.task_id,
            user_id=context.user_id,
            session_id=context.session_id,
            input=context.task_input,
            endless_threshold=5,
            agent=target,
            context=context,
            conf=TaskConfig(
                stream=False,
                exit_on_failure=True,
                trajectory_strategy=MemoryTrajectoryStrategy
            ),
            timeout=timeout
        )

    # await context.build_agents_state(swarm.topology)
