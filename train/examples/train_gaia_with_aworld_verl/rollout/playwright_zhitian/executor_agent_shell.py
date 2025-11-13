
import re
import asyncio
from typing import Dict, Any, List, Optional, Union

from aworld.agents.llm_agent import Agent

from aworld.core.context.base import Context

from aworld.config import AgentConfig, TaskConfig
from aworld.core.common import Observation, ActionModel
from aworld.core.event.base import Message
from aworld.output import Output
from aworld.runner import Runners, Task
# from aworldspace.agents.fast_agent.tools.out_put_tools import build_parent_task_status_output, build_logo_output
from train.examples.train_gaia_with_aworld_verl.rollout.playwright_zhitian.executor_agent_meat import ExecuteAgent
# from aworldspace.models.agent import ApplicationAgent
from aworld.logs.util import logger


class GaiaPlayWrightAgent(Agent):
    start_flag: bool = True
    executors = []

    def get_task_context(self, message: Message) -> Context:
        return message.context

    # async def send_logo_output(self, message: Message):
    #     await self.send_output(message=message, data=build_logo_output())

    # async def send_outputs(self, message: Message, list_data: list[str]):
    #     for data in list_data:
    #         await self.send_output(message=message, data=data)

    # async def send_output(self, message: Message, data: str):
    #     await message.context.outputs.add_output(Output(task_id=message.task_id, data=data))

    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {},
                           message: Message = None, **kwargs) -> List[ActionModel]:
        print(f"observation: {observation.content}")
        actions = observation.content.split('&')
        context = self.get_task_context(message)
        current_task = context._task
        #TODO: 并行执行所有任务
        exe_answer = "以下是我的查询结果:"
        ## 把for循环改成并行执行 使用python的异步包async，不要写的太复杂，把核心执行逻辑包装成一个方法，调用就好了
        # 原顺序执行代码（已注释）
        # for index, action in enumerate(actions):
        #     print(f"action: {action}, index: {index}")
        #     execute_agent = await self.build_execute_agent(index=index)
        #     sub_task = await self.build_sub_aworld_run_task(index=index, input=action, agent=execute_agent,
        #                                                     sub_task_context=context, parent_task=current_task)
        #     task_result = await Runners.run_task(sub_task)
        #     task_response = task_result[sub_task.id] if task_result else None
        #     answer = task_response.answer if task_response is not None else None
        #     exe_answer = f"{exe_answer}\n\n{answer}"
        #     print(f"exec_agent_{index} answer: {answer}")
        
        # 并行执行所有任务
        tasks = [
            self._execute_single_action(index, action, context, current_task)
            for index, action in enumerate(actions)
        ]
        results = await asyncio.gather(*tasks)
        
        # 按index排序并拼接结果
        results.sort(key=lambda x: x[0])  # 按index排序
        for index, answer in results:
            exe_answer = f"{exe_answer}\n\n{answer}"

        print(f"exe_answer: {exe_answer}")
        return [ActionModel(policy_info=exe_answer, agent_name=self.id())]
        # action_model_list = await super().async_policy(observation, info, message, **kwargs)
        # content = action_model_list[0].policy_info
        # return action_model_list

    max_loop = 100

    async def should_terminate_loop(self, message: Message) -> bool:
        return self.loop_step >= self.max_loop

    async def _execute_single_action(self, index: int, action: str, context: Context, current_task: Task) -> tuple[int, str]:
        """
        执行单个action的核心逻辑
        返回 (index, answer) 元组
        """
        print(f"action: {action}, index: {index}")
        execute_agent = await self.build_execute_agent(index=index)
        sub_task = await self.build_sub_aworld_run_task(index=index, input=action, agent=execute_agent,
                                                        sub_task_context=context, parent_task=current_task)
        self.executors.append({"agent_id": execute_agent.id(), "task_id": sub_task.id, "query": action})

        task_result = await Runners.run_task(sub_task)
        task_response = task_result[sub_task.id] if task_result else None
        answer = task_response.answer if task_response is not None else None
        print(f"exec_agent_{index} answer: {answer}")
        return (index, answer)

    async def build_execute_agent(self, index: int) -> Agent:
        return ExecuteAgent(
            name=f"exec_agent_{index}",
            conf=self.conf,
            system_prompt=self.system_prompt,
            mcp_servers=self.mcp_servers,
            mcp_config=self.mcp_config
        )

    async def build_sub_aworld_run_task(self, index: int,input: str,
                                        agent: Optional[Agent], sub_task_context: Context,
                                        parent_task: Task) -> Task:
        aworld_run_task = Task(
            user_id=sub_task_context.user_id,
            session_id=sub_task_context.session_id,
            input=input,
            endless_threshold=5,
            agent=agent,
            context=sub_task_context,
            conf=TaskConfig(
                stream=False,
                exit_on_failure=True
            ),
            is_sub_task=True,
            outputs=parent_task.outputs,
        )
        return aworld_run_task