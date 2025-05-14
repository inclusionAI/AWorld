import os
import uuid
from collections.abc import AsyncGenerator
from typing import List

from aworld.config.conf import AgentConfig
from aworld.core.agent.base import Agent
from aworld.output import AworldUI, WorkSpace
from aworld.runner import Runners
from pydantic import BaseModel, Field

from aworldspace.base_agent import AworldBaseAgent


class Pipeline(AworldBaseAgent):
    class Valves(BaseModel):
        llm_model_name: str = Field(default="DeepSeek-V3-Function-Call", description="llm_model_name")
        llm_base_url: str = Field(
            default="https://antchat.alipay.com/v1",
            description="search api type, antsearch/tavily",
        )
        llm_api_key: str = Field(
            default="",
            description="llm api key",
        )
        agent_sys_prompt: str = Field(
            default="You are a helpful agent.",
            description="search api type, antsearch/tavily",
        )
        history_messages: int = Field(default=100, description="rounds of history messages")

    def __init__(self):
        self.valves = self.Valves()
        self.agent_config = AgentConfig(
            llm_provider="openai",
            llm_model_name=self.valves.llm_model_name if self.valves.llm_model_name else os.environ.get("LLM_MODEL_NAME"),
            llm_api_key=self.valves.llm_api_key if len(self.valves.llm_api_key) > 0 else os.environ.get("LLM_API_KEY"),
            llm_base_url=self.valves.llm_base_url if self.valves.llm_base_url else os.environ.get("LLM_BASE_URL")
        )

    def agent_name(self) -> str:
        return "AmapAgent"

    async def pipe(
            self,
            user_message: str,
            model_id: str,
            messages: List[dict],
            body: dict
    ):
        print(f"body is {body}")
        print(f"user_message is {user_message}")
        print(f"body is {body}")

        # resolve params TODO 从前面传过来
        chat_id = str(uuid.uuid4())
        user_input = body["messages"][-1]["content"]



        # build agent task read from config
        mcp_servers = ["filesystem", "amap-amap-sse"]
        agent = await self.build_agent(mcp_servers=mcp_servers)

        # return task
        task = await self.build_task(agent=agent, task_id=chat_id, user_input=user_input, user_message=user_message)

        # render output
        # render output
        openwebui_ui = OpenWebuiAworldUI(
            chat_id=chat_id,
            workspace=WorkSpace.from_local_storages(
                workspace_id=chat_id,
                storage_path=os.path.join(os.curdir, "workspaces", chat_id)
            )
        )

        try:
            async for output in Runners.streamed_run_task(task).stream_events():
                res = await AworldUI.parse_output(output, openwebui_ui)
                print(type(output))
                if res:
                    if isinstance(res, AsyncGenerator):
                        async for item in res:
                            yield item
                    else:
                        yield res
        except Exception as e:
            yield await self._format_exception(e)

    async def build_agent(self, mcp_servers, **kwargs):
        agent = Agent(
            conf=self.agent_config,
            name=self.agent_name(),
            system_prompt=self.valves.agent_sys_prompt,
            mcp_servers=mcp_servers,
            mcp_config={
                "mcpServers": {
                    "amap-amap-sse": {
                        "url": "https://mcp.amap.com/sse?key=" + os.environ["AMAP_API_KEY"],
                        "timeout": 5.0,
                        "sse_read_timeout": 300.0
                    },
                    "playwright": {
                        "command": "npx",
                        "args": [
                            "@playwright/mcp@latest"
                        ]
                    },
                    "filesystem": {
                        "command": "npx",
                        "args": [
                            "-y",
                            "@modelcontextprotocol/server-filesystem",
                            str(os.path.curdir)
                        ]
                    }
                }
            },
            history_messages=self.valves.history_messages
        )
        return agent
