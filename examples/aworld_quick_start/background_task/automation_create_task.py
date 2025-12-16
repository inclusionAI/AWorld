import asyncio
import os
import traceback
from datetime import datetime

from dotenv import load_dotenv

from examples.aworld_quick_start.background_task.agents.swarm import build_swarm

load_dotenv()

from aworld.core.context.amni.config import AmniConfigFactory, AmniConfigLevel, init_middlewares
from aworld.core.task import Task
from aworld.runner import Runners


async def build_task(task_content: str, context_config, session_id: str = None, task_id: str = None) -> Task:
    if not session_id:
        session_id = f"session_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    if not task_id:
        task_id = f"task_{datetime.now().strftime('%Y%m%d%H%M%S')}"

    # 2. build swarm
    swarm = build_swarm()

    # 3. build task with context
    return Task(
        user_id=f"user",
        session_id=session_id,
        id=task_id,
        input=task_content,
        swarm=swarm,
        context_config=context_config,
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
    asyncio.run(run(user_input="我今天要干什么,你可以后台做，不用立马给我答案"))
    # 1. orchestrator_agent 根据当前时间 创建这个后台执行任务 这个任务交给 WorkerAgent
    # 2. 到了指定时间之后 触发这个后台任务给WorkerAgent
    # 3. 后台任务执行
