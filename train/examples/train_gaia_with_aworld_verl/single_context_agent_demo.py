import asyncio
import os
import traceback
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

from aworld.core.agent.swarm import Swarm

from train.examples.train_gaia_with_aworld_verl.gaia_agent import build_gaia_agent
from aworld.config import TaskConfig
from aworld.core.context.amni import TaskInput, ApplicationContext
from aworld.core.context.amni.config import AmniConfigFactory, AmniConfigLevel, init_middlewares
from aworld.core.task import Task
from aworld.runner import Runners


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
    swarm = Swarm(build_gaia_agent(llm_model_name=os.getenv("LLM_MODEL_NAME"), llm_base_url=os.getenv("LLM_BASE_URL"), llm_api_key=os.getenv("LLM_API_KEY")), max_steps=30)

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
    query = "In July 2, 1959 United States standards for grades of processed fruits, vegetables, and certain other products listed as dehydrated, consider the items in the \"dried and dehydrated section\" specifically marked as dehydrated along with any items in the Frozen/Chilled section that contain the whole name of the item, but not if they're marked Chilled. As of August 2023, what is the percentage (to the nearest percent) of those standards that have been superseded by a new version since the date given in the 1959 standards?"
    asyncio.run(run(user_input=query))
