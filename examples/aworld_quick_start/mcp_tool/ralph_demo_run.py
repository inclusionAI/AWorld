# coding: utf-8
# Copyright (c) inclusionAI.
import asyncio
import time
from threading import Thread

from aworld.agents.llm_agent import Agent
from aworld.core.task import Task
from aworld.ralph_loop.config import RalphConfig
from aworld.ralph_loop.types import CompletionCriteria
from aworld.runner import Runners
from examples.aworld_quick_start.common import agent_config


async def main():
    def is_open(port: int):
        import socket

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.connect(("localhost", port))
            s.shutdown(2)
            print(f'{port} is open')
            return True
        except:
            print(f'{port} is down')
            return False

    is_open = is_open(8500)
    if not is_open:
        from examples.aworld_quick_start.mcp_tool.mcp_server import main as mcp_main

        thread = Thread(target=mcp_main)
        thread.daemon = True
        thread.start()
        time.sleep(1)
    search = Agent(
        conf=agent_config,
        name="search_agent",
        system_prompt="You must use simple-calculator tools to calculate numbers and answer questions",
        mcp_servers=["simple-calculator"],
        mcp_config={
            "mcpServers": {
                "simple-calculator": {
                    "type": "sse",
                    "url": "http://127.0.0.1:8500/calculator/sse",
                    "timeout": 5,
                    "sse_read_timeout": 300
                }
            }
        }
    )

    # Run
    question = "30,000 divided by 1.2 "
    task = Task(input=question, agent=search, conf=RalphConfig.create(model_config=agent_config.llm_config))
    completion_criteria = CompletionCriteria(answer="25000")
    res = await Runners.ralph_run(task=task, completion_criteria=completion_criteria)
    print(res.answer)


if __name__ == '__main__':
    asyncio.run(main())
