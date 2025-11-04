# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio

from aworld.core.agent.swarm import Swarm
from aworld.core.event.base import Message, TopicType
from aworld.core.task import Task
from aworld.runner import Runners
from examples.multi_agents.workflow.search.common import *

# os.environ["LLM_MODEL_NAME"] = "YOUR_LLM_MODEL_NAME"
# os.environ["LLM_BASE_URL"] = "YOUR_LLM_BASE_URL"
# os.environ["LLM_API_KEY"] = "YOUR_LLM_API_KEY"
# search and summary
async def run_task_stream():
    swarm = Swarm(search, summary, max_steps=1)
    # swarm = WorkflowSwarm(search, summary, max_steps=1)

    prefix = ""

    task = Task(input=prefix + """What is an agent.""", swarm=swarm)
    idx = 0
    with open("stream_core.txt", "w") as f:
        async for res in Runners.streaming_run_task(task, streaming_mode="core"):
            f.write(f"idx {idx}|{res.category}: {res.debug_repr()}\n")
            idx += 1
            if isinstance(res, Message) and res.topic == TopicType.TASK_RESPONSE:
                print(f"Task Response answer: {res.payload.answer}")

if __name__ == "__main__":
    # need to set GOOGLE_API_KEY and GOOGLE_ENGINE_ID to use Google search.
    # os.environ['GOOGLE_API_KEY'] = ""
    # os.environ['GOOGLE_ENGINE_ID'] = ""

    asyncio.run(run_task_stream())

    print("=========== TaskDone ===========")
