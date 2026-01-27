# coding: utf-8
from datetime import datetime

from aworld.core.agent.swarm import Swarm
from aworld.core.context.amni import ApplicationContext, TaskInput
from aworld.core.task import Task
from aworld.logs.util import logger
from aworld.runner import Runners

"""
Skill-enabled Agent example for aworld-cli.

This demonstrates how to create an agent with multiple integrated skills and MCP tools,
enabling it to handle complex real-world tasks including document processing, web browsing,
task planning, and knowledge management.
"""

import os

from aworld.config import AgentConfig, ModelConfig
from aworld.utils.skill_loader import collect_skill_docs

from aworld.core.context.amni import AmniConfigFactory
from aworld.core.context.amni.config import AmniConfigLevel
from examples.aworld_quick_start.cli.agents.team_runner_agent import TeamRunnerAgent


async def build_context(task_input: TaskInput) -> ApplicationContext:
    context_config = AmniConfigFactory.create(AmniConfigLevel.PILOT)
    context_config.debug_mode = True

    return await ApplicationContext.from_input(task_input, context_config=context_config)


async def main():
    session_id = f"session_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    task_id = "task_1"
    task_content = """```json
{
    "team_name": "ppt_team",
    "task_input": "帮我生成一个ppt，介绍贝克汉姆的生平"
}
```"""

    os.environ['SKILLS_PATH']='/Users/hgc/hgc_repo/AWorld/examples/aworld_quick_start/cli/skills'
    os.environ['AGENT_REGISTRY_STORAGE_PATH'] = '/Users/hgc/hgc_repo/AWorld/examples/aworld_quick_start/cli/skills'

    # task_content = "帮我生成一个贝克汉姆介绍ppt"
    task_input = TaskInput(
        user_id=f"test_user",
        session_id=session_id,
        task_id=f"task_{datetime.now().strftime('%Y%m%d%H%M%S')}" if not task_id else task_id,
        task_content=task_content,
        origin_user_input=task_content
    )
    context = await build_context(task_input)
    SKILLS_DIR = "/Users/hgc/hgc_repo/AWorld/examples/aworld_quick_start/cli/skills"

    CUSTOM_SKILLS = collect_skill_docs(SKILLS_DIR)

    meta_agent_config = AgentConfig(
        llm_config=ModelConfig(
            llm_temperature=0.,
            llm_model_name=os.environ.get("LLM_MODEL_NAME"),
            llm_provider=os.environ.get("LLM_PROVIDER"),
            llm_api_key=os.environ.get("LLM_API_KEY"),
            llm_base_url=os.environ.get("LLM_BASE_URL"),
            params={"max_completion_tokens": 40960}
        ),
        use_vision=False,
        skill_configs=CUSTOM_SKILLS
    )
    team_runner_agent = TeamRunnerAgent(
        name="TeamRunner",
        desc="Meta-level orchestrator that analyzes user requirements, dynamically generates agent teams from registry templates, and coordinates multi-agent workflows to accomplish complex tasks. Accepts parameters: {'team_name': str (team name with 'Team' suffix, e.g., 'pptTeam'), 'task_input': str (user's original task description)}. Pass parameters as top-level arguments, NOT wrapped in a 'content' field.",
        conf=meta_agent_config,
        system_prompt="11111",
        mcp_servers=[],
        mcp_config={}
    )
    swarm = Swarm(team_runner_agent, max_steps=2)
    task = Task(
        input=task_content,
        swarm=swarm,
        context=context,
    )
    result = await Runners.run_task(task)
    logger.info(f'hahaha result1: {result}')


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
