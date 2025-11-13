# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import traceback
import asyncio

import aworld.trace as trace
from aworld.trace.config import ObservabilityConfig
from aworld.logs.util import logger
from aworld.core.agent.swarm import Swarm
from aworld.runner import Runners
from aworld.runners.state_manager import RuntimeStateManager
from examples.aworld_quick_start.common import search, summary

# use_trace
trace.configure(ObservabilityConfig(trace_server_enabled=True))


async def main():
    # default is sequence swarm mode
    swarm = Swarm(search, summary, max_steps=1, event_driven=True)

    prefix = "search:"
    # can special search google, wiki, duck go, or baidu. such as:
    # prefix = "search wiki: "
    try:
        res = await Runners.run(
            input=prefix + """What is an agent.""",
            swarm=swarm,
            session_id="123"
        )
        print(res.answer)
    except Exception as e:
        logger.error(traceback.format_exc())

    state_manager = RuntimeStateManager.instance()
    nodes = state_manager.get_nodes("123")
    logger.info(f"session 123 nodes: {nodes}")


if __name__ == "__main__":
    asyncio.run(main())
