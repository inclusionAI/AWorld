import asyncio
import json
import uuid
from typing import Any, List, Dict

from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig
from aworld.runner import Runners
from omegaconf import DictConfig
from transformers import AutoTokenizer, AutoProcessor

from verl.experimental.agent_loop.agent_loop import AgentLoopBase, AgentLoopOutput, AgentLoopMetrics
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    convert_to_openai_messages,
)


GAIA_SYSTEM_PROMPT = """
You are an all-capable AI assistant, aimed at solving any task presented by the user.
"""

GAIA_MCP_CONFIG = {
    "mcpServers": {
        "virtualpc-mcp-server": {
            "type": "streamable-http",
            "url": "",
            "headers": {
              "Authorization": "",
              "MCP_SERVERS": "",
            },
            "timeout": 600,
            "sse_read_timeout": 600,
            "client_session_timeout_seconds": 600
        }
    }
}

class GaiaAgentLoop(AgentLoopBase):
    @classmethod
    def init_class(cls, config: DictConfig, tokenizer: AutoTokenizer, **kwargs):
        super().init_class(config=config, tokenizer=tokenizer, **kwargs)
        cls.tokenizer = AutoTokenizer.from_pretrained(config.model_name)
        cls.agent = cls.build_agent()

    @classmethod
    def build_agent(cls):
        agent_config = AgentConfig(
            llm_model_name="{YOUR_CONFIG}",
            llm_base_url="{YOUR_CONFIG}",
            llm_api_key="{YOUR_CONFIG}",
        )
        super_agent = Agent(
            conf=agent_config,
            name="gaia_super_agent",
            system_prompt="{YOUR_SYSTEM_PROMPT}",
            mcp_config=GAIA_MCP_CONFIG,
            mcp_servers=list(server_name for server_name in GAIA_MCP_CONFIG.get("mcpServers", {}).keys()),
        )
        return super_agent

    async def run(self, messages: list, sampling_params: dict[str, Any], **kwargs) -> AgentLoopOutput:
        # ##################################################################
        # # run batch tasks
        # # messages: 输入的query集合
        # results = Runners.sync_batch_run(agent=self.agent,
        #                                  input_queries=messages,
        #                                  batch_size=8,
        #                                  run_config=RunConfig())
        # # 其中task_id作为llm server的request id
        # for task_id, task_resp in results.items():
        #     traj = task_resp.trajectory
        # ##################################################################

        # update base url
        server = self.server_manager._choose_server(uuid.uuid4().hex)
        base_url = await server.get_server_address()
        self.agent.conf.get("llm_config", {}).put("llm_base_url", base_url)

        # collect trajectory
        result = Runners.sync_run(input=messages[0], agent=self.agent.deep_copy())
        res = result.trajectory
        output = await self.convert_to_agent_loop_output(trajectory=res, response_length=self.config.actor_rollout_ref.rollout.response_length)
        return output

    def get_num_turns(self, trajectory: List[Dict[str, Any]]) -> int:
        return len(trajectory)

    async def convert_to_agent_loop_output(self, trajectory: List[Dict[str, Any]], response_length: int) -> AgentLoopOutput:
        """Convert messages to AgentLoopOutput.

        Args:
            messages (List[BaseMessage]): List of messages, last message must be assistant
                with response_metadata containing `prompt_ids` and `response_mask`.
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
                if (i == 0 or messages[i-1].get("role") == "system"):
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
                last_assistant_message = messages[i-1]
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
                while i < len(messages) and messages[i].get("role") == "tool" :
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
                tool_response_ids = token_assistant_tool[len(token_assistant) :]
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
