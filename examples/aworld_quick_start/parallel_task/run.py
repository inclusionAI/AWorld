import asyncio
import os
from aworld.agents.llm_agent import Agent
from aworld.core.task import Task
from aworld.runner import Runners
from aworld.config import RunConfig, EngineName
from examples.aworld_quick_start.common import agent_config


# TODO: need fix
async def main():
    # Create agent
    my_agent = Agent(name="my_agent", conf=agent_config)

    # Create tasks
    tasks = [
        Task(input="What is machine learning?", agent=my_agent, id="task1"),
        Task(input="Explain neural networks", agent=my_agent, id="task2"),
        Task(input="What is deep learning?", agent=my_agent, id="task3")
    ]

    # Run in parallel (default local run).
    # If you want to run in a distributed environment, you need to submit a job to the Ray cluster.
    results = await Runners.run_task(
        task=tasks,
        run_conf=RunConfig(
            engine_name=EngineName.RAY,
            worker_num=len(tasks)
        )
    )

    # Process results
    for task_id, result in results.items():
        print(f"Task {task_id}: {result.answer}")


if __name__ == "__main__":
    asyncio.run(main())
