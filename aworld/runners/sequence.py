# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import time

from typing import List

from aworld.core.agent.base import Agent
from aworld.core.common import Observation, ActionModel
from aworld.core.context.base import Context
from aworld.core.envs.tool import ToolFactory, Tool, AsyncTool
from aworld.core.envs.tool_desc import is_tool_by_name
from aworld.core.task import Task, TaskResponse
from aworld.logs.util import logger, color_log, Color, trace_logger
from aworld.models.model_response import ToolCall
from aworld.output.base import StepOutput, ToolResultOutput
from aworld.runners.task_runner import TaskRunner
from aworld.utils.common import override_in_subclass
import aworld.trace as trace
import json


class SequenceRunner(TaskRunner):
    def __init__(self, task: Task, *args, **kwargs):
        super().__init__(task=task, *args, **kwargs)

    async def do_run(self, context: Context = None) -> TaskResponse:
        """Multi-agent sequence general process workflow.

        NOTE: Use the agent's finished state(no tool calls) to control the inner loop.
        Args:
            observation: Observation based on env
            info: Extend info by env
        """
        observation = self.observation
        if not observation:
            raise RuntimeError("no observation, check run process")

        start = time.time()
        step = 1
        max_steps = self.conf.get("max_steps", 100)
        msg = None

        # Use trace.span to record the entire task execution process
        with trace.span(f"task_execution_{self.task.id}", attributes={
            "task_id": self.task.id,
            "task_name": self.task.name,
            "start_time": start
        }) as task_span:
            try:
                for i in range(self.swarm.max_steps):
                    pre_agent_name = None
                    for idx, agent in enumerate(self.swarm.ordered_agents):
                        observation.from_agent_name = agent.name()
                        observations = [observation]
                        policy = None
                        cur_agent = agent
                        while step <= max_steps:
                            await self.outputs.add_output(StepOutput.build_start_output(name=f"Step{step}", step_num=step))

                            terminated = False

                            observation = self.swarm.action_to_observation(
                                policy, observations)
                            observation.from_agent_name = observation.from_agent_name or cur_agent.name()

                            if observation.to_agent_name and observation.to_agent_name != cur_agent.name():
                                cur_agent = self.swarm.agents.get(
                                    observation.to_agent_name)

                            exp_id = self._get_step_span_id(
                                step, cur_agent.name())
                            with trace.span(f"step_execution_{exp_id}") as step_span:
                                step_span.set_attributes({
                                    "exp_id": exp_id,
                                    "task_id": self.task.id,
                                    "task_name": self.task.name,
                                    "trace_id": trace.get_current_span().get_trace_id(),
                                    "step": step,
                                    "agent_id": cur_agent.name(),
                                    "pre_agent": pre_agent_name,
                                    "observation": json.dumps(observation.model_dump(exclude_none=True), ensure_ascii=False)
                                })
                                pre_agent_name = cur_agent.name()

                                if not override_in_subclass('async_policy', cur_agent.__class__, Agent):
                                    policy: List[ActionModel] = cur_agent.run(observation,
                                                                              step=step,
                                                                              outputs=self.outputs,
                                                                              stream=self.conf.get(
                                                                                  "stream", False),
                                                                              exp_id=exp_id)
                                else:
                                    policy: List[ActionModel] = await cur_agent.async_run(observation,
                                                                                          step=step,
                                                                                          outputs=self.outputs,
                                                                                          stream=self.conf.get(
                                                                                              "stream", False),
                                                                                          exp_id=exp_id)

                                step_span.set_attribute("actions", json.dumps(
                                    [action.model_dump() for action in policy], ensure_ascii=False))
                                observation.content = None
                                color_log(
                                    f"{cur_agent.name()} policy: {policy}")
                                if not policy:
                                    logger.warning(
                                        f"current agent {cur_agent.name()} no policy to use.")
                                    await self.outputs.add_output(
                                        StepOutput.build_failed_output(name=f"Step{step}",
                                                                       step_num=step,
                                                                       data=f"current agent {cur_agent.name()} no policy to use.")
                                    )
                                    await self.outputs.mark_completed()
                                    task_span.set_attributes({
                                        "end_time": time.time(),
                                        "duration": time.time() - start,
                                        "status": "failed",
                                        "error": f"current agent {cur_agent.name()} no policy to use."
                                    })
                                    return TaskResponse(msg=f"current agent {cur_agent.name()} no policy to use.",
                                                        answer="",
                                                        success=False,
                                                        id=self.task.id,
                                                        time_cost=(
                                                            time.time() - start),
                                                        usage=self.context.token_usage)

                                if self.is_agent(policy[0]):
                                    status, info = await self._agent(agent, observation, policy, step)
                                    if status == 'normal':
                                        if info:
                                            observations.append(observation)
                                    elif status == 'break':
                                        observation = self.swarm.action_to_observation(
                                            policy, observations)
                                        break
                                    elif status == 'return':
                                        await self.outputs.add_output(
                                            StepOutput.build_finished_output(
                                                name=f"Step{step}", step_num=step)
                                        )
                                        info.time_cost = (time.time() - start)
                                        task_span.set_attributes({
                                            "end_time": time.time(),
                                            "duration": info.time_cost,
                                            "status": "success"
                                        })
                                        return info
                                elif is_tool_by_name(policy[0].tool_name):
                                    msg, reward, terminated = await self._tool_call(policy, observations, step)
                                    step_span.set_attribute("reward", reward)

                                else:
                                    logger.warning(
                                        f"Unrecognized policy: {policy[0]}")
                                    await self.outputs.add_output(
                                        StepOutput.build_failed_output(name=f"Step{step}",
                                                                       step_num=step,
                                                                       data=f"Unrecognized policy: {policy[0]}, need to check prompt or agent / tool.")
                                    )
                                    await self.outputs.mark_completed()
                                    task_span.set_attributes({
                                        "end_time": time.time(),
                                        "duration": time.time() - start,
                                        "status": "failed",
                                        "error": f"Unrecognized policy: {policy[0]}, need to check prompt or agent / tool."
                                    })
                                    return TaskResponse(
                                        msg=f"Unrecognized policy: {policy[0]}, need to check prompt or agent / tool.",
                                        answer="",
                                        success=False,
                                        id=self.task.id,
                                        time_cost=(time.time() - start),
                                        usage=self.context.token_usage
                                    )
                                await self.outputs.add_output(
                                    StepOutput.build_finished_output(name=f"Step{step}",
                                                                     step_num=step, )
                                )
                                step += 1
                                if terminated and agent.finished:
                                    logger.info("swarm finished")
                                    break
            except Exception as err:
                logger.error(f"Runner run failed, err is {err}", exc_info=True)
            finally:
                await self.outputs.mark_completed()
                color_log(f"task token usage: {self.context.token_usage}",
                          color=Color.pink,
                          logger_=trace_logger)
                for _, tool in self.tools.items():
                    if isinstance(tool, AsyncTool):
                        await tool.close()
                    else:
                        tool.close()
                task_span.set_attributes({
                    "end_time": time.time(),
                    "duration": time.time() - start,
                    "error": msg if msg else ""
                })
            return TaskResponse(msg=msg,
                                answer=observation.content,
                                success=True if not msg else False,
                                id=self.task.id,
                                time_cost=(time.time() - start),
                                usage=self.context.token_usage)

    async def _agent(self, agent: Agent, observation: Observation, policy: List[ActionModel], step: int):
        # only one agent, and get agent from policy
        policy_for_agent = policy[0]
        agent_name = policy_for_agent.agent_name
        if not agent_name:
            agent_name = policy_for_agent.tool_name
        cur_agent: Agent = self.swarm.agents.get(agent_name)
        if not cur_agent:
            raise RuntimeError(f"Can not find {agent_name} agent in swarm.")

        status = "normal"
        if cur_agent.name() == agent.name():
            # Current agent is entrance agent, means need to exit to the outer loop
            logger.info(f"{cur_agent.name()} exit the loop")
            status = "break"
            return status, None

        if agent.handoffs and agent_name not in agent.handoffs:
            # Unable to hand off, exit to the outer loop
            status = "return"
            return status, TaskResponse(msg=f"Can not handoffs {agent_name} agent ",
                                        answer=observation.content,
                                        success=False,
                                        id=self.task.id,
                                        usage=self.context.token_usage)
        # Check if current agent done
        if cur_agent.finished:
            cur_agent._finished = False
            logger.info(
                f"{cur_agent.name()} agent be be handed off, so finished state reset to False.")

        con = policy_for_agent.policy_info
        if policy_for_agent.params and 'content' in policy_for_agent.params:
            con = policy_for_agent.params['content']
        if observation:
            observation.content = con
            observation.to_agent_name = cur_agent.name()
            observation.from_agent_name = agent.name()
        else:
            observation = Observation(content=con)
            observation.to_agent_name = cur_agent.name()
            observation.from_agent_name = agent.name()
            return status, observation
        return status, None

    async def _tool_call(self, policy: List[ActionModel], observations: List[Observation], step: int):
        msg = None
        terminated = False
        reward = 0.0
        # group action by tool name
        tool_mapping = dict()
        # Directly use or use tools after creation.
        for act in policy:
            if not self.tools or (self.tools and act.tool_name not in self.tools):
                # dynamic only use default config in module.
                conf = self.tools_conf.get(act.tool_name)
                tool = ToolFactory(act.tool_name, conf=conf,
                                   asyn=conf.use_async if conf else False)
                if isinstance(tool, Tool):
                    tool.reset()
                elif isinstance(tool, AsyncTool):
                    await tool.reset()
                tool_mapping[act.tool_name] = []
                self.tools[act.tool_name] = tool
            if act.tool_name not in tool_mapping:
                tool_mapping[act.tool_name] = []
            tool_mapping[act.tool_name].append(act)

        for tool_name, action in tool_mapping.items():
            span_name, run_type = trace.get_tool_name(tool_name, action)
            logger.info(f"create span={span_name}")
            with trace.span(span_name, run_type=run_type) as tool_span:
                # Execute action using browser tool and unpack all return values
                if isinstance(self.tools[tool_name], Tool):
                    observation, reward, terminated, _, info = self.tools[tool_name].step(
                        action)
                elif isinstance(self.tools[tool_name], AsyncTool):
                    observation, reward, terminated, _, info = await self.tools[tool_name].step(action)
                else:
                    logger.warning(
                        f"Unsupported tool type: {self.tools[tool_name]}")
                    continue

                observations.append(observation)
                for i, item in enumerate(action):
                    tool_output = ToolResultOutput(
                        tool_type=tool_name,
                        tool_name=item.tool_name,
                        data=observation.content,
                        origin_tool_call=ToolCall.from_dict({
                            "function": {
                                "name": item.action_name,
                                "arguments": item.params,
                            }
                        })
                    )
                    await self.outputs.add_output(tool_output)

            # Check if there's an exception in info
            if info.get("exception"):
                color_log(
                    f"Step {step} failed with exception: {info['exception']}", color=Color.red)
                msg = f"Step {step} failed with exception: {info['exception']}"
            logger.info(f"step: {step} finished by tool action: {action}.")
            log_ob = Observation(content='' if observation.content is None else observation.content,
                                 action_result=observation.action_result)
        return msg, reward, terminated

    def _get_step_span_id(self, step, cur_agent_name):
        key = (step, cur_agent_name)
        if key not in self.step_agent_counter:
            self.step_agent_counter[key] = 0
        else:
            self.step_agent_counter[key] += 1
        exp_index = self.step_agent_counter[key]

        return f"{self.task.id}_{step}_{cur_agent_name}_{exp_index}"
