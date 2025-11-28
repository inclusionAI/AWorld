import asyncio
import os
import traceback
from datetime import datetime

from dotenv import load_dotenv

from aworld.core.agent.swarm import Swarm

load_dotenv()

from aworld.config import TaskConfig
from aworld.core.context.amni import TaskInput, ApplicationContext
from aworld.core.context.amni.config import AmniConfigFactory, AmniConfigLevel, init_middlewares, AgentContextConfig, \
    CONTEXT_OFFLOAD_TOOL_NAME_WHITE
from aworld.core.task import Task
from aworld.runner import Runners
from examples.skill_agent.agents.swarm import build_swarm


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

    # 2. build swarm
    swarm = build_swarm()


    # 3. build context
    async def build_context(_task_input: TaskInput, _swarm: Swarm) -> ApplicationContext:
        """Important Config"""
        _context = await ApplicationContext.from_input(_task_input, context_config=context_config)
        await _context.init_swarm_state(_swarm)
        return _context

    context = await build_context(task_input, swarm)

    # 3. build task with context
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
            exit_on_failure=True
        ),
        timeout=60 * 60
    )

async def run(user_input: str):
    # 1. init middlewares
    load_dotenv()
    init_middlewares()

    # 2. build context config
    context_config = AmniConfigFactory.create(
        AmniConfigLevel.NAVIGATOR,
        debug_mode=True
    )
    context_config.agent_config = AgentContextConfig(
        enable_system_prompt_augment=True,
        neuron_names=["task", "working_dir", "todo", "action_info", "skills", "basic"],
        history_rounds=100,
        enable_summary=True,
        summary_rounds=30,
        summary_summaried=False,
        summary_context_length=128000,
        tool_result_offload=True,
        tool_action_white_list=CONTEXT_OFFLOAD_TOOL_NAME_WHITE,
        tool_result_length_threshold=30000
    )

    # 3. build task
    task = await build_task(user_input, context_config)

    # 4. run task
    try:
        result = await Runners.run_task(task=task)
        print(result[task.id].answer)
        if not os.path.exists("results"):
            os.makedirs("results")
        with open(f"results/{task.id}.txt", "w") as f:
            f.write(result[task.id].answer)
    except Exception as err:
        print(f"err is {err}, trace is {traceback.format_exc()}")


if __name__ == '__main__':
    asyncio.run(run(user_input="Help me find the latest week stock price of BABA. And Analysis the trend of news."))
