import asyncio
from aworld.agents.llm_agent import Agent
from aworld.core.task import Task
from aworld.config import RunConfig, EngineName, HistoryWriteStrategy, AgentMemoryConfig
from aworld.utils.run_util import exec_tasks
from examples.aworld_quick_start.common import agent_config


async def main():
    # NOTE: need set direct write to memory
    agent_config.memory_config = AgentMemoryConfig(
        history_write_strategy=HistoryWriteStrategy.DIRECT
    )
    # Create agent
    my_agent = Agent(name="my_agent", conf=agent_config)
    print("origin agent id: ", my_agent.id())
    # copy an agent with different agent id
    new_agent = Agent.from_dict(await Agent.to_dict(my_agent))
    print("new agent id: ", new_agent.id())

    # Create tasks
    tasks = [
        Task(input="What is machine learning?", agent=my_agent, id="task1"),
        Task(input="Explain neural networks", agent=my_agent, id="task2"),
        Task(input="What is deep learning?", agent=my_agent, id="task3")
    ]

    # Run in parallel (default local run).
    # If you want to run in a distributed environment, you need to submit a job to the Ray cluster.
    results = await exec_tasks(
        tasks=tasks,
        run_conf=RunConfig(
            engine_name=EngineName.LOCAL,
            worker_num=len(tasks),
            reuse_process=False
        )
    )

    # Process results
    for task_id, result in results.items():
        print(f"Task {task_id}: {result.answer}")


if __name__ == "__main__":
    asyncio.run(main())
