# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc
import json
import traceback
from typing import AsyncGenerator, Dict, Any, List, Union, Callable
from datetime import datetime

import aworld.trace as trace
from aworld.agents.parallel_llm_agent import ParallelizableAgent
from aworld.agents.serial_llm_agent import SerialableAgent
from aworld.config.conf import AgentConfig, ConfigDict, RunConfig
from aworld.core.agent.base import AgentFactory, BaseAgent, AgentResult, is_agent_by_name, is_agent
from aworld.core.common import Observation, ActionModel
from aworld.core.context.base import AgentContext
from aworld.core.context.base import Context
from aworld.core.event.base import Message, ToolMessage, Constants, AgentMessage
from aworld.core.memory import MemoryItem, MemoryConfig
from aworld.logs.util import logger
from aworld.models.llm import get_llm_model, call_llm_model, acall_llm_model, acall_llm_model_stream
from aworld.runner import Runners
from aworld.runners.hook.hooks import HookPoint
from aworld.utils.common import sync_exec
from aworld.agents.llm_agent import Agent
from aworld.runners.utils import choose_runners, execute_runner
from aworld.core.task import Task

# plan = Agent("")
# execute1 = Agent("")
# execute2 = Agent("")
# execute3 = Agent("")
# execute4 = Agent("")
# # 定义
# TeamSwarm(plan, execute1, execute2, execute3, execute4, build_type=GraphBuildType.TEAM)
#
# # 执行

class PlanAgent(Agent):
    def __init__(self, conf: Union[Dict[str, Any], ConfigDict, AgentConfig], **kwargs):
        super().__init__(conf, **kwargs)
        self.cur_action_step = 0
        self.max_steps = 10
        self.cur_step = 0

    async def async_run(self, message: Message, **kwargs) -> Message:
        """Execute the main logic of the plan agent, supporting parallel or serial execution of multiple actions.
        
        Args:
            message: Input message
            **kwargs: Additional parameters
            
        Returns:
            Message: Execution result message
        """
        self.context = message.context
        # Only init context if cur_step = 0
        if self.cur_step == 0:
            self._init_context(message.context)
        # Check if maximum steps reached
        if self.cur_step >= self.max_steps:
            logger.info(f"Maximum steps {self.max_steps} reached, stopping execution")
            return self._create_finished_message(message, "Maximum steps limit reached")

        observation = message.payload
        if not isinstance(observation, Observation):
            logger.warn(f"Invalid message payload, 'Observation' expected, got: {observation}")
            return self._create_finished_message(message, "Invalid message payload")

        actions = await self.async_policy(message.payload, **kwargs)
        if not actions:
            return self._create_finished_message(message, "No valid actions from llm response")
        self._llm = None
        # Create corresponding agents or tools based on actions
        agents, tools = await self._create_agents_and_tools(actions)

        # should stop
        if self._is_done(actions) or (not agents and not tools):
            logger.info("No more actions, all tasks completed.")
            return self._create_finished_message(message, actions)

        # todo: parallelize tool execution and agent execution
        tool_results = []
        if tools:
            tool_tasks = []
            # get tool results
            for action in tools:
                tool_tasks.append(self.fork_new_task(input=Observation(content=[action]), context=self.context))

            if not tool_tasks:
                raise RuntimeError("no tool task need to run in plan agent.")

            runners = await choose_runners(tool_tasks)
            res = await execute_runner(runners, RunConfig(reuse_process=True))

            for k, v in res.items():
                tool_results.append(ActionModel(agent_name=self.id(), policy_info=v.answer))
                self._save_action_trajectory(self.cur_action_step, v)
                # 获取对应的工具名称
                tool_name = tools[len(tool_results)-1].tool_name if len(tool_results) <= len(tools) else "unknown_tool"
                logger.info(f"Tool execution - Step {self.cur_action_step}, Tool: {tool_name}, Task: {k} -> Result: {v}")
                self.cur_action_step += 1

        agent_results = []
        # if agents:
        #     parallel_agents = []
        #     inputs = []
        #     for agent_action in agents:
        #         agent_name = agent_action.tool_name
        #         agent = AgentFactory.agent_instance(agent_name)
        #         agent = Agent(name=agent.name(), conf=agent.conf, system_prompt=agent.system_prompt,
        #                       agent_prompt=agent.agent_prompt)
        #         parallel_agents.append(agent)
        #         inputs.append(Observation(content=agent_action.params.get("content")))
        #     parallel_agent = ParallelizableAgent(conf=AgentConfig(), name="parallel", agents=parallel_agents)
        #     agent_results = await parallel_agent.run(inputs, self.context)

        if agents:
            parallel_agent_res = None
            # Decide whether to use parallel or serial execution
            agent_tasks = []
            for agent_action in agents:
                agent_name = agent_action.tool_name
                agent = AgentFactory.agent_instance(agent_name)
                input = agent_action.params.get("content") # TODO: 需要修改
                agent_tasks.append(self.fork_new_task(input=input, agent=agent, context=self.context))
                if not agent_tasks:
                    raise RuntimeError("no agent task need to run in plan agent.")

            if self._should_use_parallel(actions):
                agent_runners = await choose_runners(agent_tasks)
                parallel_agent_res = await execute_runner(agent_runners, RunConfig(reuse_process=True))

                for k, v in parallel_agent_res.items():
                    agent_results.append(ActionModel(agent_name=self.id(), policy_info=v.answer))
                    self._save_action_trajectory(self.cur_action_step, v)
                    # 获取对应的Agent名称
                    agent_name = agents[len(agent_results)-1].tool_name if len(agent_results) <= len(agents) else "unknown_agent"
                    logger.info(f"Parallel agent execution - Step {self.cur_action_step}, Agent: {agent_name}, Task: {k} -> Result: {v}")
                    self.cur_action_step += 1
                # logger.info("Using parallel execution mode")
                # parallel_agent = ParallelizableAgent(conf=self.conf, agents=agents)
                # parallel_agent_res = await parallel_agent.async_run(message, **kwargs)
            else:
                for task in agent_tasks:
                    agent_runners = await choose_runners([task])
                    agent_res = await execute_runner(agent_runners, RunConfig(reuse_process=True))

                    for k, v in agent_res.items():
                        agent_results.append(ActionModel(agent_name=task.agent.id(), policy_info=v.answer))
                        self._save_action_trajectory(self.cur_action_step, v)
                        # 使用task.agent获取Agent名称
                        agent_name = task.agent.id() if task.agent else "unknown_agent"
                        logger.info(f"Serial agent execution - Step {self.cur_action_step}, Agent: {agent_name}, Task: {k} -> Result: {v}")
                        self.cur_action_step += 1
                # logger.info("Using serial execution mode")
                # serial_agent = SerialableAgent(conf=self.conf, agents=agents)
                # parallel_agent_res = await serial_agent.async_run(message, **kwargs)

        # replan
        # Increment step counter
        self.cur_step += 1
        logger.info(f"Current execution step: {self.cur_step}/{self.max_steps}\ntrajectories: {self.context.trajectories}")
        # todo:
        #  1. update context
        #  2. build next step message from agent_results and tools_results
        next_message = self._actions_to_message(agent_results, tool_results, message)
        return await self.async_run(next_message, **kwargs)

    async def async_policy(self, observation: Observation, **kwargs) -> List[ActionModel]:
        # Otherwise, parse actions using LLM
        # Prepare LLM input
        llm_messages = await self._prepare_llm_input(observation)
        llm_response = None
        try:
            llm_response = await self._call_llm_model(observation, llm_messages, **kwargs)
        except Exception as e:
            logger.warn(traceback.format_exc())
            raise e
        finally:
            if llm_response:
                # update usage
                self.update_context_usage(used_context_length=llm_response.usage['total_tokens'])
                # update current step output
                self.update_llm_output(llm_response)

                use_tools = self.use_tool_list(llm_response)
                is_use_tool_prompt = len(use_tools) > 0
                if llm_response.error:
                    logger.info(f"llm result error: {llm_response.error}")
                else:
                    self.memory.add(MemoryItem(
                        content=llm_response.content,
                        metadata={
                            "role": "assistant",
                            "agent_name": self.id(),
                            "tool_calls": llm_response.tool_calls if not self.use_tools_in_prompt else use_tools,
                            "is_use_tool_prompt": is_use_tool_prompt if not self.use_tools_in_prompt else False
                        }
                    ))
            else:
                logger.error(f"{self.id()} failed to get LLM response")
                raise RuntimeError(f"{self.id()} failed to get LLM response")

        try:
            events = []
            async for event in self.run_hooks(self.context, HookPoint.POST_LLM_CALL):
                events.append(event)
        except Exception as e:
            logger.warn(traceback.format_exc())

        agent_result = sync_exec(self.resp_parse_func, llm_response)
        print(f"plan_agent.agent_result: {agent_result}")
        return agent_result.actions

    def fork_new_task(self, input, agent:Agent = None, context: Context = None):
        # Use the new deep_copy method for complete and generic copying
        new_context = context.deep_copy()
        new_task = Task(input=input, agent=agent, context=new_context)
        new_context.set_task(new_task)
        new_context.task_id = new_task.id
        return new_task

    def _save_action_trajectory(self, step, action: ActionModel):
        # 将agent_results和tool_results保存到trajectories中
        step_key = f"step_{step}"
        step_data = {
            "step": step,
            "action": action,
            "timestamp": datetime.now().isoformat(),
            "agent_name": self.id()
        }
        self.context.trajectories[step_key] = step_data

    async def _create_agents_and_tools(self, actions: List[ActionModel]):
        """Create corresponding agents or tools based on actions"""
        agents_and_tools = []
        agents = []
        tools = []
        
        for action in actions:
            try:
                if is_agent(action):
                    agents.append(action)
                elif action.tool_name:
                    tools.append(action)
            except Exception as e:
                logger.error(f"Failed to parse actions from actions to agent or tool: {e}")

        return agents, tools
        
    def _should_use_parallel(self, agent_tasks) -> bool:
        return False
        """Decide whether to use parallel execution mode"""
        # More complex logic can be implemented based on actual requirements
        # For example: check if there are dependencies between actions
        
        # By default, use parallel mode if there are more than one action
        return len(agent_tasks) > 1

        
    def _is_done(self, actions: List[ActionModel]) -> bool:
        """Check if all tasks are completed"""
        return False
        
    def _create_finished_message(self, original_message: Message, result: Any) -> Message:
        """Create a message indicating task completion"""
        # Create a message containing the final result
        # todo: create message from context
        return AgentMessage(payload=result,
                            sender=self.id(),
                            receiver=self.id(),
                            session_id=self.context.session_id if self.context else "",
                            headers={"context": self.context})

    def _actions_to_message(self, agents_result_actions: List[ActionModel],tools_result_actions: List[ActionModel], original_message: Message) -> Message:
        """Convert actions to a new message"""
        # Create a new message containing actions
        content_list = []
        content_list.extend(agents_result_actions)
        content_list.extend(tools_result_actions)
        new_message = AgentMessage(
            payload=Observation(content=json.dumps([content.model_dump() for content in content_list], ensure_ascii=False)),
            sender=self.id(),
            receiver=self.id(),
            session_id=self.context.session_id,
            headers={"context": self.context}
        )
        return new_message