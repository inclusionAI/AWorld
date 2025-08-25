# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc
import asyncio
import json
import uuid
from typing import Any, List, Dict, Union

from aworld.agents.llm_agent import Agent
from aworld.core.agent.swarm import Swarm
from aworld.runner import Runners

from verl.experimental.agent_loop.agent_loop import AgentLoopBase, AgentLoopOutput, AgentLoopMetrics


class AworldAgentLoop(AgentLoopBase):
    __metaclass__ = abc.ABCMeta

    @abc.abstractmethod
    def build_agents(self) -> Union[Agent, Swarm]:
        """Build single- or multi-agent"""

    # main branch
    # async def run(self, sampling_params: dict[str, Any], **kwargs) -> AgentLoopOutput:
    #     messages = list(kwargs["raw_prompt"])

    # release 0.5.0
    async def run(self, messages: list, sampling_params: dict[str, Any], **kwargs) -> AgentLoopOutput:
        agent = self.build_agent()

        # update conf on runtime
        if not agent.conf.get("llm_config", {}).get("llm_base_url"):
            server = self.server_manager._choose_server(uuid.uuid4().hex)
            base_url = await server.get_server_address.remote()
            base_url = f"http://{base_url}/v1"
            agent.conf.get("llm_config", {})["llm_base_url"] = base_url
            agent.conf.get("llm_config", {})["llm_api_key"] = "dummy"

        if not agent.conf.get("llm_config", {}).get("llm_model_name"):
            model_name = "/".join(self.config.actor_rollout_ref.model.path.split("/")[-2:])
            agent.conf.get("llm_config", {})["llm_model_name"] = model_name

        result = await self.run_agents(messages[0], agent)
        res = result.trajectory

        # build agent loop output
        output = await self.to_agent_loop_output(trajectory=res,
                                                 response_length=self.config.actor_rollout_ref.rollout.response_length)
        return output

    async def run_agents(self, input, agent):
        # collect trajectory
        if isinstance(agent, Swarm):
            result = Runners.sync_run(input=input, swarm=agent)
        else:
            result = Runners.sync_run(input=input, agent=agent)

        return result

    def get_num_turns(self, trajectory: List[Dict[str, Any]]):
        return len(trajectory)

    async def to_agent_loop_output(self, trajectory: List[Dict[str, Any]], response_length: int) -> AgentLoopOutput:
        """Convert trajectory to AgentLoopOutput.

        Args:
            trajectory (List[Dict[str, Any]]): List of agent execution trajectory.
            response_length (int): Max length of response.

        Returns:
            AgentLoopOutput: agent loop output trajectory used for training.
        """
        if not trajectory:
            raise Exception("Trajectory is empty")

        num_turns = self.get_num_turns(trajectory)
        messages = trajectory[-1].get("exp_data", {}).get("messages", [])
        if not messages:
            return AgentLoopOutput(
                prompt_ids=[],
                response_ids=[],
                response_mask=[],
                num_turns=num_turns,
                metrics={},
            )
        if messages[-1].get("role") != "assistant":
            try:
                actions = trajectory[-1].get("exp_data", {}).get("actions", [])
                assert len(actions) >= 1, f"Last action must not be empty, but got {actions}"
                agent_resp_content = str(actions[0].get("policy_info"))
                last_assistant_message = {
                    "role": "assistant",
                    "content": agent_resp_content
                }
                tool_calls = []
                for action in actions:
                    tool_calls.append({
                        "id": action.get("tool_call_id"),
                        "type": "function",
                        "function": {
                            "name": action.get("tool_name"),
                            "arguments": json.dumps(action.get("params"), ensure_ascii=False),
                        }
                    })
                last_assistant_message["tool_calls"] = tool_calls
                messages.append(last_assistant_message)
            except Exception as e:
                raise Exception(f"Failed to get last assistant message from last trajectory: {trajectory[-1]}")

        prompt_ids = []
        response_ids = []
        response_mask = []
        chat_list = []
        loop = asyncio.get_running_loop()
        # system_prompt_prefix_ids = self.tokenizer.apply_chat_template([{}], add_generation_prompt=False, tokenize=True)
        i = 0
        while i < len(messages):
            if messages[i].get("role") == "system":
                chat_list.append(messages[i])
                i += 1
                continue
            # initial chat completion
            if messages[i].get("role") == "user":
                if (i == 0 or messages[i - 1].get("role") == "system"):
                    chat_list.append(messages[i])
                    prompt_ids = await loop.run_in_executor(
                        None,
                        lambda: self.tokenizer.apply_chat_template(
                            chat_list,
                            tools=self.agent.tools,
                            add_generation_prompt=True,
                            tokenize=True,
                        ),
                    )
                    chat_list = []
                    i += 1
                    continue
                else:
                    chat_list.append(messages[i])
                    cur_response_ids = await loop.run_in_executor(
                        None,
                        lambda: self.tokenizer.apply_chat_template(
                            chat_list,
                            add_generation_prompt=True,
                            tokenize=True,
                        ),
                    )
                    chat_list = []
                    response_ids += cur_response_ids
                    response_mask += [0] * len(cur_response_ids)
                    i += 1
                    continue
            # assistant message
            if messages[i].get("role") == "assistant":
                chat_list.append(messages[i])
                cur_response_ids = await loop.run_in_executor(
                    None,
                    lambda: self.tokenizer.apply_chat_template(
                        chat_list,
                        tools=self.agent.tools,
                        add_generation_prompt=True,
                        tokenize=True,
                    ),
                )
                chat_list = []
                response_ids += cur_response_ids
                response_mask += [1] * len(cur_response_ids)
                i += 1
                continue
            # follow up chat completion with tool response:
            if messages[i].get("role") == "tool":
                last_assistant_message = messages[i - 1]
                chat_list.append(last_assistant_message)
                token_assistant = await loop.run_in_executor(
                    None,
                    lambda: self.tokenizer.apply_chat_template(
                        chat_list,
                        tools=self.agent.tools,
                        add_generation_prompt=True,
                        tokenize=True,
                    ),
                )
                while i < len(messages) and messages[i].get("role") == "tool":
                    chat_list.append(messages[i])
                    i += 1
                token_assistant_tool = await loop.run_in_executor(
                    None,
                    lambda: self.tokenizer.apply_chat_template(
                        chat_list,
                        tools=self.agent.tools,
                        add_generation_prompt=True,
                        tokenize=True,
                    ),
                )
                tool_response_ids = token_assistant_tool[len(token_assistant):]
                chat_list = []
                response_ids += tool_response_ids
                response_mask += [0] * len(tool_response_ids)

        max_response_length = min(response_length, len(response_ids))
        output = AgentLoopOutput(
            prompt_ids=prompt_ids,
            response_ids=response_ids[:max_response_length],
            response_mask=response_mask[:max_response_length],
            num_turns=num_turns,
            metrics={},
        )
        return output
