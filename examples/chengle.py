import os
import asyncio
from aworld.agents.llm_agent import Agent
from aworld.sandbox import Sandbox
from aworld.runner import Runners
from aworld.core.task import Task
from aworld.logs.util import logger
import json
from aworld.config import TaskConfig, TaskRunMode

COMPOSER_BASE64_ENCODED_KEY_AND_SECRET = os.environ.get("COMPOSER_BASE64_ENCODED_KEY_AND_SECRET", "")

ENV_JSON = json.dumps({
    "X_API_KEY_ID": os.getenv("X_API_KEY_ID", ""),
    "X_API_AUTHORIZATION": os.getenv("X_API_AUTHORIZATION", ""),
})

TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhcHAiOiJhd29ybGRjb3JlLWFnZW50IiwidmVyc2lvbiI6MSwidGltZSI6MTc1NjM0ODcyMi45MTYyODd9.zM_l1VghOHaV6lC_0fYmZ35bLnH8uxIaA8iGeyuwQWY"

mcp_config = {
    "mcpServers": {
        "trading-server": {
            "type": "streamable-http",
            "url": "http://mcp.aworldagents.com/vpc-pre/mcp",
            "headers": {
                "env_name": "trading",
                "Authorization": f"Bearer {TOKEN}",
                "IMAGE_VERSION": "trading-20251127114005",
                "SANDBOX_ENV": f"{ENV_JSON}",
            },
            "timeout": 6000,
            "sse_read_timeout": 6000,
            "client_session_timeout_seconds": 6000
        }
    }
}


async def _list_tools():
    sand_box = Sandbox(mcp_config=mcp_config, mcp_servers=["trading-server"])
    return await sand_box.mcpservers.list_tools()


trader_agent = Agent(
    name="Trader Agent",
    system_prompt="You specialize at trading.",
    mcp_config=mcp_config
)
if __name__ == "__main__":
    query = (
        "for the companies NVIDIA (NVDA), Advanced Micro Devices (AMD), and Intel (INTC), "
        "retrieve retrieve historical daily price data using historical_price of one year."
        "Then, summary the price data and give a concise answer."
    )


    async def single_step_introspection(query):
        task = Task(
            input=query,
            agent=trader_agent,
            conf=TaskConfig(
                resp_carry_context=True,
                run_mode=TaskRunMode.INTERACTIVE
            )
        )

        trajectory_log = os.path.join(os.path.dirname(__file__), "trading_log.txt")

        if os.path.exists(trajectory_log):
            os.remove(trajectory_log)

        is_finished = False
        step = 1
        while not is_finished:
            with open(trajectory_log, "a", encoding="utf-8") as traj_file:
                is_finished, observation, response = await Runners.step(task)
                traj_file.write(f"Step {step}\n")
                traj_file.write(json.dumps(response.trajectory, ensure_ascii=False, indent=2))
                traj_file.write("\n\n")
                step += 1


    asyncio.run(single_step_introspection(query))