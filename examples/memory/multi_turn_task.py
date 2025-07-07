# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio

from dotenv import load_dotenv

from aworld.memory.main import MemoryFactory
from examples.memory.agent.self_evolving_agent import SuperAgent

async def _run_single_session_examples() -> None:
    """
    Run examples within a single session.
    Demonstrates a complete learning session about reinforcement learning concepts.
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
                                user_input="Summarize the above content and generate a complete report in Markdown format")

if __name__ == '__main__':
    load_dotenv()

    MemoryFactory.init()

    # Run the multi-session example with concrete learning tasks
    asyncio.run(_run_single_session_examples())

