# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import copy
import json
import time
import traceback
from typing import Any, Dict, List, Union

from aworld.agents.gaia.prompts import *
from aworld.agents.gaia.utils import extract_pattern
from aworld.config.common import Agents
from aworld.config.conf import AgentConfig, ConfigDict
from aworld.core.agent.base import Agent, AgentFactory
from aworld.core.common import ActionModel, Observation
from aworld.logs.util import logger
from aworld.models.llm import call_llm_model


def check_log_level(level_name):
    try:
        level_value = logger.level(level_name).no
        return True
    except ValueError:
        return False


@AgentFactory.register(name=Agents.EXECUTE.value, desc="execute agent")
class ExecuteAgent(Agent):
    def __init__(self, conf: Union[Dict[str, Any], ConfigDict, AgentConfig], **kwargs):
        super(ExecuteAgent, self).__init__(conf, **kwargs)
        # Initialize logger context
        self.logger_context = logger.bind(agent="ExecuteAgent", method="policy")
        if not check_log_level("EXECUTE"):
            self.logger_context.level(
                "EXECUTE", no=25, color="<bold><fg #FC753F>", icon="🚀"
            )
        self.logger_context.execute = (
            lambda message, *message_args, **message_kwargs: self.logger_context.log(
                "EXECUTE",
                message,
                *message_args,
                **message_kwargs,
            )
        )
        self.logger_context.add(
            "./agent-running–details.log",
            rotation="1 week",
            compression="zip",
            format="{time} - {level} - {message}",
        )

    def reset(self, options: Dict[str, Any]):
        """Execute agent reset need query task as input."""
        super().reset(options)

        self.system_prompt = execute_system_prompt.format(task=self.task)
        self.step_reset = False

    def policy(
        self, observation: Observation, info: Dict[str, Any] = None, **kwargs
    ) -> List[ActionModel] | None:
        start_time = time.time()
        self._finished = False
        self.desc_transform()
        content = observation.content

        llm_result = None
        ## build input of llm
        input_content = [
            {"role": "system", "content": self.system_prompt},
        ]
        for traj in self.trajectory:
            # Handle multiple messages in content
            if isinstance(traj[0].content, list):
                input_content.extend(traj[0].content)
            else:
                input_content.append(traj[0].content)

            if traj[-1].tool_calls is not None:
                input_content.append(
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": traj[-1].tool_calls,
                    }
                )
            else:
                input_content.append({"role": "assistant", "content": traj[-1].content})

        self.logger_context.execute(f"Input content prepared: {input_content}")

        if content is None:
            content = observation.action_result[0].error
        if not self.trajectory:
            new_messages = [{"role": "user", "content": content}]
            input_content.extend(new_messages)
        else:
            # Collect existing tool_call_ids from input_content
            existing_tool_call_ids = {
                msg.get("tool_call_id")
                for msg in input_content
                if msg.get("role") == "tool" and msg.get("tool_call_id")
            }

            new_messages = []
            for traj in self.trajectory:
                if traj[-1].tool_calls is not None:
                    # Handle multiple tool calls
                    for tool_call in traj[-1].tool_calls:
                        # Only add if this tool_call_id doesn't exist in input_content
                        if tool_call.id not in existing_tool_call_ids:
                            new_messages.append(
                                {
                                    "role": "tool",
                                    "content": content,
                                    "tool_call_id": tool_call.id,
                                }
                            )
            if new_messages:
                input_content.extend(new_messages)
            else:
                input_content.append({"role": "user", "content": content})

            # Validate tool_calls and tool messages pairing
            assistant_tool_calls = []
            tool_responses = []
            for msg in input_content:
                if msg.get("role") == "assistant" and msg.get("tool_calls"):
                    assistant_tool_calls.extend(msg["tool_calls"])
                elif msg.get("role") == "tool":
                    tool_responses.append(msg.get("tool_call_id"))

            # Check if all tool_calls have corresponding responses
            tool_call_ids = {call.id for call in assistant_tool_calls}
            tool_response_ids = set(tool_responses)
            if tool_call_ids != tool_response_ids:
                missing_calls = tool_call_ids - tool_response_ids
                extra_responses = tool_response_ids - tool_call_ids
                error_msg = f"Tool calls and responses mismatch. Missing responses for tool_calls: {missing_calls}, Extra responses: {extra_responses}"
                self.logger_context.error(error_msg)
                raise ValueError(error_msg)

        tool_calls = []
        try:
            llm_result = call_llm_model(
                self.llm,
                input_content,
                model=self.model_name,
                tools=self.tools,
                temperature=0,
            )
            self.logger_context.execute(f"Execute response: {llm_result.message}")
            res = self.response_parse(llm_result)
            content = res.actions[0].policy_info
            tool_calls = llm_result.tool_calls
        except Exception as e:
            self.logger_context.warning(traceback.format_exc())
            # raise e
        finally:
            if llm_result:
                ob = copy.deepcopy(observation)
                ob.content = new_messages
                self.trajectory.append((ob, info, llm_result))
            else:
                self.logger_context.warning("no result to record!")

        res = []
        if tool_calls:
            for tool_call in tool_calls:
                tool_action_name: str = tool_call.function.name
                if not tool_action_name:
                    continue

                names = tool_action_name.split("__")
                tool_name = names[0]
                action_name = "__".join(names[1:]) if len(names) > 1 else ""
                params = json.loads(tool_call.function.arguments)
                res.append(
                    ActionModel(
                        tool_name=tool_name, action_name=action_name, params=params
                    )
                )

        if res:
            res[0].policy_info = content
        elif content:
            policy_info = extract_pattern(content, "final_answer")
            if policy_info:
                res.append(
                    ActionModel(agent_name=Agents.PLAN.value, policy_info=policy_info)
                )
                self._finished = True
            else:
                res.append(
                    ActionModel(agent_name=Agents.PLAN.value, policy_info=content)
                )

        self.logger_context.execute(f">>> execute result: {res}")
        return res


@AgentFactory.register(name=Agents.PLAN.value, desc="plan agent")
class PlanAgent(Agent):
    def __init__(self, conf: Union[Dict[str, Any], ConfigDict, AgentConfig], **kwargs):
        super(PlanAgent, self).__init__(conf, **kwargs)
        # Initialize logger context
        self.logger_context = logger.bind(agent="PlanAgent", method="policy")
        if not check_log_level("PLAN"):
            self.logger_context.level(
                "PLAN", no=25, color="<bold><fg #6837F4>", icon="🤔"
            )
        self.logger_context.plan = (
            lambda message, *message_args, **message_kwargs: self.logger_context.log(
                "PLAN",
                message,
                *message_args,
                **message_kwargs,
            )
        )
        self.logger_context.add(
            "./agent-running–details.log",
            rotation="1 week",
            compression="zip",
            format="{time} - {level} - {message}",
        )

    def reset(self, options: Dict[str, Any]):
        """Execute agent reset need query task as input."""
        super().reset(options)

        self.system_prompt = plan_system_prompt.format(task=self.task)
        self.done_prompt = plan_done_prompt.format(task=self.task)
        self.postfix_prompt = plan_postfix_prompt.format(task=self.task)
        self.first_prompt = init_prompt
        self.first = True
        self.step_reset = False

    def policy(
        self, observation: Observation, info: Dict[str, Any] = None, **kwargs
    ) -> List[ActionModel] | None:
        llm_result = None
        self._finished = False
        self.desc_transform()

        input_content = [
            {"role": "system", "content": self.system_prompt},
        ]
        # build input of llm based history
        for traj in self.trajectory:
            input_content.append({"role": "user", "content": traj[0].content})
            if traj[-1].tool_calls is not None:
                input_content.append(
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": traj[-1].tool_calls,
                    }
                )
            else:
                input_content.append({"role": "assistant", "content": traj[-1].content})

        self.logger_context.plan(f"Input content prepared: {input_content}")

        message = observation.content
        if self.first_prompt:
            message = self.first_prompt
            self.first_prompt = None

        input_content.append({"role": "user", "content": message})
        try:
            llm_result = call_llm_model(
                self.llm, messages=input_content, model=self.model_name
            )
            self.logger_context.plan(f"Plan response: {llm_result.message}")
        except Exception as e:
            self.logger_context.warning(traceback.format_exc())
            raise e
        finally:
            if llm_result:
                ob = copy.deepcopy(observation)
                ob.content = message
                self.trajectory.append((ob, info, llm_result))
            else:
                self.logger_context.warning("no result to record!")
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
        self.logger_context.plan(f">>> plan result: {content}")
        return [ActionModel(agent_name=Agents.EXECUTE.value, policy_info=content)]
