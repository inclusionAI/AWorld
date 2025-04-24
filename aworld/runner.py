# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc
import asyncio
import time
import traceback
from typing import List, Dict, Any, Union

from aworld.config.conf import ToolConfig
from pydantic import BaseModel

from aworld.core.agent.base import Agent, is_agent_by_name
from aworld.core.agent.swarm import Swarm
from aworld.core.common import Observation, ActionModel
from aworld.core.envs.tool import ToolFactory, Tool, AsyncTool
from aworld.core.envs.tool_desc import is_tool_by_name
from aworld.core.task import Runner, Task
from aworld.logs.util import logger, color_log, Color
from aworld.utils.common import sync_exec, override_in_subclass


class Runners:

    @staticmethod
    def sync_run_task(task: Union[Task, List[Task]], parallel: bool = False):
        return sync_exec(Runners.run_task, task=task, parallel=parallel)

    @staticmethod
    async def run_task(task: Union[Task, List[Task]], parallel: bool = False):
        """Run tasks for some complex scenarios where agents cannot be directly used.

        Args:
            task: User task define.
            parallel: Whether to process multiple tasks in parallel.
        """
        import time
        start = time.time()

        if isinstance(task, Task):
            task = [task]

        res = {}
        if parallel:
            await Runners._parallel_run_in_local(task, res)
        else:
            await Runners._run_in_local(task, res)

        res['time_cost'] = time.time() - start
        return res

    @staticmethod
    def sync_run(
            input: str,
            agent: Agent = None,
            swarm: Swarm = None,
            tool_names: List[str] = []
    ):
        return sync_exec(Runners.run, input=input, agent=agent, swarm=swarm, tool_names=tool_names)

    @staticmethod
    async def run(
            input: str,
            agent: Agent = None,
            swarm: Swarm = None,
            tool_names: List[str] = []
    ):
        """Run agent directly with input and tool names.

        Args:
            input: User query.
            agent: An agent with AI model configured, prompts, tools, mcp servers and other agents.
            swarm: Multi-agent topo.
            tool_names: Tool name list.
        """
        if agent and swarm:
            raise ValueError("`agent` and `swarm` only choose one.")

        if not input:
            raise ValueError('`input` is empty.')

        if agent:
            agent.task = input
            swarm = Swarm(agent)

        task = Task(input=input, swarm=swarm, tool_names=tool_names)
        runner = Runners._choose_runner(task=task)
        res = await runner.run()
        logger.info(f"{input} execute finished, response: {res}")
        return res

    @staticmethod
    async def _parallel_run_in_local(tasks: List[Task], res):
        # also can use ProcessPoolExecutor
        parallel_tasks = []
        for t in tasks:
            parallel_tasks.append(Runners._choose_runner(task=t).run())

        results = await asyncio.gather(*parallel_tasks)
        for idx, t in enumerate(results):
            res[f'task_{idx}'] = t

    @staticmethod
    async def _run_in_local(tasks: List[Task], res: Dict[str, Any]) -> None:
        for idx, task in enumerate(tasks):
            # Execute the task
            result = await Runners._choose_runner(task=task).run()
            res[f'task_{idx}'] = result

    @staticmethod
    def _choose_runner(task: Task):
        if not task.swarm:
            return SequenceRunner(task=task)

        task.swarm.reset(task.input)
        topology = task.swarm.topology_type
        if topology == 'social':
            return SocialRunner(task=task)
        else:
            return SequenceRunner(task=task)


class TaskRunner(Runner):
    __metaclass__ = abc.ABCMeta

    def __init__(self, task: Task, *args, **kwargs):
        if task.tools is None:
            task.tools = []
        if task.tool_names is None:
            task.tool_names = []

        if not task.agent and not task.swarm:
            raise ValueError("agent and swarm all is None.")
        if task.agent and task.swarm:
            raise ValueError("agent and swarm choose one only.")
        if task.agent:
            # uniform agent
            task.swarm = Swarm(task.agent)

        if task.conf is None:
            task.conf = dict()
        if isinstance(task.conf, BaseModel):
            task.conf = task.conf.model_dump()
        check_input = task.conf.get("check_input", False)
        if check_input and not task.input:
            raise ValueError("task no input")

        self.swarm = task.swarm
        self.input = task.input
        self.name = task.name
        self.conf = task.conf
        self.tools = {tool.name(): tool for tool in task.tools} if task.tools else {}
        task.tool_names.extend(self.tools.keys())
        # lazy load
        self.tool_names = task.tool_names
        self.tools_conf = task.tools_conf
        if self.tools_conf is None:
            self.tools_conf = {}
        # mcp performs special process, use async only in the runn
        self.tools_conf['mcp'] = ToolConfig(use_async=True, name='mcp')
        self.endless_threshold = task.endless_threshold

        self.daemon_target = kwargs.pop('daemon_target', None)
        self._use_demon = False if not task.conf else task.conf.get('use_demon', False)
        self._exception = None
        for k, v in kwargs.items():
            setattr(self, k, v)

        # modules init

    async def pre_run(self):
        # init tool state by reset(), and ignore them observation
        observation = None
        if self.tools:
            for _, tool in self.tools.items():
                # use the observation and info of the last one
                if isinstance(tool, Tool):
                    observation, info = tool.reset()
                elif isinstance(tool, AsyncTool):
                    observation, info = await tool.reset()
                else:
                    logger.warning(f"Unsupported tool type: {tool}, will ignored.")

        if observation:
            if not observation.content:
                observation.content = self.input
        else:
            observation = Observation(content=self.input)

        self.observation = observation
        self.swarm.reset(observation.content, self.tool_names)

    def is_agent(self, policy: ActionModel):
        return is_agent_by_name(policy.tool_name) or (not policy.tool_name and not policy.action_name)


class SequenceRunner(TaskRunner):
    def __init__(self, task: Task, *args, **kwargs):
        super().__init__(task=task, *args, **kwargs)

    async def do_run(self):
        """Multi-agent sequence general process workflow.

        NOTE: Use the agent‘s finished state(no tool calls) to control the inner loop.
        Args:
            observation: Observation based on env
            info: Extend info by env
        """
        observation = self.observation
        if not observation:
            raise RuntimeError("no observation, check run process")

        start = time.time()
        step = 0
        max_steps = self.conf.get("max_steps", 100)
        msg = None

        for i in range(self.swarm.max_steps):
            for idx, agent in enumerate(self.swarm.ordered_agents):
                observations = [observation]
                policy = None
                cur_agent = agent
                while step < max_steps:
                    terminated = False

                    observation = self.swarm.action_to_observation(policy, observations)

                    if not override_in_subclass('async_policy', cur_agent.__class__, Agent):
                        policy: List[ActionModel] = cur_agent.policy(observation,
                                                                     step=step)
                    else:
                        policy: List[ActionModel] = await cur_agent.async_policy(observation,
                                                                                 step=step)
                    observation.content = None
                    color_log(f"{cur_agent.name()} policy: {policy}")
                    if not policy:
                        logger.warning(f"current agent {cur_agent.name()} no policy to use.")
                        return {"msg": f"current agent {cur_agent.name()} no policy to use.",
                                "steps": step,
                                "success": False,
                                "time_cost": (time.time() - start)}

                    if self.is_agent(policy[0]):
                        status, info = await self._agent(agent, observation, policy, step)
                        if status == 'normal':
                            if info:
                                observations.append(observation)
                        elif status == 'break':
                            observation = self.swarm.action_to_observation(policy, observations)
                            break
                        elif status == 'return':
                            return info
                    elif is_tool_by_name(policy[0].tool_name):
                        msg, terminated = await self._tool_call(policy, observations, step)
                    else:
                        logger.warning(f"Unrecognized policy: {policy[0]}")
                        return {"msg": f"Unrecognized policy: {policy[0]}, need to check prompt or agent / tool.",
                                "response": "",
                                "steps": step,
                                "success": False}
                    step += 1
                    if terminated and agent.finished:
                        logger.info("swarm finished")
                        break
        return {"steps": step,
                "answer": observation.content,
                "observation": observation,
                "msg": msg,
                "success": True if not msg else False}

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
            return status, {"msg": f"Can not handoffs {agent_name} agent "
                                   f"by {agent.name()} agent.",
                            "response": policy[0].policy_info if policy else "",
                            "steps": step,
                            "success": False}
        # Check if current agent done
        if cur_agent.finished:
            cur_agent._finished = False
            logger.info(f"{cur_agent.name()} agent be be handed off, so finished state reset to False.")

        con = policy_for_agent.policy_info
        if policy_for_agent.params and 'content' in policy_for_agent.params:
            con = policy_for_agent.params['content']
        if observation:
            observation.content = con
        else:
            observation = Observation(content=con)
            return status, observation
        return status, None

    async def _tool_call(self, policy: List[ActionModel], observations: List[Observation], step: int):
        msg = None
        terminated = False
        # group action by tool name
        tool_mapping = dict()
        # Directly use or use tools after creation.
        for act in policy:
            if not self.tools or (self.tools and act.tool_name not in self.tools):
                # dynamic only use default config in module.
                conf = self.tools_conf.get(act.tool_name)
                tool = ToolFactory(act.tool_name, conf=conf, asyn=conf.use_async if conf else False)
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
            # Execute action using browser tool and unpack all return values
            if isinstance(self.tools[tool_name], Tool):
                observation, reward, terminated, _, info = self.tools[tool_name].step(action)
            elif isinstance(self.tools[tool_name], AsyncTool):
                observation, reward, terminated, _, info = await self.tools[tool_name].step(action)
            else:
                logger.warning(f"Unsupported tool type: {self.tools[tool_name]}")
                continue

            observations.append(observation)

            logger.info(f'{action} state: {observation}; reward: {reward}')
            # Check if there's an exception in info
            if info.get("exception"):
                color_log(f"Step {step} failed with exception: {info['exception']}", color=Color.red)
                msg = f"Step {step} failed with exception: {info['exception']}"
            logger.info(f"step: {step} finished by tool action.")
            log_ob = Observation(content='' if observation.content is None else observation.content,
                                 action_result=observation.action_result)
            color_log(f"{tool_name} observation: {log_ob}", color=Color.green)
        return msg, terminated


class SocialRunner(TaskRunner):
    def __init__(self, task: Task, *args, **kwargs):
        super().__init__(task=task, *args, **kwargs)

    async def do_run(self) -> Dict[str, Any]:
        """Multi-agent general process workflow.

        NOTE: Use the agent‘s finished state to control the loop, so the agent must carefully set finished state.
        Args:
            observation: Observation based on env
            info: Extend info by env
        """
        start = time.time()

        observation = self.observation
        info = dict()
        step = 0
        max_steps = self.conf.get("max_steps", 100)
        results = []
        swarm_resp = None
        self.loop_detect = []
        try:
            while step < max_steps:
                # Loose protocol
                result_dict = await self._process(observation=observation, info=info)
                results.append(result_dict)

                swarm_resp = result_dict.get("response")
                logger.info(f"Step: {step} response:\n {result_dict}")

                step += 1
                if self.swarm.finished or self._loop_detect():
                    logger.info("task done!")
                    break

                if not swarm_resp:
                    logger.warning(f"Step: {step} swarm no valid response")
                    break

                observation = result_dict.get("observation")
                if not observation:
                    observation = Observation(content=swarm_resp)
                else:
                    observation.content = swarm_resp

            time_cost = time.time() - start
            if not results:
                logger.warning("task no result!")
                return {"answer": "",
                        "traceback": traceback.format_exc(),
                        "steps": step,
                        "success": False,
                        "total_time": time_cost}

            answer = results[-1].get('observation').content if results[-1].get('observation') else swarm_resp
            return {"answer": answer,
                    "steps": step,
                    "success": True,
                    "total_time": (time.time() - start)}
        except Exception as e:
            logger.error(f"Task execution failed with error: {str(e)}\n{traceback.format_exc()}")
            return {"msg": str(e),
                    "traceback": traceback.format_exc(),
                    "steps": step,
                    "success": False,
                    "total_time": (time.time() - start)}

    async def _process(self, observation, info) -> Dict[str, Any]:
        if not self.swarm.initialized:
            raise RuntimeError("swarm needs to use `reset` to init first.")

        start = time.time()
        step = 0
        max_steps = self.conf.get("max_steps", 100)
        self.swarm.cur_agent = self.swarm.communicate_agent
        # use communicate agent every time
        if override_in_subclass('async_policy', self.swarm.cur_agent.__class__, Agent):
            policy: List[ActionModel] = self.swarm.cur_agent.policy(observation,
                                                                    step=step)
        else:
            policy: List[ActionModel] = await self.swarm.cur_agent.async_policy(observation,
                                                                                step=step)
        if not policy:
            logger.warning(f"current agent {self.swarm.cur_agent.name()} no policy to use.")
            return {"msg": f"current agent {self.swarm.cur_agent.name()} no policy to use.",
                    "steps": step,
                    "success": False,
                    "time_cost": (time.time() - start)}
        color_log(f"{self.swarm.cur_agent.name()} policy: {policy}")

        msg = None
        response = None
        return_entry = False
        cur_agent = None
        finished = False
        try:
            while step < max_steps:
                terminated = False
                if self.is_agent(policy[0]):
                    status, info = await self._social_agent(policy, step)
                    if status == 'normal':
                        self.swarm.cur_agent = self.swarm.agents.get(policy[0].agent_name)
                        policy = info
                    # clear observation
                    observation = None
                elif is_tool_by_name(policy[0].tool_name):
                    status, terminated, info = await self._social_tool_call(policy, step)
                    if status == 'normal':
                        observation = info
                else:
                    logger.warning(f"Unrecognized policy: {policy[0]}")
                    return {"msg": f"Unrecognized policy: {policy[0]}, need to check prompt or agent / tool.",
                            "response": "",
                            "steps": step,
                            "success": False}

                if status == 'break':
                    return_entry = info
                    break
                elif status == 'return':
                    return info

                step += 1
                if terminated and self.swarm.cur_agent.finished:
                    logger.info(f"{self.swarm.cur_agent.name()} finished")
                    break

                if observation:
                    if cur_agent is None:
                        cur_agent = self.swarm.cur_agent
                    if not override_in_subclass('async_policy', cur_agent.__class__, Agent):
                        policy = cur_agent.policy(observation, step=step)
                    else:
                        policy = await cur_agent.async_policy(observation, step=step)
                    color_log(f"{cur_agent.name()} policy: {policy}")

            if policy:
                response = policy[0].policy_info if policy[0].policy_info else policy[0].action_name

            # All agents or tools have completed their tasks
            if all(agent.finished for _, agent in self.swarm.agents.items()) or (all(
                    tool.finished for _, tool in self.tools.items()) and len(self.swarm.agents) == 1):
                logger.info("entry agent finished, swarm process finished.")
                finished = True

            if return_entry and not finished:
                # Return to the entrance, reset current agent finished state
                self.swarm.cur_agent._finished = False
            return {"steps": step,
                    "response": response,
                    "observation": observation,
                    "msg": msg,
                    "success": True if not msg else False}
        except Exception as e:
            logger.error(f"Task execution failed with error: {str(e)}\n{traceback.format_exc()}")
            return {
                "msg": str(e),
                "response": "",
                "traceback": traceback.format_exc(),
                "steps": step,
                "success": False
            }

    async def _social_agent(self, policy: List[ActionModel], step):
        # only one agent, and get agent from policy
        policy_for_agent = policy[0]
        agent_name = policy_for_agent.agent_name
        if not agent_name:
            agent_name = policy_for_agent.tool_name
        cur_agent: Agent = self.swarm.agents.get(agent_name)
        if not cur_agent:
            raise RuntimeError(f"Can not find {agent_name} agent in swarm.")

        if cur_agent.name() == self.swarm.communicate_agent.name() or cur_agent.name() == self.swarm.cur_agent.name():
            # Current agent is entrance agent, means need to exit to the outer loop
            logger.info(f"{cur_agent.name()} exit to the outer loop")
            self.loop_detect.append(cur_agent.name())
            return 'break', True

        if self.swarm.cur_agent.handoffs and agent_name not in self.swarm.cur_agent.handoffs:
            # Unable to hand off, exit to the outer loop
            return "return", {"msg": f"Can not handoffs {agent_name} agent "
                                     f"by {cur_agent.name()} agent.",
                              "response": policy[0].policy_info if policy else "",
                              "steps": step,
                              "success": False}
        # Check if current agent done
        if cur_agent.finished:
            cur_agent._finished = False
            logger.info(f"{cur_agent.name()} agent be be handed off, so finished state reset to False.")

        observation = Observation(content=policy_for_agent.policy_info)
        self.loop_detect.append(cur_agent.name())
        if cur_agent.step_reset:
            cur_agent.reset({"task": observation.content,
                             "tool_names": cur_agent.tool_names,
                             "agent_names": cur_agent.handoffs,
                             "mcp_servers": cur_agent.mcp_servers})

        if not override_in_subclass('async_policy', cur_agent.__class__, Agent):
            agent_policy = cur_agent.policy(observation,
                                            step=step)
        else:
            agent_policy = await cur_agent.async_policy(observation,
                                                        step=step)

        if not agent_policy:
            logger.warning(
                f"{observation} can not get the valid policy in {policy_for_agent.agent_name}, exit task!")
            return "return", {"msg": f"{policy_for_agent.agent_name} invalid policy",
                              "response": "",
                              "steps": step,
                              "success": False}
        color_log(f"{cur_agent.name()} policy: {agent_policy}")
        return 'normal', agent_policy

    async def _social_tool_call(self, policy: List[ActionModel], step: int):
        observation = None
        terminated = False
        # group action by tool name
        tool_mapping = dict()
        # Directly use or use tools after creation.
        for act in policy:
            if not self.tools or (self.tools and act.tool_name not in self.tools):
                # dynamic only use default config in module.
                conf: ToolConfig = self.tools_conf.get(act.tool_name)
                tool = ToolFactory(act.tool_name, conf=conf, asyn=conf.use_async if conf else False)
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
            # Execute action using browser tool and unpack all return values
            if isinstance(self.tools[tool_name], Tool):
                observation, reward, terminated, _, info = self.tools[tool_name].step(action)
            elif isinstance(self.tools[tool_name], AsyncTool):
                observation, reward, terminated, _, info = await self.tools[tool_name].step(action)
            else:
                logger.warning(f"Unsupported tool type: {self.tools[tool_name]}")
                continue

            # Check if there's an exception in info
            if info.get("exception"):
                color_log(f"Step {step} failed with exception: {info['exception']}", color=Color.red)
            logger.info(f"step: {step} finished by tool action {action}.")
            log_ob = Observation(content='' if observation.content is None else observation.content,
                                 action_result=observation.action_result)
            color_log(f"{tool_name} observation: {log_ob}", color=Color.green)

        # The tool results give itself, exit; give to other agents, continue
        tmp_name = policy[0].agent_name
        if self.swarm.cur_agent.name() == self.swarm.communicate_agent.name() and (
                len(self.swarm.agents) == 1 or tmp_name is None or self.swarm.cur_agent.name() == tmp_name):
            return "break", terminated, True
        elif policy[0].agent_name:
            policy_for_agent = policy[0]
            agent_name = policy_for_agent.agent_name
            if not agent_name:
                agent_name = policy_for_agent.tool_name
            cur_agent: Agent = self.swarm.agents.get(agent_name)
            if not cur_agent:
                raise RuntimeError(f"Can not find {agent_name} agent in swarm.")
            if self.swarm.cur_agent.handoffs and agent_name not in self.swarm.cur_agent.handoffs:
                # Unable to hand off, exit to the outer loop
                return "return", {"msg": f"Can not handoffs {agent_name} agent "
                                         f"by {cur_agent.name()} agent.",
                                  "response": policy[0].policy_info if policy else "",
                                  "steps": step,
                                  "success": False}
            # Check if current agent done
            if cur_agent.finished:
                cur_agent._finished = False
                logger.info(f"{cur_agent.name()} agent be be handed off, so finished state reset to False.")
        return "normal", terminated, observation

    def _loop_detect(self):
        if not self.loop_detect:
            return False

        threshold = self.endless_threshold
        last_agent_name = self.swarm.communicate_agent.name()
        count = 1
        for i in range(len(self.loop_detect) - 2, -1, -1):
            if last_agent_name == self.loop_detect[i]:
                count += 1
            else:
                last_agent_name = self.loop_detect[i]
                count = 1

            if count >= threshold:
                logger.warning("detect loop, will exit the loop.")
                return True

        if len(self.loop_detect) > 6:
            last_agent_name = None
            # latest
            for j in range(1, 3):
                for i in range(len(self.loop_detect) - j, 0, -2):
                    if last_agent_name and last_agent_name == (self.loop_detect[i], self.loop_detect[i - 1]):
                        count += 1
                    elif last_agent_name is None:
                        last_agent_name = (self.loop_detect[i], self.loop_detect[i - 1])
                        count = 1
                    else:
                        last_agent_name = None
                        break

                    if count >= threshold:
                        logger.warning(f"detect loop: {last_agent_name}, will exit the loop.")
                        return True

        return False
