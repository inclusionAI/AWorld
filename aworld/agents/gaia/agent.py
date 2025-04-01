# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import copy
import json
import time
import traceback
from typing import Dict, Any, List

from aworld.core.agent.base import BaseAgent, AgentFactory
from aworld.models.utils import tool_desc_transform
from aworld.config.conf import AgentConfig
from aworld.core.common import Observation, ActionModel, Agents, Tools
from aworld.logs.util import logger
from aworld.core.envs.tool_desc import get_tool_desc
from aworld.agents.gaia.prompts import *
from aworld.agents.gaia.utils import extract_pattern
from aworld.utils.diagnostic_tools import Diagnostic


@AgentFactory.register(name=Agents.EXECUTE.value, desc="execute agent")
class ExecuteAgent(BaseAgent):
    def __init__(self, conf: AgentConfig, **kwargs):
        super(ExecuteAgent, self).__init__(conf, **kwargs)
        self.has_summary = False
        self.tools = tool_desc_transform(get_tool_desc(),
                                         tools=self.tool_names if self.tool_names else [])

    def name(self) -> str:
        return Agents.EXECUTE.value

    def reset(self, options: Dict[str, Any]):
        """Execute agent reset need query task as input."""
        self.task = options.get("task")
        self.trajectory = []
        self.system_prompt = execute_system_prompt.format(task=self.task)

    @Diagnostic()
    def policy(self,
               observation: Observation,
               info: Dict[str, Any] = None,
               **kwargs) -> List[ActionModel] | None:
        start_time = time.time()
        content = observation.content

        llm_result = None
        ## build input of llm
        input_content = [
            {'role': 'system', 'content': self.system_prompt},
        ]
        for traj in self.trajectory:
            input_content.append(traj[0].content)
            if traj[-1].choices[0].message.tool_calls is not None:
                input_content.append(
                    {'role': 'assistant', 'content': '', 'tool_calls': traj[-1].choices[0].message.tool_calls})
            else:
                input_content.append({'role': 'assistant', 'content': traj[-1].choices[0].message.content})

        if content is None:
            content = observation.action_result[0].error
        if not self.trajectory:
            message = {'role': 'user', 'content': content}
        else:
            tool_id = None
            if self.trajectory[-1][-1].choices[0].message.tool_calls:
                tool_id = self.trajectory[-1][-1].choices[0].message.tool_calls[0].id
            if tool_id:
                message = {'role': 'tool', 'content': content, 'tool_call_id': tool_id}
            else:
                message = {'role': 'user', 'content': content}
        input_content.append(message)

        tool_calls = []
        try:
            llm_result = self.llm.chat.completions.create(
                messages=input_content,
                model=self.model_name,
                **{'temperature': 0, 'tools': self.tools},
            )
            logger.info(f"Execute response: {llm_result.choices[0].message}")
            content = llm_result.choices[0].message.content
            tool_calls = llm_result.choices[0].message.tool_calls
        except Exception as e:
            logger.warn(traceback.format_exc())
            raise e
        finally:
            if llm_result:
                if llm_result.choices is None:
                    logger.info(f"llm result is None, info: {llm_result.model_extra}")
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
                tool_name = tool_action_name.split("__")[0]
                action_name = tool_action_name.split("__")[1]
                params = json.loads(tool_call.function.arguments)
                res.append(ActionModel(tool_name=tool_name, action_name=action_name, params=params))

        if res:
            res[0].policy_info = content
            self._finished = False
            self.has_summary = False
        elif content:
            if self.has_summary:
                policy_info = extract_pattern(content, "final_answer")
                if policy_info:
                    res.append(ActionModel(agent_name=Agents.PLAN.value, policy_info=policy_info))
                    self._finished = True
                else:
                    res.append(ActionModel(agent_name=Agents.PLAN.value, policy_info=content))
            else:
                res.append(ActionModel(agent_name=Agents.PLAN.value, policy_info=content))
                self.has_summary = True

        logger.info(f">>> execute result: {res}")
        return res


@AgentFactory.register(name=Agents.PLAN.value, desc="plan agent")
class PlanAgent(BaseAgent):
    def __init__(self, conf: AgentConfig, **kwargs):
        super(PlanAgent, self).__init__(conf, **kwargs)

    def name(self) -> str:
        return Agents.PLAN.value

    def reset(self, options: Dict[str, Any]):
        """Execute agent reset need query task as input."""
        self.task = options.get("task")
        self.trajectory = []
        self.system_prompt = plan_system_prompt.format(task=self.task)
        self.done_prompt = plan_done_prompt.format(task=self.task)
        self.postfix_prompt = plan_postfix_prompt.format(task=self.task)
        self.first_prompt = init_prompt
        self.first = True

    @Diagnostic()
    def policy(self,
               observation: Observation,
               info: Dict[str, Any] = None,
               **kwargs) -> List[ActionModel] | None:
        llm_result = None
        input_content = [
            {'role': 'system', 'content': self.system_prompt},
        ]
        # build input of llm based history
        for traj in self.trajectory:
            input_content.append({'role': 'user', 'content': traj[0].content})
            if traj[-1].choices[0].message.tool_calls is not None:
                input_content.append(
                    {'role': 'assistant', 'content': '', 'tool_calls': traj[-1].choices[0].message.tool_calls})
            else:
                input_content.append({'role': 'assistant', 'content': traj[-1].choices[0].message.content})

        message = observation.content
        if self.first_prompt:
            message = self.first_prompt
            self.first_prompt = None

        input_content.append({'role': 'user', 'content': message})
        try:
            llm_result = self.llm.chat.completions.create(
                messages=input_content,
                model=self.model_name,
            )
            logger.info(f"Plan response: {llm_result.choices[0].message}")
        except Exception as e:
            logger.warn(traceback.format_exc())
            raise e
        finally:
            if llm_result:
                if llm_result.choices is None:
                    logger.info(f"llm result is None, info: {llm_result.model_extra}")
                ob = copy.deepcopy(observation)
                ob.content = message
                self.trajectory.append((ob, info, llm_result))
            else:
                logger.warn("no result to record!")
        content = llm_result.choices[0].message.content
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
