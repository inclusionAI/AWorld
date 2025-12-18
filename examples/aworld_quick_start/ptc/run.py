import asyncio
import traceback
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

from aworld.core.context.amni import TaskInput, ApplicationContext
from aworld.core.context.amni.config import AmniConfigFactory, AmniConfigLevel
from aworld.core.task import Task
from aworld.runner import Runners

import os

from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig, ModelConfig

MCP_CONFIG = {
    "mcpServers": {
        "ms-playwright": {
            "command": "npx",
            "args": [
                "@playwright/mcp@0.0.37",
                "--no-sandbox",
                "--isolated",
                "--output-dir=/tmp/playwright",
                "--timeout-action=10000",
            ],
            "env": {
                "PLAYWRIGHT_TIMEOUT": "120000",
                "SESSION_REQUEST_CONNECT_TIMEOUT": "120"
            }
        }
    }
}


def build_agent():
    return


async def build_task(task_content: str, context_config, session_id: str = None, task_id: str = None) -> Task:
    if not session_id:
        session_id = f"session_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    if not task_id:
        task_id = f"task_{datetime.now().strftime('%Y%m%d%H%M%S')}"

    # 1. build task input
    task_input = TaskInput(
        user_id=f"user",
        session_id=session_id,
        task_id=task_id,
        task_content=task_content,
        origin_user_input=task_content
    )

    # 2. build agent
    agent = Agent(
        name="orchestrator_agent",
        desc="orchestrator_agent",
        system_prompt="You are a versatile AI assistant designed to solve any task presented by users.",
        conf=AgentConfig(
            llm_config=ModelConfig(
                llm_temperature=0.,
                llm_model_name=os.environ.get("LLM_MODEL_NAME"),
                llm_provider=os.environ.get("LLM_PROVIDER"),
                llm_api_key=os.environ.get("LLM_API_KEY"),
                llm_base_url=os.environ.get("LLM_BASE_URL")
            )
        ),
        mcp_config=MCP_CONFIG,
        mcp_servers=["ms-playwright"],
        ptc_tools=["browser_evaluate", "browser_navigate", "browser_snapshot", "browser_wait_for", "browser_type"]  #
    )

    # 3. build context
    async def build_context(_task_input: TaskInput) -> ApplicationContext:
        """Important Config"""
        _context = await ApplicationContext.from_input(_task_input, context_config=context_config)
        await _context.build_agents_state([agent])
        return _context

    context = await build_context(task_input)

    # 3. build task with context
    return Task(
        id=context.task_id,
        user_id=context.user_id,
        session_id=context.session_id,
        input=context.task_input,
        endless_threshold=5,
        agent=agent,
        context=context
    )


async def run(user_input: str):
    # 1. init middlewares
    load_dotenv()

    # 2. build context config
    context_config = AmniConfigFactory.create(
        AmniConfigLevel.PILOT,
        debug_mode=True
    )

    # 3. build task
    task = await build_task(user_input, context_config)

    # 4. run task
    try:
        result = await Runners.run_task(task=task)
        print(result[task.id].answer)

    except Exception as err:
        print(f"err is {err}, trace is {traceback.format_exc()}")


if __name__ == '__main__':
    asyncio.run(run(user_input="I want to know today the weather in beijing,nangjin,hangzhou,guangzhou,shenzhen,wulumuqi; use https://www.weather.com.cn/, use ptc"))
