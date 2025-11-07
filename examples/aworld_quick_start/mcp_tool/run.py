# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import time
from threading import Thread

from aworld.agents.llm_agent import Agent
from aworld.runner import Runners
from examples.aworld_quick_start.common import agent_config


def main():
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

    # Run agent
    res = Runners.sync_run(input="30,000 divided by 1.2 ", agent=search)
    print(res.answer)

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

if __name__ == '__main__':
    is_open = is_open(8500)
    if not is_open:
        from examples.aworld_quick_start.mcp_tool.mcp_server import main as mcp_main
        thread = Thread(target=mcp_main)
        thread.daemon = True
        thread.start()
        time.sleep(1)

    main()
