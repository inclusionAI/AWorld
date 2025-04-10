# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import math
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Union

from pydantic import BaseModel

from aworld.core.agent.base import Agent, agent_executor, is_agent_by_name
from aworld.core.agent.swarm import Swarm
from aworld.core.common import ActionModel, Observation
from aworld.core.envs.tool import Tool, ToolFactory
from aworld.logs.util import color_log, logger


@dataclass
class TaskModel:
    name: str = uuid.uuid1().hex
    input: Any = None
    conf: Union[Dict[str, Any], BaseModel] = None
    tools: List[Tool] = field(default_factory=list)
    tool_names: List[str] = field(default_factory=list)
    swarm: Swarm = None
    agent: Agent = None


class Task(object):
    def __init__(
        self,
        task: TaskModel = None,
        agent: Agent = None,
        swarm: Swarm = None,
        name: str = uuid.uuid1().hex,
        input: Any = None,
        conf: Union[Dict[str, Any], BaseModel] = {},
        tools: List[Tool] = [],
        tool_names: List[str] = [],
        *args,
        **kwargs,
    ):
        """Task instance init.

        Args:
            task: Task model
            agent: Agent instance which want to run.
            swarm: Swarm
            name: Task unique name
            input: A string query or dataset.
            conf: Task config in process.
            tools: Special tools in task run.
        """
        # Prioritize using the task model.
        if task:
            agent = task.agent
            swarm = task.swarm
            name = task.name
            input = task.input
            conf = task.conf
            tools = task.tools
            tool_names = task.tool_names
            if tools is None:
                tools = []
            if tool_names is None:
                tool_names = []

        if not agent and not swarm:
            raise ValueError("agent and swarm all is None.")
        if agent and swarm:
            raise ValueError("agent and swarm choose one only.")
        if agent:
            # uniform agent
            swarm = Swarm(agent)

        if conf is None:
            conf = dict()
        if isinstance(conf, BaseModel):
            conf = conf.model_dump()
        check_input = conf.get("check_input", False)
        if check_input and not input:
            raise ValueError

        self.swarm = swarm
        self.input = input
        self.name = name
        self.conf = conf
        self.tools = {tool.name(): tool for tool in tools} if tools else {}
        tool_names.extend(self.tools.keys())
        # lazy load
        self.tool_names = tool_names

        self.daemon_target = kwargs.pop("daemon_target", None)
        self._use_demon = False if not conf else conf.get("use_demon", False)
        self._exception = None
        for k, v in kwargs.items():
            setattr(self, k, v)

    def before_run(self):
        pass

    def after_run(self):
        pass

    def start(self) -> Any:
        try:
            self.before_run()
            self._daemon_run()
            ret = self.run()
            return 0 if ret is None else ret
        except BaseException as ex:
            self._exception = ex
            # do record or report
            raise ex
        finally:
            self.after_run()

    def _daemon_run(self) -> None:
        if self._use_demon and self.daemon_target and callable(self.daemon_target):
            import threading

            t = threading.Thread(target=self.daemon_target, name="daemon")
            t.setDaemon(True)
            t.start()

    def run(self):
        # init tool state by reset(), and ignore them observation
        observation = None
        info = dict()
        if self.tools:
            for _, tool in self.tools.items():
                # use the observation and info of the last one
                observation, info = tool.reset()

        if observation:
            if not observation.content:
                observation.content = self.input
        else:
            observation = Observation(content=self.input)

        self.swarm.reset(self.tool_names)
        for agent in self.swarm.agents.values():
            agent.reset(
                {
                    "task": observation.content,
                    "tool_names": agent.tool_names,
                    "agent_names": agent.handoffs,
                    "mcp_servers": agent.mcp_servers,
                }
            )
            # global tools
            agent.tool_names.extend(self.tool_names)

        if self.swarm.topology_type == "social":
            return self._social_process(observation, info)
        else:
            return self._sequence_process(observation, info)

    def _sequence_process(self, observation: Observation, info: Dict[str, Any]):
        if not observation:
            raise RuntimeError("no observation, check run process")

        start = time.time()
        step = 0
        max_steps = self.conf.get("max_steps", 100)
        msg = None

        for agent in self.swarm.ordered_agents:
            observations = [observation]
            policy = None
            while step < max_steps:
                terminated = False

                observation = self.swarm.action_to_observation(policy, observations)
                policy: List[ActionModel] = agent_executor.execute_agent(
                    observation, agent=agent, conf=agent.conf, step=step
                )
                if not policy:
                    logger.warning(f"current agent {agent.name()} no policy to use.")
                    return {
                        "msg": f"current agent {self.swarm.cur_agent.name()} no policy to use.",
                        "steps": step,
                        "success": False,
                        "time_cost": (time.time() - start),
                    }

                if self.tools is None:
                    self.tools = {}
                if self.is_agent(policy[0]):
                    # only one agent, and get agent from policy
                    policy_for_agent = policy[0]
                    cur_agent: Agent = self.swarm.agents.get(
                        policy_for_agent.agent_name
                    )
                    logger.critical(f"Execute agent: {cur_agent.name()}")
                    if not cur_agent:
                        raise RuntimeError(
                            f"Can not find {policy_for_agent.agent_name} agent in swarm."
                        )
                    if cur_agent.name() == agent.name():
                        # Current agent is entrance agent, means need to exit to the outer loop
                        logger.warning("Exit the loop")
                        observation = self.swarm.action_to_observation(
                            policy, observations
                        )
                        break

                    if (
                        agent.handoffs
                        and policy_for_agent.agent_name not in agent.handoffs
                    ):
                        # Unable to hand off, exit to the outer loop
                        return {
                            "msg": f"Can not handoffs {policy_for_agent.agent_name} agent "
                            f"by {cur_agent.name()} agent.",
                            "response": policy[0].policy_info if policy else "",
                            "steps": step,
                            "success": False,
                        }
                    # Check if current agent done
                    if cur_agent.finished:
                        cur_agent._finished = False
                        logger.info(
                            f"{cur_agent.name()} agent be be handed off, so finished state reset to False."
                        )

                    if observation:
                        observation.content = policy_for_agent.policy_info
                    else:
                        observation = Observation(content=policy_for_agent.policy_info)
                    policy = agent_executor.execute_agent(
                        observation, agent=cur_agent, conf=cur_agent.conf, step=step
                    )

                    if not policy:
                        logger.warning(
                            f"{observation} can not get the valid policy in {policy_for_agent.agent_name}, exit task!"
                        )
                        msg = f"{policy_for_agent.agent_name} invalid policy"
                        break
                    # clear observation
                    observation = None
                else:
                    # group action by tool name
                    tool_mapping = dict()
                    # Directly use or use tools after creation.
                    for act in policy:
                        if not self.tools or (
                            self.tools and act.tool_name not in self.tools
                        ):
                            # dynamic only use default config in module.
                            tool = ToolFactory(act.tool_name)
                            tool.reset()
                            tool_mapping[act.tool_name] = []
                            self.tools[act.tool_name] = tool
                        if act.tool_name not in tool_mapping:
                            tool_mapping[act.tool_name] = []
                        tool_mapping[act.tool_name].append(act)

                    for tool_name, action in tool_mapping.items():
                        # Execute action using browser tool and unpack all return values
                        logger.critical(f"Execute tool: {tool_name}-{action}")
                        observation, reward, terminated, _, info = self.tools[
                            tool_name
                        ].step(action)
                        observations.append(observation)

                        logger.info(f"{action} state: {observation}; reward: {reward}")
                        # Check if there's an exception in info
                        if info.get("exception"):
                            color_log(
                                f"Step {step} failed with exception: {info['exception']}"
                            )
                            msg = f"Step {step} failed with exception: {info['exception']}"
                        logger.info(f"step: {step} finished by tool action.")
                step += 1
                if terminated and agent.finished:
                    logger.info("swarm finished")
                    break
        return {
            "steps": step,
            "answer": observation.content,
            "observation": observation,
            "msg": msg,
            "success": True if not msg else False,
        }

    def _social_process(
        self, observation: Observation, info: Dict[str, Any]
    ) -> Dict[str, Any]:
        start = time.time()

        step = 0
        max_steps = self.conf.get("max_steps", 100)
        results = []
        swarm_resp = None
        try:
            while step < max_steps:
                # Loose protocol
                result_dict = self._process(observation=observation, info=info)
                results.append(result_dict)

                swarm_resp = result_dict.get("response")
                logger.info(f"Step: {step} response:\n {swarm_resp}")

                step += 1
                if self.swarm.finished:
                    logger.info("task done!")
                    break

                if not swarm_resp:
                    logger.warning(f"Step: {step} sarm no valid response")
                    break

                observation = result_dict.get("observation")
                if not observation:
                    observation = Observation(content=swarm_resp)
                else:
                    observation.content = swarm_resp

            time_cost = time.time() - start
            if not results:
                logger.warning("task no result!")
                return {
                    "answer": "",
                    "traceback": traceback.format_exc(),
                    "steps": step,
                    "success": False,
                    "total_time": time_cost,
                }

            answer = (
                results[-1].get("observation").content
                if results[-1].get("observation")
                else swarm_resp
            )
            return {
                "answer": answer,
                "steps": step,
                "success": True,
                "total_time": (time.time() - start),
            }
        except Exception as e:
            logger.error(
                f"Task execution failed with error: {str(e)}\n{traceback.format_exc()}"
            )
            return {
                "msg": str(e),
                "traceback": traceback.format_exc(),
                "steps": step,
                "success": False,
                "total_time": (time.time() - start),
            }

    def is_agent(self, policy: ActionModel):
        return (
            policy.tool_name is None and policy.action_name is None
        ) or is_agent_by_name(policy.tool_name)

    def _process(self, observation, info) -> Dict[str, Any]:
        """Multi-agent general process workflow.

        NOTE: Use the agent‘s finished state to control the loop, so the agent must carefully set finished state.
        Args:
            observation: Observation based on env
            info: Extend info by env
        """
        if not self.swarm.initialized:
            raise RuntimeError("swarm needs to use `reset` to init first.")

        start = time.time()
        step = 0
        max_steps = self.conf.get("max_steps", 100)
        self.swarm.cur_agent = self.swarm.communicate_agent
        # use entry agent every time
        policy: List[ActionModel] = agent_executor.execute_agent(
            observation,
            agent=self.swarm.cur_agent,
            conf=self.swarm.cur_agent.conf,
            step=step,
        )
        # policy: List[ActionModel] = self.swarm.cur_agent.policy(observation, info)
        if not policy:
            logger.warning(
                f"current agent {self.swarm.cur_agent.name()} no policy to use."
            )
            return {
                "msg": f"current agent {self.swarm.cur_agent.name()} no policy to use.",
                "steps": step,
                "success": False,
                "time_cost": (time.time() - start),
            }

        if self.tools is None:
            self.tools = {}

        msg = None
        response = None
        return_entry = False
        cur_agent = None
        finished = False
        try:
            while step < max_steps:
                terminated = False
                if self.is_agent(policy[0]):
                    # only one agent, and get agent from policy
                    policy_for_agent = policy[0]
                    cur_agent: Agent = self.swarm.agents.get(
                        policy_for_agent.agent_name
                    )
                    if not cur_agent:
                        raise RuntimeError(
                            f"Can not find {policy_for_agent.agent_name} agent in swarm."
                        )
                    if cur_agent.name() == self.swarm.communicate_agent.name():
                        # Current agent is entrance agent, means need to exit to the outer loop
                        logger.warning("Exit to the outer loop")
                        return_entry = True
                        break

                    if (
                        self.swarm.cur_agent.handoffs
                        and policy_for_agent.agent_name
                        not in self.swarm.cur_agent.handoffs
                    ):
                        # Unable to hand off, exit to the outer loop
                        return {
                            "msg": f"Can not handoffs {policy_for_agent.agent_name} agent "
                            f"by {cur_agent.name()} agent.",
                            "response": policy[0].policy_info if policy else "",
                            "steps": step,
                            "success": False,
                        }
                    # Check if current agent done
                    if cur_agent.finished:
                        cur_agent._finished = False
                        logger.info(
                            f"{cur_agent.name()} agent be be handed off, so finished state reset to False."
                        )

                    observation = Observation(content=policy_for_agent.policy_info)
                    agent_policy = agent_executor.execute_agent(
                        observation, agent=cur_agent, conf=cur_agent.conf, step=step
                    )
                    # agent_policy = cur_agent.policy(observation, info=info)
                    if not agent_policy:
                        logger.warning(
                            f"{observation} can not get the valid policy in {policy_for_agent.agent_name}, exit task!"
                        )
                        msg = f"{policy_for_agent.agent_name} invalid policy"
                        break
                    self.swarm.cur_agent = cur_agent
                    policy = agent_policy
                    # clear observation
                    observation = None
                else:
                    # group action by tool name
                    tool_mapping = dict()
                    # Directly use or use tools after creation.
                    for act in policy:
                        if not self.tools or (
                            self.tools and act.tool_name not in self.tools
                        ):
                            # dynamic only use default config in module.
                            tool = ToolFactory(act.tool_name)
                            tool.reset()
                            tool_mapping[act.tool_name] = []
                            self.tools[act.tool_name] = tool
                        if act.tool_name not in tool_mapping:
                            tool_mapping[act.tool_name] = []
                        tool_mapping[act.tool_name].append(act)

                    for tool_name, action in tool_mapping.items():
                        # Execute action using browser tool and unpack all return values
                        logger.info(f"execute tool action: {tool_name}-{action}")
                        observation, reward, terminated, _, info = self.tools[
                            tool_name
                        ].step(action)

                        # Check if there's an exception in info
                        if info.get("exception"):
                            color_log(
                                f"Step {step} failed with exception: {info['exception']}"
                            )
                            msg = info.get("exception")
                        logger.info(f"step: {step} finished by tool action.")

                    # The tool results give itself, exit; give to other agents, continue
                    tmp_name = policy[0].agent_name
                    if (
                        self.swarm.cur_agent.name()
                        == self.swarm.communicate_agent.name()
                        and (
                            len(self.swarm.agents) == 1
                            or tmp_name is None
                            or self.swarm.cur_agent.name() == tmp_name
                        )
                    ):
                        return_entry = True
                        break
                    elif policy[0].agent_name:
                        policy_for_agent = policy[0]
                        cur_agent: Agent = self.swarm.agents.get(
                            policy_for_agent.agent_name
                        )
                        if not cur_agent:
                            raise RuntimeError(
                                f"Can not find {policy_for_agent.agent_name} agent in swarm."
                            )
                        if (
                            self.swarm.cur_agent.handoffs
                            and policy_for_agent.agent_name
                            not in self.swarm.cur_agent.handoffs
                        ):
                            # Unable to hand off, exit to the outer loop
                            return {
                                "msg": f"Can not handoffs {policy_for_agent.agent_name} agent "
                                f"by {cur_agent.name()} agent.",
                                "response": policy[0].policy_info if policy else "",
                                "steps": step,
                                "success": False,
                            }
                        # Check if current agent done
                        if cur_agent.finished:
                            cur_agent._finished = False
                            logger.info(
                                f"{cur_agent.name()} agent be be handed off, so finished state reset to False."
                            )

                step += 1
                if terminated and self.swarm.cur_agent.finished:
                    logger.info("swarm finished")
                    break

                if observation:
                    if cur_agent is None:
                        cur_agent = self.swarm.cur_agent
                    policy = agent_executor.execute_agent(
                        observation, agent=cur_agent, conf=cur_agent.conf, step=step
                    )

            if policy:
                response = (
                    policy[0].policy_info
                    if policy[0].policy_info
                    else policy[0].action_name
                )

            # All agents or tools have completed their tasks
            if all(agent.finished for _, agent in self.swarm.agents.items()) or (
                all(tool.finished for _, tool in self.tools.items())
                and len(self.swarm.agents) == 1
            ):
                logger.info("entry agent finished, swarm process finished.")
                finished = True

            if return_entry and not finished:
                # Return to the entrance, reset current agent finished state
                self.swarm.cur_agent._finished = False
            return {
                "steps": step,
                "response": response,
                "observation": observation,
                "msg": msg,
                "success": True if not msg else False,
            }
        except Exception as e:
            logger.error(
                f"Task execution failed with error: {str(e)}\n{traceback.format_exc()}"
            )
            return {
                "msg": str(e),
                "response": "",
                "traceback": traceback.format_exc(),
                "steps": step,
                "success": False,
            }
