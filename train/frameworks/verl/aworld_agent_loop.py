# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc
import json
import os
import uuid
from typing import Any, List, Dict, Union

from aworld.agents.llm_agent import Agent
from aworld.config.agent_loader import _load_yaml
from aworld.core.agent.swarm import Swarm
from aworld.runner import Runners

from verl.experimental.agent_loop.agent_loop import AgentLoopBase, AgentLoopOutput

from train.frameworks.verl.common import to_agent_loop_output


class AworldAgentLoop(AgentLoopBase):
    __metaclass__ = abc.ABCMeta

    @abc.abstractmethod
    def build_agents(self, model_name: str = "", base_url: str = "", api_key: str = "") -> Union[Agent, Swarm]:
        """Build single- or multi-agent"""

    # main branch
    # async def run(self, sampling_params: dict[str, Any], **kwargs) -> AgentLoopOutput:
    #     messages = list(kwargs["raw_prompt"])

    # release 0.5.0
    async def run(self, messages: list, sampling_params: dict[str, Any], **kwargs) -> AgentLoopOutput:
        server = self.server_manager._choose_server(uuid.uuid4().hex)
        base_url = await server.get_server_address.remote()
        base_url = f"http://{base_url}/v1"
        model_name = "/".join(self.config.actor_rollout_ref.model.path.split("/")[-2:])
        print(f"base_url: {base_url}, model_name: {model_name}")
        agent = self.build_agents(model_name=model_name, base_url=base_url, api_key="dummy")

        # load mcp tool config
        tool_config_path = os.environ["AGENT_TOOL_CONFIG_PATH"]
        if isinstance(agent, Agent) and tool_config_path:
            tool_config = await self.get_agent_tool_config(tool_config_path)
            agent.mcp_config = tool_config
            agent.mcp_servers = list(server_name for server_name in tool_config.get("mcpServers", {}).keys())

        self.agent = agent

        result = await self.run_agents(messages[0], agent)
        res = result.trajectory

        # build agent loop output
        output = await self.convert_agent_output(trajectory=res,
                                                 response_length=self.config.actor_rollout_ref.rollout.response_length)
        return output

    async def run_agents(self, input, agent):
        # collect trajectory
        if isinstance(agent, Swarm):
            result = Runners.sync_run(input=input, swarm=agent)
        else:
            result = Runners.sync_run(input=input, agent=agent)

        return result

    async def get_agent_tool_config(self, config_path: str) -> Dict[str, Any]:
        """Load tool configuration, preferring YAML with simple fields.

            Priority:
            1) agent_tools.yaml (simple user config with url, Authorization, MCP_SERVERS)
            2) mcp.json (legacy full config)
            """

        # 1) Try YAML (simple schema)
        try:
            import yaml  # Local import to avoid hard dependency at import time
            if os.path.exists(config_path):
                src = _load_yaml(config_path)

                url = src.get('url', '')
                authorization = src.get('Authorization', '')
                mcp_servers_value = src.get('MCP_SERVERS', '')

                # Normalize servers to comma-separated string for header and list for internal
                if isinstance(mcp_servers_value, list):
                    mcp_servers_str = ','.join([str(s).strip() for s in mcp_servers_value if str(s).strip()])
                else:
                    mcp_servers_str = str(mcp_servers_value or '').strip()

                # Build internal full mcp_config
                server_name = src.get('server_name', 'aworld-mcp')
                server_type = src.get('type', 'streamable-http')
                timeout = src.get('timeout', 600)
                sse_read_timeout = src.get('sse_read_timeout', 600)
                client_session_timeout_seconds = src.get('client_session_timeout_seconds', 600)

                if url:
                    mcp_config = {
                        "mcpServers": {
                            server_name: {
                                "type": server_type,
                                "url": url,
                                "headers": {
                                    "Authorization": authorization,
                                    "MCP_SERVERS": mcp_servers_str,
                                },
                                "timeout": timeout,
                                "sse_read_timeout": sse_read_timeout,
                                "client_session_timeout_seconds": client_session_timeout_seconds,
                            }
                        }
                    }
                    return mcp_config
        except Exception as err:
            print(f"Error loading YAML tool config err: {err}")

        # 2) Fallback to legacy JSON
        try:
            if os.path.exists(config_path):
                with open(config_path, "r") as f:
                    return json.load(f)
        except Exception as err:
            print(f"Error loading tool config[{config_path}] err is : {err}")

    def get_num_turns(self, trajectory: List[Dict[str, Any]]):
        return len(trajectory)

    async def convert_agent_output(self, trajectory: List[Dict[str, Any]], response_length: int) -> AgentLoopOutput:
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

        output = await to_agent_loop_output(tokenizer=self.tokenizer,
                                            messages=messages,
                                            response_length=response_length,
                                            tools=self.agent.tools)
        return output
