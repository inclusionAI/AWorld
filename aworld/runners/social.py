# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import time
import traceback

from typing import List, Dict, Any

from aworld.config.conf import ToolConfig
from aworld.core.agent.base import Agent
from aworld.core.common import Observation, ActionModel
from aworld.core.context.base import Context
from aworld.core.envs.tool import ToolFactory, Tool, AsyncTool
from aworld.core.envs.tool_desc import is_tool_by_name
from aworld.core.task import Task
from aworld.logs.util import logger, color_log, Color, trace_logger
from aworld.models.model_response import ToolCall
from aworld.output.base import StepOutput, ToolResultOutput
from aworld.runners.task_runner import TaskRunner
from aworld.utils.common import override_in_subclass


class SocialRunner(TaskRunner):
    def __init__(self, task: Task, *args, **kwargs):
        super().__init__(task=task, *args, **kwargs)

    async def do_run(self, context: Context = None) -> Dict[str, Any]:
        """Multi-agent general process workflow.

        NOTE: Use the agent's finished state to control the loop, so the agent must carefully set finished state.

        Args:
            context: Context of runner.
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
        finally:
            for _, tool in self.tools.items():
                if isinstance(tool, AsyncTool):
                    await tool.close()
                else:
                    tool.close()

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
                                                                    step=step,
                                                                    outputs=self.outputs,
                                                                    stream=self.conf.get("stream", False))
        else:
            policy: List[ActionModel] = await self.swarm.cur_agent.async_policy(observation,
                                                                                step=step,
                                                                                outputs=self.outputs,
                                                                                stream=self.conf.get("stream", False))
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
                        policy = cur_agent.policy(observation,
                                                  step=step,
                                                  outputs=self.outputs,
                                                  stream=self.conf.get("stream", False))
                    else:
                        policy = await cur_agent.async_policy(observation,
                                                              step=step,
                                                              outputs=self.outputs,
                                                              stream=self.conf.get("stream", False))
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
                                            step=step,
                                            outputs=self.outputs,
                                            stream=self.conf.get("stream", False))
        else:
            agent_policy = await cur_agent.async_policy(observation,
                                                        step=step,
                                                        outputs=self.outputs,
                                                        stream=self.conf.get("stream", False))

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

            for i, item in enumerate(action):
                tool_output = ToolResultOutput(data=observation.content, origin_tool_call=ToolCall.from_dict({
                    "function": {
                        "name": item.action_name,
                        "arguments": item.params,
                    }
                }))
                await self.outputs.add_output(tool_output)

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
