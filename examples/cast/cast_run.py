"""
Skill Agent example running the PPTX skill.
"""
import asyncio
import os
from datetime import datetime

from aworld.agents.llm_agent import Agent
from aworld.agents.image_agent import ImageAgent
from aworld.config import AgentConfig, ModelConfig
from aworld.core.agent.swarm import Swarm, TeamSwarm
from aworld.core.context.amni import ApplicationContext
from aworld.core.context.amni import TaskInput, AmniConfigFactory
from aworld.core.context.amni.config import AmniConfigLevel
from aworld.core.task import Task
from aworld.experimental.cast.tools import CAST_ANALYSIS, CAST_CODER
from aworld.experimental.cast.tools.cast_search_tool import CAST_SEARCH
from aworld.runner import Runners
from aworld_cli.core.agent_registry_tool import AGENT_REGISTRY


def build_skill_run_agent():
    agent_config = AgentConfig(
        llm_config=ModelConfig(
            llm_model_name=os.environ.get("LLM_MODEL_NAME", "gpt-4"),
            llm_provider=os.environ.get("LLM_PROVIDER", "openai"),
            llm_api_key=os.environ.get("LLM_API_KEY"),
            llm_base_url=os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1"),
            llm_temperature=0.7,
            params={"max_completion_tokens": 40960}
        ),
        # meta_learning_config=MetaLearningConfig(enabled=True,
        #                                         learning_knowledge_storage_base_path="~/.aworld/meta_learning")
    )

    skill_runner = Agent(
        name="cast_agent",
        desc="cast_agent",
        conf=agent_config,
        system_prompt="you are a coding agent",
        tool_names=[AGENT_REGISTRY, CAST_SEARCH, CAST_ANALYSIS, CAST_CODER],
    )

    return TeamSwarm(skill_runner)


def build_image_agent():
    """Build the image generation agent."""
    image_agent_config = AgentConfig(
        llm_config=ModelConfig(
            llm_model_name="Qwen-Image-2512-Lightning",
            llm_provider="image",
            llm_api_key="key",
            llm_base_url="url",
            llm_temperature=0.7,
        ),
    )

    image_agent = ImageAgent(
        name="image_agent",
        desc="An agent that generates images from text prompts using Qwen Image API",
        conf=image_agent_config,
        default_size="1024x1024",
        default_output_format="png",
        output_dir="./generated_images",
        auto_filename=True,
    )

    return image_agent


async def build_context(task_input: TaskInput) -> ApplicationContext:
    context_config = AmniConfigFactory.create(AmniConfigLevel.PILOT)
    context_config.debug_mode = True
    return await ApplicationContext.from_input(task_input, context_config=context_config)


async def main():
    session_id = f"session_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    task_id = "task_1"
    
    # Task to test image generation functionality
    task_content = """Generate an image of a cute cat playing with a ball"""

    task_input = TaskInput(
        user_id=f"test_user",
        session_id=session_id,
        task_id=f"task_{datetime.now().strftime('%Y%m%d%H%M%S')}" if not task_id else task_id,
        task_content=task_content,
        origin_user_input=task_content
    )

    context = await build_context(task_input)

    # Build the swarm with both cast_agent and image_agent
    cast_swarm = build_skill_run_agent()
    image_agent = build_image_agent()
    
    # Create a team swarm with both agents
    # The cast_agent can delegate to image_agent for image generation tasks
    swarm = TeamSwarm(cast_swarm, image_agent)

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
