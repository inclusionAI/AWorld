# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import time
import traceback
from typing import Dict, Any, List

from aworld.core.agent.base import Agent, BaseAgent
from aworld.core.envs.tool import ToolFactory
from aworld.core.mcp.tools import MCPToolExecutor
from aworld.logs.util import logger, color_log
from aworld.core.common import Observation, ActionModel
from aworld.config.conf import ToolConfig, load_config
from aworld.core.envs.tool_desc import get_actions_by_tools


class Swarm(object):
    """Simple implementation of interactive collaboration between multi-agent and interaction with env tools."""

    def __init__(self, *args, **kwargs):
        valid_agent_pair = []
        for pair in args:
            if isinstance(pair, (list, tuple)):
                if len(pair) != 2:
                    logger.warning(f"{pair} is not a pair value, ignore it.")
                    continue
                else:
                    if not isinstance(pair[0], BaseAgent) or not isinstance(pair[1], BaseAgent):
                        logger.warning(f"agent in {pair} is not a base agent instance, ignore it.")
                        continue
                    valid_agent_pair.append(pair)
            else:
                if not isinstance(pair, BaseAgent):
                    logger.warning(f"agent {pair} is not a base agent instance, ignore it.")
                    continue
                # only one agent, build itself pair
                valid_agent_pair.append((pair, pair))

        self.initialized = False
        if not valid_agent_pair:
            logger.warning("no valid agent pair to build graph.")
            return

        self.conf = kwargs
        # entrance agent
        self.entry_agent: Agent = valid_agent_pair[0][0]
        # agents in swarm
        self.agents = {}

        for pair in valid_agent_pair:
            if pair[0] not in self.agents:
                self.agents[pair[0].name()] = pair[0]
            if pair[1] not in self.agents:
                self.agents[pair[1].name()] = pair[1]

            pair[0].handoffs.append(pair[1].name())
        self.finished = False

    def reset(self, tools):
        if not self.agents:
            logger.warning("No valid agent in swarm.")
            return

        # can only use the special tools in the swarm as a global
        self.tools_actions = get_actions_by_tools(tools)
        self.tools = tools
        self.cur_agent = self.entry_agent
        self.initialized = True

    def is_agent(self, policy: ActionModel):
        return policy.tool_name is None and policy.action_name is None

    def process(self, observation, info) -> Dict[str, Any]:
        """Multi-agent general process workflow.

        NOTE: Use the agent‘s finished state to control the loop, so the agent must carefully set finished state.
        Args:
            observation: Observation based on env
            info: Extend info by env
        """
        if not self.initialized:
            raise RuntimeError("swarm needs to use `reset` to init first.")

        start = time.time()
        step = 0
        max_steps = self.conf.get("max_steps", 100)
        # use entry agent every time
        self.cur_agent = self.entry_agent
        policy: List[ActionModel] = self.cur_agent.policy(observation, info)
        if not policy:
            logger.warning(f"current agent {self.cur_agent.name()} no policy to use.")
            return {"msg": f"current agent {self.cur_agent.name()} no policy to use.",
                    "steps": step,
                    "success": False,
                    "time_cost": (time.time() - start)}

        if self.tools is None:
            self.tools = {}

        msg = None
        response = None
        return_entry = False
        try:
            while step < max_steps:
                terminated = False
                if self.is_agent(policy[0]):
                    # only one agent, and get agent from policy
                    policy_for_agent = policy[0]
                    cur_agent: BaseAgent = self.agents.get(policy_for_agent.agent_name)
                    if not cur_agent:
                        raise RuntimeError(f"Can not find {policy_for_agent.agent_name} agent in swarm.")
                    if cur_agent.name() == self.entry_agent.name():
                        # Current agent is entrance agent, means need to exit to the outer loop
                        logger.warning("Exit to the outer loop")
                        return_entry = True
                        break

                    if self.cur_agent.handoffs and policy_for_agent.agent_name not in self.cur_agent.handoffs:
                        # Unable to hand off, exit to the outer loop
                        return {"msg": f"Can not handoffs {policy_for_agent.agent_name} agent "
                                       f"by {cur_agent.name()} agent.",
                                "response": policy[0].policy_info if policy else "",
                                "steps": step,
                                "success": False}
                    # Check if current agent done
                    if cur_agent.finished:
                        cur_agent._finished = False
                        logger.info(f"{cur_agent.name()} agent be be handed off, so finished state reset to False.")

                    observation = Observation(content=policy_for_agent.policy_info)
                    agent_policy = cur_agent.policy(observation, info=info)
                    if not agent_policy:
                        logger.warning(
                            f"{observation} can not get the valid policy in {policy_for_agent.agent_name}, exit task!")
                        msg = f"{policy_for_agent.agent_name} invalid policy"
                        break
                    self.cur_agent = cur_agent
                    policy = agent_policy
                    # clear observation
                    observation = None
                else:
                    # group action by tool name
                    tool_mapping = dict()
                    # Directly use or use tools after creation.
                    for act in policy:
                        if not self.tools or (self.tools and act.tool_name not in self.tools):
                            # dynamic only use default config in module.
                            if not act.is_mcp:
                                tool = ToolFactory(act.tool_name)
                                tool.reset()
                                tool_mapping[act.tool_name] = []
                                self.tools[act.tool_name] = tool
                        if act.tool_name not in tool_mapping:
                            tool_mapping[act.tool_name] = []
                        tool_mapping[act.tool_name].append(act)

                    for tool_name, action in tool_mapping.items():
                        # Execute action using browser tool and unpack all return values
                        if action and hasattr(action[0], 'is_mcp') and action[0].is_mcp:
                            mcp_executor = MCPToolExecutor()
                            # Initialize the MCPToolExecutor before calling step
                            mcp_executor._load_mcp_config()
                            observation, reward, terminated, _, info = mcp_executor.step(action)
                        else:
                           observation, reward, terminated, _, info = self.tools[tool_name].step(action)

                        logger.info(f'{action} state: {observation}; reward: {reward}')
                        # Check if there's an exception in info
                        if info.get("exception"):
                            color_log(f"Step {step} failed with exception: {info['exception']}")
                            msg = info.get("exception")
                        logger.info(f"step: {step} finished by tool action.")

                    # The tool results give itself, exit; give to other agents, continue
                    tmp_name = policy[0].agent_name
                    if self.cur_agent.name() == self.entry_agent.name() and (
                            len(self.agents) == 1 or tmp_name is None or self.cur_agent.name() == tmp_name):
                        return_entry = True
                        break
                    elif policy[0].agent_name:
                        policy_for_agent = policy[0]
                        cur_agent: BaseAgent = self.agents.get(policy_for_agent.agent_name)
                        if not cur_agent:
                            raise RuntimeError(f"Can not find {policy_for_agent.agent_name} agent in swarm.")
                        if self.cur_agent.handoffs and policy_for_agent.agent_name not in self.cur_agent.handoffs:
                            # Unable to hand off, exit to the outer loop
                            return {"msg": f"Can not handoffs {policy_for_agent.agent_name} agent "
                                           f"by {cur_agent.name()} agent.",
                                    "response": policy[0].policy_info if policy else "",
                                    "steps": step,
                                    "success": False}
                        # Check if current agent done
                        if cur_agent.finished:
                            cur_agent._finished = False
                            logger.info(f"{cur_agent.name()} agent be be handed off, so finished state reset to False.")

                step += 1
                if terminated and self.cur_agent.finished:
                    logger.info("swarm finished")
                    break

                if observation:
                    if cur_agent is None:
                        cur_agent = self.cur_agent
                    policy = cur_agent.policy(observation, info)

            if policy:
                response = policy[0].policy_info if policy[0].policy_info else policy[0].action_name

            # All agents or tools have completed their tasks
            if all(agent.finished for _, agent in self.agents.items()) or (all(
                    tool.finished for _, tool in self.tools.items()) and len(self.agents) == 1):
                logger.info("entry agent finished, swarm process finished.")
                self.finished = True

            if return_entry:
                # Return to the entrance, reset current agent finished state
                self.cur_agent._finished = False
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

    @property
    def is_finished(self) -> bool:
        return self.finished
