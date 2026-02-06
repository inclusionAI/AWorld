"""
Skill Agent example running the PPTX skill.
"""
import asyncio
import os
from datetime import datetime

from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig, ModelConfig
from aworld.core.agent.swarm import Swarm
from aworld.core.context.amni import TaskInput, ApplicationContext, AmniConfigFactory
from aworld.core.context.amni.config import AmniConfigLevel
from aworld.core.task import Task
from aworld.experimental.cast.tools import CAST_ANALYSIS, CAST_CODER
from aworld.experimental.cast.tools.cast_search_tool import CAST_SEARCH
from aworld.runner import Runners
from aworld_cli.core import agent

# Path to the PPTX skill definition

os.environ['AGENTS_PATH'] = "/Users/hgc/hgc_repo/AWorld/examples/aworld_quick_start/cli/skills"


@agent(
    name="CastAgent",
    desc="Agent configured with PPTX skill"
)
def build_cast_run_agent():
    """
    Build an agent configured with the PPTX skill.
    """

    # 2. Configure Agent
    agent_config = AgentConfig(
        llm_config=ModelConfig(
            llm_model_name=os.environ.get("LLM_MODEL_NAME", "gpt-4"),
            llm_provider=os.environ.get("LLM_PROVIDER", "openai"),
            llm_api_key=os.environ.get("LLM_API_KEY"),
            llm_base_url=os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1"),
            llm_temperature=0.7,
            params={"max_completion_tokens": 40960},
            ext_config={
                "max_tokens": 16384
            }
        ),
    )

    skill_runner = Agent(
        name="pptx_agent",
        desc="pptx_agent",
        conf=agent_config,
        system_prompt="",
        # mcp_servers=pptx_skill.get("mcp_servers"),
        # mcp_config=mcp_config,
        tool_names=[CAST_ANALYSIS, CAST_CODER, CAST_SEARCH]
    )

    return Swarm(skill_runner)

async def build_context(task_input: TaskInput) -> ApplicationContext:
    context_config = AmniConfigFactory.create(AmniConfigLevel.PILOT)
    context_config.debug_mode = True
    return await ApplicationContext.from_input(task_input, context_config=context_config)

async def main():
    session_id = f"session_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    task_id = "task_1"
    task_content = """在/Users/hgc/.aworld/agents/html_generator_agent目录中，检索mcp_config第三个mcp server配置的内容"""

    swarm = build_cast_run_agent()

    task_input = TaskInput(
        user_id=f"test_user",
        session_id=session_id,
        task_id=f"task_{datetime.now().strftime('%Y%m%d%H%M%S')}" if not task_id else task_id,
        task_content=task_content,
        origin_user_input=task_content
    )

    context = await build_context(task_input)

    task1 = Task(
        input=task_content,
        swarm=swarm,
        context=context,
    )

    print("Running task...")
    result = await Runners.run_task(task1)
    print("Result:", result)


if __name__ == "__main__":
    asyncio.run(main())
