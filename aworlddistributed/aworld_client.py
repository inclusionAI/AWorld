from typing import Optional, Any, AsyncGenerator

from aworld.models.llm import acall_llm_model, get_llm_model
from aworld.models.model_response import ModelResponse
from pydantic import BaseModel, Field


class AworldTask(BaseModel):
    task_id: str = Field(default=None, description="task id")
    agent_id: str = Field(default=None, description="agent id")
    agent_input: str = Field(default=None, description="agent input")
    session_id: Optional[str] = Field(default=None, description="session id")
    user_id: Optional[str] = Field(default=None, description="user id")
    node_id: Optional[str] = Field(default=None, description="execute task node_id")

class AworldTaskResult(BaseModel):
    server_host: str = Field(default=None, description="aworld server id")
    data: Any = Field(default=None, description="result data")


class AworldTaskClient(BaseModel):
    """
    AworldTaskClient
    """
    know_hosts: list[str] = Field(default_factory=list, description="aworldserver list")
    tasks: list[AworldTask] = Field(default_factory=list, description="submitted task list")
    task_states: dict[str, AworldTaskResult] = Field(default_factory=dict, description="task_states")

    async def submit_task(self, task: AworldTask):
        if not self.know_hosts:
            raise ValueError("No aworld server hosts configured.")
        # 1. select aworld server from know_hosts using round-robin
        if not hasattr(self, '_current_server_index'):
            self._current_server_index = 0
        aworld_server = self.know_hosts[self._current_server_index]
        self._current_server_index = (self._current_server_index + 1) % len(self.know_hosts)

        # 2. call _submit_task
        result = await self._submit_task(aworld_server, task)
        # 3. update task_states
        self.task_states[task.task_id] = result

        
    async def _submit_task(self, aworld_server, task: AworldTask):
        try:
            print(f"submit task#{task.task_id} to cluster#[{aworld_server}]")

            return await self._submit_task_to_server(aworld_server, task)
        except Exception as e:
            print(f"submit task to {aworld_server} failed: {e}")
            return None

    async def _submit_task_to_server(self, aworld_server, task: AworldTask):
        # build params
        llm_model = get_llm_model(
            llm_provider="openai",
            model_name=task.agent_id,
            base_url=f"http://{aworld_server}/v1",
            api_key="0p3n-w3bu!"
        )
        messages = [
            {"role": "user", "content": task.agent_input}
        ]
        #call_llm_model
        data = await acall_llm_model(llm_model, messages, stream=True, user={
            "user_id": task.user_id,
            "session_id": task.session_id,
            "task_id": task.task_id
        })
        result_data = ""
        if isinstance(data, AsyncGenerator):
            async for item in data:
                if item.raw_response and item.raw_response.model_extra and item.raw_response.model_extra.get('node_id'):
                    if not task.node_id:
                        print(f"submit task#{task.task_id} success. execute pod ip is [{item.raw_response.model_extra.get('node_id')}]")
                    task.node_id = item.raw_response.model_extra.get('node_id')

                if item.content:
                    print(item)
                    result_data += item.content


        elif isinstance(data, ModelResponse):
            if data.raw_response and data.raw_response.model_extra and data.raw_response.model_extra.get('node_id'):
                if not task.node_id:
                    print(f"submit task#{task.task_id} success. execute pod ip is [{data.raw_response.model_extra.get('node_id')}]")
                task.node_id = data.raw_response.model_extra.get('node_id')

            print(data)
            if data.content:
                result_data = data.content

        return AworldTaskResult(server_host=aworld_server, data=result_data)

    async def get_task_state(self, task_id: str):
        if not isinstance(self.task_states, dict):
            self.task_states = dict(self.task_states)
        return self.task_states.get(task_id, None)

