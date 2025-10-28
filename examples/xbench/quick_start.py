import asyncio
import traceback
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

from aworld.config import TaskConfig
from aworld.core.context.amni import TaskInput, ApplicationContext
from aworld.core.context.amni.config import AmniConfigFactory, AmniConfigLevel, init_middlewares
from aworld.core.task import Task
from aworld.runner import Runners
from examples.xbench.agents.swarm import build_xbench_swarm as build_swarm


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

    # 2. build context
    async def build_context(_task_input: TaskInput) -> ApplicationContext:
        """Important Config"""
        return await ApplicationContext.from_input(_task_input, context_config=context_config)

    context = await build_context(task_input)

    # build swarm
    swarm = build_swarm()

    await context.build_agents_state(swarm.topology)

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
    context_config = AmniConfigFactory.create(AmniConfigLevel.NAVIGATOR)

    # 3. build task
    task = await build_task(user_input, context_config)

    # 4. run task
    try:
        result = await Runners.run_task(task=task)
        print(result)
    except Exception as err:
        print(f"err is {err}, trace is {traceback.format_exc()}")

if __name__ == '__main__':
    asyncio.run(run(user_input="如果你是蚂蚁AQ的CEO，你面临一个业务转型压力，综合当前AQ的业务特点和医疗领域潜在方向，在tempus,hims,oscar三家公司的业务方向上进行选择，你会选择哪一条赛道"))
