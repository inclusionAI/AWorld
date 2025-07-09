# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
from asyncio.log import logger
from datetime import datetime

from dotenv import load_dotenv

from examples.memory.agent.self_evolving_agent import SuperAgent
from examples.memory.utils import init_memory


async def _run_single_session_examples() -> None:
    """
    Run examples within a single session.
    Demonstrates a complete learning session about reinforcement learning concepts.
    """
    # await init_dataset()
    salt = datetime.now().strftime("%Y%m%d%H%M%S")
    super_agent = SuperAgent(id="super_agent", name="super_agent")
    user_id = "zues"
    session_id = f"session#foo_{salt}"
    logger.info(f"🚀 Running session {session_id}")
    await super_agent.async_run(user_id=user_id, session_id=session_id, task_id=f"zues:session#foo:task#1_{salt}",
                                user_input="Introduce the basic concepts of reinforcement learning")
    await super_agent.async_run(user_id=user_id, session_id=session_id, task_id=f"zues:session#foo:task#2_{salt}",
                                user_input="Explain the differences between online and offline learning in reinforcement learning")
    await super_agent.async_run(user_id=user_id, session_id=session_id, task_id=f"zues:session#foo:task#3_{salt}",
                                user_input="Describe the application domains of reinforcement learning")
    await super_agent.async_run(user_id=user_id, session_id=session_id, task_id=f"zues:session#foo:task#4_{salt}",
                                user_input="Summarize the above content and generate a complete report in Markdown format, save it to file")
    logger.info(f"✅ Session {session_id} completed")
    

if __name__ == '__main__':
    load_dotenv()

    init_memory()
    # Run the multi-session example with concrete learning tasks
    asyncio.run(_run_single_session_examples())

