# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import copy
import json
import time
import traceback
from typing import Dict, Any, List, Union

from aworld.config.common import Agents
from aworld.core.agent.base import Agent, AgentFactory
from aworld.models.utils import tool_desc_transform
from aworld.models.llm import call_llm_model
from aworld.config.conf import AgentConfig, ConfigDict
from aworld.core.common import Observation, ActionModel
from aworld.logs.util import logger
from aworld.core.envs.tool_desc import get_tool_desc
from aworld.agents.gaia.prompts import *
from aworld.agents.gaia.utils import extract_pattern


@AgentFactory.register(name=Agents.EXECUTE.value, desc="execute agent")
class ExecuteAgent(Agent):
    def __init__(self, conf: Union[Dict[str, Any], ConfigDict, AgentConfig], **kwargs):
        super(ExecuteAgent, self).__init__(conf, **kwargs)

    def reset(self, options: Dict[str, Any]):
        """Execute agent reset need query task as input."""
        super().reset(options)

        self.system_prompt = execute_system_prompt.format(task=self.task)
        self.step_reset = False

    def policy(self,
               observation: Observation,
               info: Dict[str, Any] = None,
               **kwargs) -> List[ActionModel] | None:
        start_time = time.time()
        self.desc_transform()
        content = observation.content

        llm_result = None
        ## build input of llm
        input_content = [
            {'role': 'system', 'content': self.system_prompt},
        ]
        for traj in self.trajectory:
            input_content.append(traj[0].content)
            if traj[-1].tool_calls is not None:
                input_content.append(
                    {'role': 'assistant', 'content': '', 'tool_calls': traj[-1].tool_calls})
            else:
                input_content.append({'role': 'assistant', 'content': traj[-1].content})

        if content is None:
            content = observation.action_result[0].error
        if not self.trajectory:
            message = {'role': 'user', 'content': content}
        else:
            tool_id = None
            if self.trajectory[-1][-1].tool_calls:
                tool_id = self.trajectory[-1][-1].tool_calls[0].id
            if tool_id:
                message = {'role': 'tool', 'content': content, 'tool_call_id': tool_id}
            else:
                message = {'role': 'user', 'content': content}
        input_content.append(message)

        tool_calls = []
        try:
            llm_result = call_llm_model(self.llm, input_content, model=self.model_name,
                                        tools=self.tools, temperature=0)
            logger.info(f"Execute response: {llm_result.message}")
            res = self.response_parse(llm_result)
            content = res.actions[0].policy_info
            tool_calls = llm_result.tool_calls
        except Exception as e:
            logger.warn(traceback.format_exc())
            raise e
        finally:
            if llm_result:
                ob = copy.deepcopy(observation)
                ob.content = message
                self.trajectory.append((ob, info, llm_result))
            else:
                logger.warn("no result to record!")

        res = []
        if tool_calls:
            for tool_call in tool_calls:
                tool_action_name: str = tool_call.function.name
                if not tool_action_name:
                    continue

                names = tool_action_name.split("__")
                tool_name = names[0]
                action_name = '__'.join(names[1:]) if len(names) > 1 else ''
                params = json.loads(tool_call.function.arguments)
                res.append(ActionModel(tool_name=tool_name, action_name=action_name, params=params))

        if res:
            res[0].policy_info = content
            self._finished = False
        elif content:
            policy_info = extract_pattern(content, "final_answer")
            if policy_info:
                res.append(ActionModel(agent_name=Agents.PLAN.value, policy_info=policy_info))
                self._finished = True
            else:
                res.append(ActionModel(agent_name=Agents.PLAN.value, policy_info=content))

        logger.info(f">>> execute result: {res}")
        return res


@AgentFactory.register(name=Agents.PLAN.value, desc="plan agent")
class PlanAgent(Agent):
    def __init__(self, conf: Union[Dict[str, Any], ConfigDict, AgentConfig], **kwargs):
        super(PlanAgent, self).__init__(conf, **kwargs)

    def reset(self, options: Dict[str, Any]):
        """Execute agent reset need query task as input."""
        super().reset(options)

        self.system_prompt = plan_system_prompt.format(task=self.task)
        self.done_prompt = plan_done_prompt.format(task=self.task)
        self.postfix_prompt = plan_postfix_prompt.format(task=self.task)
        self.first_prompt = init_prompt
        self.first = True
        self.step_reset = False

    def policy(self,
               observation: Observation,
               info: Dict[str, Any] = None,
               **kwargs) -> List[ActionModel] | None:
        llm_result = None
        self.desc_transform()
        input_content = [
            {'role': 'system', 'content': self.system_prompt},
        ]
        # build input of llm based history
        for traj in self.trajectory:
            input_content.append({'role': 'user', 'content': traj[0].content})
            if traj[-1].tool_calls is not None:
                input_content.append(
                    {'role': 'assistant', 'content': '', 'tool_calls': traj[-1].serialize_tool_calls()})
            else:
                input_content.append({'role': 'assistant', 'content': traj[-1].content})

        message = observation.content
        if self.first_prompt:
            message = self.first_prompt
            self.first_prompt = None

        input_content.append({'role': 'user', 'content': message})
        try:
            llm_result = call_llm_model(self.llm, messages=input_content, model=self.model_name)
            logger.info(f"Plan response: {llm_result.message}")
        except Exception as e:
            logger.warn(traceback.format_exc())
            raise e
        finally:
            if llm_result:
                ob = copy.deepcopy(observation)
                ob.content = message
                self.trajectory.append((ob, info, llm_result))
            else:
                logger.warn("no result to record!")
        res = self.response_parse(llm_result)
        content = res.actions[0].policy_info
        if "TASK_DONE" not in content:
            content += self.done_prompt
        else:
            # The task is done, and the assistant agent need to give the final answer about the original task
            content += self.postfix_prompt
            if not self.first:
                self._finished = True

        self.first = False
        logger.info(f">>> plan result: {content}")
        return [ActionModel(agent_name=Agents.EXECUTE.value,
                            policy_info=content)]
