import json
import logging
import os
import traceback
import uuid
from abc import abstractmethod
from typing import List, AsyncGenerator, Any

from aworld.config import AgentConfig, TaskConfig
from aworld.core.agent.base import Agent
from aworld.core.task import Task
from aworld.output import WorkSpace, AworldUI, Outputs
from aworld.runner import Runners

from aworldspace.ui.open_aworld_ui import OpenAworldUI

from client.aworld_client import AworldTask


class AworldBaseAgent:

    def pipes(self) -> list[dict]:
        return [{"id": self.agent_name(), "name": self.agent_name()}]


    @abstractmethod
    def agent_name(self) -> str:
        pass


    async def pipe(
            self,
            user_message: str,
            model_id: str,
            messages: List[dict],
            body: dict
    ):

        try:
            print(f"body is {body}")
            print(f"user_message is {user_message}")

            task = await self.get_task_from_body(body)
            if task:
                task_id = task.task_id
            else:
                task_id = str(uuid.uuid4())

            user_input = await self.get_custom_input(user_message, model_id, messages, body)

            # build agent task read from config
            agent = await self.build_agent(body = body)

            # return task
            task = await self.build_task(agent=agent, task_id=task_id, user_input=user_input, user_message=user_message)

            # render output
            async_generator = await self.parse_task_output(task_id, task)

            return async_generator()

        except Exception as e:
            return await self._format_exception(e)

    async def _format_exception(self, e: Exception) -> str:
        traceback.print_exc()
        # tb_lines = traceback.format_exception(type(e), e, e.__traceback__)
        # detailed_error = "".join(tb_lines)
        # logging.error(e)
        # return json.dumps({"error": detailed_error}, ensure_ascii=False)
        return "💥💥💥process failed💥💥💥"


    async def _format_error(self, status_code: int, error: bytes) -> str:
        if isinstance(error, str):
            error_str = error
        else:
            error_str = error.decode(errors="ignore")
        try:
            err_msg = json.loads(error_str).get("message", error_str)[:200]
        except Exception:
            err_msg = error_str[:200]
        return json.dumps(
            {"error": f"HTTP {status_code}: {err_msg}"}, ensure_ascii=False
        )

    async def get_custom_input(self, user_message: str,
            model_id: str,
            messages: List[dict],
            body: dict) -> Any:
        user_input = body["messages"][-1]["content"]
        return user_input

    @abstractmethod
    def get_history_messages(self):
        return 100

    @abstractmethod
    async def get_agent_config(self, body) -> AgentConfig:
        pass

    @abstractmethod
    async def get_mcp_servers(self, body) -> list[str]:
        pass

    async def build_agent(self, body: dict):

        agent_config =await self.get_agent_config(body)
        mcp_servers = await self.get_mcp_servers(body)
        agent = Agent(
            conf=agent_config,
            name=agent_config.name,
            system_prompt=agent_config.system_prompt,
            mcp_servers=mcp_servers,
            mcp_config=await self.load_mcp_config(),
            history_messages=self.get_history_messages()
        )
        return agent

    async def build_task(self, agent, task_id, user_input, user_message):
        task = Task(
            id=task_id,
            name=task_id,
            input=user_input,
            agent=agent,
            conf=TaskConfig(
                task_id=task_id,
                stream=False,
                ext={
                    "origin_message": user_message
                }
            )
        )
        return task



    async def parse_task_output(self, chat_id, task):
        _SENTINEL = object()

        async def async_generator():

            from asyncio import Queue
            queue = Queue()

            async def consume_all():
                openwebui_ui = OpenAworldUI(
                    chat_id=chat_id,
                    workspace=WorkSpace.from_local_storages(
                        workspace_id=chat_id,
                        storage_path=os.path.join(os.curdir, "workspaces", chat_id)
                    )
                )

                # get outputs
                outputs = Runners.streamed_run_task(task)

                # output hooks
                await self.custom_output_before_task(outputs, chat_id, task)

                # render output
                try:
                    async for output in outputs.stream_events():
                        res = await AworldUI.parse_output(output, openwebui_ui)
                        if res:
                            if isinstance(res, AsyncGenerator):
                                async for item in res:
                                    await queue.put(item)
                            else:
                                await queue.put(res)
                    custom_output = await self.custom_output_after_task(outputs, chat_id, task)
                    if custom_output:
                        await queue.put(custom_output)
                finally:
                    await queue.put(_SENTINEL)

            # Start the consumer in the background
            import asyncio
            consumer_task = asyncio.create_task(consume_all())

            while True:
                item = await queue.get()
                if item is _SENTINEL:
                    break
                yield item
            await consumer_task

        return async_generator

    async def custom_output_before_task(self, outputs: Outputs, chat_id: str, task: Task) -> str | None:
        return None

    async def custom_output_after_task(self, outputs: Outputs, chat_id: str, task: Task):
        pass

    async def get_task_from_body(self, body: dict) -> AworldTask | None:
        try:
            return AworldTask.model_validate_json(body.get("user").get("aworld_task"))
        except Exception as err:
            logging.error(f"Error parsing AworldTask: {err}; data: {body.get('user_message')}")
            traceback.print_exc()
            return None

    @abstractmethod
    async def load_mcp_config(self) -> dict:
        pass
