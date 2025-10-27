# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc
import json
import logging
import os
import uuid
from typing import Any, List, Dict, Union

from aworld.agents.llm_agent import Agent
from aworld.config.agent_loader import _load_yaml
from aworld.core.agent.swarm import Swarm
from aworld.runner import Runners
from aworld.logs.util import logger
from aworld.core.task import Task
import asyncio
import traceback
import concurrent
import concurrent.futures

from verl.experimental.agent_loop.agent_loop import AgentLoopBase, AgentLoopOutput, AgentLoopMetrics

from train.adapter.common import encode_messages, turns_num
from train.adapter.verl.verl_provider import VerlProvider

# logger.setLevel(logging.INFO)
# logger.propagate = False
# if not logger.handlers:
#     handler = logging.StreamHandler()
#     handler.setLevel(logging.INFO)
#     formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
#     handler.setFormatter(formatter)
#     logger.addHandler(handler)

# import aworld.trace as trace
# trace.configure(trace.ObservabilityConfig())

from aworld.trace.base import Span
from typing import Sequence
from aworld.trace.span_cosumer import register_span_consumer, SpanConsumer
import time
@register_span_consumer()
class MockSpanConsumer(SpanConsumer):
    def consume(self, spans: Sequence[Span]) -> None:
        for span in spans:
            start_timestamp = span.start_time / 1e9
            end_timestamp = span.end_time / 1e9
            start_ms = int((span.start_time % 1e9) / 1e6)
            end_ms = int((span.end_time % 1e9) / 1e6)
            start_time=time.strftime(
                '%Y-%m-%d %H:%M:%S', time.localtime(start_timestamp)) + f'.{start_ms:03d}',
            end_time=time.strftime(
                '%Y-%m-%d %H:%M:%S', time.localtime(end_timestamp)) + f'.{end_ms:03d}',
            logger.info(
                f"[trace_span]={span.name}, trace_id={span.get_trace_id()}, span_id={span.get_span_id()}, start_time={start_time}, end_time={end_time}, duration_ms={(span.end_time - span.start_time)/1e6}")


class AworldAgentLoop(AgentLoopBase):
    __metaclass__ = abc.ABCMeta

    @abc.abstractmethod
    async def build_agents(self) -> Union[Agent, Swarm]:
        """Build single- or multi-agent"""

    async def get_llm_server_address(self, server_name: str = None) -> str:
        server = self.server_manager._choose_server(server_name or uuid.uuid4().hex)
        base_url = await server.get_server_address.remote()
        base_url = f"http://{base_url}/v1"
        logger.info(f"agent_loop|get_server_address#base_url: {base_url}|process_id={os.getpid()}")
        return base_url

    async def get_llm_server_model_name(self):
        model_name = "/".join(self.config.actor_rollout_ref.model.path.split("/")[-2:])
        logger.info(f"agent_loop|get_server_model_name#model_name: {model_name}|process_id={os.getpid()}")
        return model_name

    # main branch
    # async def run(self, sampling_params: dict[str, Any], **kwargs) -> AgentLoopOutput:
    #     messages = list(kwargs["raw_prompt"])

    # release 0.5.0
    # async def run(self, messages: list, sampling_params: dict[str, Any], **kwargs) -> AgentLoopOutput:
    async def run(self, sampling_params: dict[str, Any], **kwargs) -> AgentLoopOutput:
        messages = list(kwargs["raw_prompt"])
        logger.warning(f"agent_loop|######## messages: {messages} ########\n|process_id={os.getpid()}")

        agent = await self.build_agents()

        self.agent = agent

        import time
        start_time = time.time()
        logger.warning(f"agent_loop|######## trajectory start ########\n|process_id={os.getpid()}")

        result = await self.run_agents(messages[0], agent)
        res = result.trajectory
        
        end_time = time.time()
        elapsed_time = end_time - start_time
        logger.warning(f"agent_loop|######## trajectory finish, time costs {elapsed_time:.2f} s ########\n|process_id={os.getpid()}")
        traj_task_id = res[-1]['exp_meta']['task_id']
        logger.warning(f"agent_loop|######## task {traj_task_id} res[-1]['exp_data']: {res[-1]['exp_data']} ########\n|process_id={os.getpid()}")
        logger.warning(f"agent_loop|######## task {traj_task_id} res[-1]['exp_data']['actions']: {res[-1]['exp_data']['actions']} ########\n|process_id={os.getpid()}")
        logger.warning(f"agent_loop|######## task {traj_task_id} res[-1]['exp_data']['messages']: {res[-1]['exp_data']['messages']} ########\n|process_id={os.getpid()}")

        # build agent loop output
        output = await self.convert_agent_output(result=result, trajectory=res)
        return output

    # @trace.func_span(span_name="agent_loop.run_agents")
    async def run_agents(self, input, agent):
        loop = asyncio.get_event_loop()
        logger.warning(f"agent_loop|######## run_agents start ######## loop_id={id(loop)}\n|process_id={os.getpid()}")
        if isinstance(input, dict):
            input = input.get("content", "")
        
        # Define default trajectory return value
        default_trajectory = [
            {
                "exp_meta": {
                    "task_id": "timeout_default",
                    "timestamp": "2025-01-01T00:00:00Z"
                },
                "exp_data": {
                    "messages": [
                        {"role": "user", "content": str(input)},
                        {"role": "assistant", "content": "Timeout, please try again."}
                    ],
                    "actions": []
                }
            }
        ]
        
        import uuid
        task_id = uuid.uuid1().hex
        # Create default result object
        class DefaultResult:
            id: str = ""
            def __init__(self):
                self.id = task_id
                self.trajectory = default_trajectory
        
        default_result = DefaultResult()
        
        try:
            logger.info(f"agent_loop|run_agents|start|process_id={os.getpid()}|input={input}")
            
            # Execute agent task directly with timeout
            result = await asyncio.wait_for(
                self._execute_agent_task(task_id, input, agent),
                timeout=1200.0
            )
            
            logger.info(f"agent_loop|run_agents|finished|process_id={os.getpid()}|input={input}\n|result={result}")
            return result
                    
        except asyncio.TimeoutError:
            logger.warning(f"agent_loop|run_agents|timeout|process_id={os.getpid()}|input={input}|returning_default_result")
            return default_result
        except Exception as e:
            logger.error(f"agent_loop|run_agents|error|process_id={os.getpid()}|input={input}|error={str(e)}|returning_default_result|{traceback.format_exc()}")
            return default_result

    async def _execute_agent_task(self, id, input, agent):
        """Core logic for executing agent tasks"""
        # collect trajectory
        if isinstance(agent, Swarm):
            task = Task(id=id, input=input, swarm=agent, timeout=1200)
            res = await Runners.run_task(task)
            result = res.get(id)
        else:
            agent.task = input
            task = Task(id=id, input=input, agent=agent, timeout=1200)
            res = await Runners.run_task(task)
            result = res.get(id)
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

    async def convert_agent_output(self, result, trajectory: List[Dict[str, Any]]) -> AgentLoopOutput:
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
            actions = trajectory[-1].get("exp_data", {}).get("actions", [])
            if len(actions) < 1:
                logger.warning(f"agent_loop|Found last message actions empty.|process_id={os.getpid()}")
                last_non_tool_index = -1
                for i in range(len(messages) - 1, -1, -1):
                    if messages[i].get("role") != "tool":
                        last_non_tool_index = i
                        break 
                if last_non_tool_index != -1:
                    messages = messages[:last_non_tool_index + 1]
                else:
                    messages = []
            else:
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
                logger.info(f"agent_loop|last_assistant_message: {last_assistant_message}|process_id={os.getpid()}")
                logger.info(f"agent_loop|messages postprocessed: {messages}|process_id={os.getpid()}")

        output = await self.to_agent_loop_output(messages=messages)
        if hasattr(result, 'id'):
            logger.info(f"agent_loop|convert_agent_output|finish|task_id={result.id}")
            output.extra_fields['task_id'] = result.id
        
        return output

    async def to_agent_loop_output(self, messages: List[Dict[str, Any]]) -> AgentLoopOutput:
        """Convert messages to AgentLoopOutput.

        Args:
            messages (List[Dict[str, Any]]): List of messages in OpenAI request format.

        Returns:
            AgentLoopOutput: agent loop output trajectory used for training.
        """
        # Ensure tools is iterable for chat templates that iterate over tools

        response_length = self.config.actor_rollout_ref.rollout.response_length
        chat_template = self.config.actor_rollout_ref.rollout.customize_chat_template
        # |chat_template={chat_template}|messages={messages}
        logger.info(f"to_agent_loop_output|start|messages_len={len(messages)}|actor_rollout_ref.rollout.response_length={self.config.actor_rollout_ref.rollout.response_length}")
        prompt_ids, response_ids, response_mask = await encode_messages(self.tokenizer,
                                                                        messages,
                                                                        response_length=response_length,
                                                                        tools=self.agent.tools,
                                                                        chat_template=chat_template)
        filtered_ids = []
        for i, item in enumerate(response_mask):
            filtered_ids.append(0 if response_mask[i] == 0 else response_ids[i])
        logger.info(f"to_agent_loop_output|finish|len={len(response_ids)}") #|response_ids={self.tokenizer.decode(response_ids, skip_special_tokens=True)}\n|masked={self.tokenizer.decode(filtered_ids)}")

        

        output = AgentLoopOutput(
            prompt_ids=prompt_ids,
            response_ids=response_ids,
            response_mask=response_mask,
            num_turns=turns_num(messages),
            metrics={},
        )
        return output


