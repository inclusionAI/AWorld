# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio

from dotenv import load_dotenv

from aworld.memory.main import MemoryFactory
from examples.advance.self_evolving_agent import SuperAgent


async def _run_one_agent_one_hot() -> None:
    """
    Run a single agent in one-hot mode.
    Currently a placeholder function.
    """
    pass

async def _run_single_task_examples() -> None:
    """
    Run examples with a single task.
    Demonstrates basic agent interaction with outdoor sports topic.
    """
    super_agent = SuperAgent(id="super_agent", name="super_agent")
    user_id = "zues"
    session_id = "session#foo"
    await super_agent.async_run(user_id=user_id, session_id=session_id, task_id="zues:session#foo:task#1", user_input="I really enjoy outdoor sports")


async def _run_single_session_examples() -> None:
    """
    Run examples within a single session.
    Demonstrates a complete learning session about reinforcement learning concepts.
    """
    # await init_dataset()

    super_agent = SuperAgent(id="super_agent", name="super_agent")
    user_id = "zues"
    session_id = "session#foo"
    await super_agent.async_run(user_id=user_id, session_id=session_id, task_id="zues:session#foo:task#1", user_input="Introduce the basic concepts of reinforcement learning")
    await super_agent.async_run(user_id=user_id, session_id=session_id, task_id="zues:session#foo:task#2", user_input="Explain the differences between online and offline learning in reinforcement learning")
    await super_agent.async_run(user_id=user_id, session_id=session_id, task_id="zues:session#foo:task#3", user_input="Describe the application domains of reinforcement learning")
    await super_agent.async_run(user_id=user_id, session_id=session_id, task_id="zues:session#foo:task#4", user_input="Summarize the above content and generate a complete report in Markdown format")

async def _run_multi_session_examples() -> None:
    """
    Run examples across multiple sessions.
    Demonstrates handling multiple learning sessions with different topics in reinforcement learning.
    """
    # await init_dataset()

    super_agent = SuperAgent(id="super_agent", name="super_agent")
    user_id = "zues"
    session_id = "session#foo"
    await super_agent.async_run(user_id=user_id, session_id=session_id, task_id="zues:session#foo:task#1",
                            user_input="Introduce the basic concepts of reinforcement learning")
    await super_agent.async_run(user_id=user_id, session_id=session_id, task_id="zues:session#foo:task#2",
                            user_input="Explain the differences between online and offline learning in reinforcement learning")
    await super_agent.async_run(user_id=user_id, session_id=session_id, task_id="zues:session#foo:task#3",
                            user_input="Describe the application domains of reinforcement learning")
    await super_agent.async_run(user_id=user_id, session_id=session_id, task_id="zues:session#foo:task#4",
                            user_input="Summarize the above content and generate a report in Markdown format")

    session_id = "session#bar"
    await super_agent.async_run(user_id=user_id, session_id=session_id, task_id="zues:session#bar:task#1",
                            user_input="Explain the differences between reinforcement learning and deep learning")

if __name__ == '__main__':
    load_dotenv()

    MemoryFactory.init()

    asyncio.run(_run_single_session_examples())

