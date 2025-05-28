# Initialize AworldTaskClient with server endpoints
import asyncio
import os
import random
import uuid

from aworld.utils.common import get_local_ip

from client.aworld_client import AworldTask, AworldTaskClient

AWORLD_TASK_CLIENT = AworldTaskClient(
    know_hosts=["localhost:9999"]
)


async def _run_gaia_task(gaia_task: AworldTask) -> None:
    """Run a single Gaia task with the given question ID.

    Args:
        gaia_task_id: The ID of the question to process
    """
    global AWORLD_TASK_CLIENT
    await asyncio.sleep(random.random() * 10)

    # Submit task to Aworld server
    await AWORLD_TASK_CLIENT.submit_task(gaia_task)

    # Get and print task result
    task_result = await AWORLD_TASK_CLIENT.get_task_state(task_id=gaia_task.task_id)
    print(task_result)


async def _batch_run_gaia_task(gaia_tasks: list[AworldTask]) -> None:
    """Run multiple Gaia tasks in parallel.

    """
    tasks = [
        _run_gaia_task(gaia_task)
        for gaia_task in gaia_tasks
    ]
    await asyncio.gather(*tasks)


CUSTOM_SYSTEM_PROMPT = f""" **PLEASE CUSTOM IT **"""

if __name__ == '__main__':
    gaia_task_ids = ["32102e3e-d12a-4209-9163-7b3a104efe5d"]
    gaia_tasks = []
    custom_mcp_servers = [
            "excel"
    ]

    for gaia_task_id in gaia_task_ids:
        task_id = gaia_task_id + "_" + str(uuid.uuid4())
        gaia_tasks.append(
            AworldTask(
                task_id=task_id,
                agent_id="gaia_agent",
                agent_input=gaia_task_id,
                session_id="session_id",
                user_id=os.getenv("USER", "SYSTEM"),
                client_id=get_local_ip(),
                mcp_servers=custom_mcp_servers,
                llm_model_name="DeepSeek-V3-Function-Call",
                # task_system_prompt=CUSTOM_SYSTEM_PROMPT
            )
        )
    # Run batch processing for questions 1-5
    asyncio.run(_batch_run_gaia_task(gaia_tasks))
