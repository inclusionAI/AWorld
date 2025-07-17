# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
from asyncio.log import logger
from datetime import datetime

from dotenv import load_dotenv

from examples.memory.agent.self_evolving_agent import SuperAgent
from examples.memory.utils import init_memory, init_postgres_memory


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
    user_input = ("请针对以下的个智能体记忆系统进行全面的调研与分析，撰写一份约10000字的调查研究报告：\n"
    "1. Mem0\n"
    "2. MemoryBank\n"
    "3. MemoryOS\n"
    "4. MemoryAgent\n"
    "\n"
    "调研内容需包括但不限于：系统简介、核心原理、架构设计、应用场景、优缺点、与其他系统的对比分析、未来发展趋势等。\n"
    "请充分利用 GitHub、arXiv 等权威网站查找相关资料，确保信息的准确性和前沿性。\n"
    "最终请输出一份结构清晰、内容详实的 markdown 格式调研报告。\n")
    await super_agent.async_run(user_id=user_id, session_id=session_id, task_id=f"zues:session#foo:task#1_{salt}",
                                user_input=user_input)
    await super_agent.async_run(user_id=user_id, session_id=session_id, task_id=f"zues:session#foo:task#2_{salt}",
                                user_input="增加一个Aworld Memory模块对比的章节")

    logger.info(f"✅ Session {session_id} completed")
    

if __name__ == '__main__':
    load_dotenv()

    init_postgres_memory()
    # Run the multi-session example with concrete learning tasks
    asyncio.run(_run_single_session_examples())

