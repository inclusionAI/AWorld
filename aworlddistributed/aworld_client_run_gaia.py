# Initialize AworldTaskClient with server endpoints
import asyncio
import random
import uuid

from base import AworldTask
from client.aworld_client import AworldTaskClient

AWORLD_TASK_CLIENT = AworldTaskClient(
    know_hosts=["nlb-lm8m8hityawuxbxvof.ap-southeast-1.nlb.aliyuncsslbintl.com:9099"]
)


async def _run_gaia_task(gaia_task_id: str) -> None:
    """Run a single Gaia task with the given question ID.

    Args:
        gaia_task_id: The ID of the question to process
    """
    global AWORLD_TASK_CLIENT
    task_id = gaia_task_id + "_" + str(uuid.uuid4())
    await asyncio.sleep(random.random() * 10)

    # Submit task to Aworld server
    await AWORLD_TASK_CLIENT.submit_task(
        AworldTask(
            task_id=task_id,
            agent_id="gaia_agent",
            agent_input=gaia_task_id,
            session_id="session_id",
            user_id="SYSTEM"
        )
    )

    # Get and print task result
    task_result = await AWORLD_TASK_CLIENT.get_task_state(task_id=task_id)
    print(task_result)


async def _batch_run_gaia_task(gaia_task_ids: list[str]) -> None:
    """Run multiple Gaia tasks in parallel.

    """
    tasks = [
        _run_gaia_task(gaia_task_id)
        for gaia_task_id in gaia_task_ids
    ]
    await asyncio.gather(*tasks)


if __name__ == '__main__':
    # Run batch processing for questions 1-5
    asyncio.run(_batch_run_gaia_task(["32102e3e-d12a-4209-9163-7b3a104efe5d"]))