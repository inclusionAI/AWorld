import logging
import os
import traceback
from typing import Any, Callable, Dict, List, Union

from aworld.config.conf import AgentConfig, ConfigDict
from aworld.core.agent.base import Agent
from aworld.core.common import ActionModel, Observation
from aworld.logs.util import Color
from aworld.memory.base import MemoryItem
from aworld.models.llm import call_llm_model
from aworld.models.model_response import ToolCall
from aworld.output.base import StepOutput
from aworld.utils.common import sync_exec
from examples.gaia.utils import color_log, setup_logger


class GaiaAgent(Agent):
    def __init__(
        self,
        output_folder_path: str,
        config: Union[Dict[str, Any], ConfigDict, AgentConfig],
        resp_parse_func: Callable[..., Any] = None,
        **kwargs,
    ):
        super().__init__(config, resp_parse_func, **kwargs)
        self.logger: logging.Logger = self._setup_logger(
            logger_name=self.__class__.__name__, output_folder_path=output_folder_path
        )
        self._color_log(f"Using {os.getenv('LLM_MODEL_NAME')} from {os.getenv('LLM_BASE_URL')}", Color.red)

    def policy(self, observation: Observation, info: Dict[str, Any] = None, **kwargs) -> Union[List[ActionModel], None]:
        """Adapted from the base class. Format necessary GAIA logs.

        Args:
            observation: The state observed from tools in the environment.
            info: Extended information is used to assist the agent to decide a policy.

        Returns:
            ActionModel sequence from agent policy
        """
        # LOG CKPT: Agent's Observation
        self._color_log(f"🌍 Observation: {observation}", Color.pink)

        if info is None:
            info = {}

        if kwargs.get("output") and isinstance(kwargs.get("output"), StepOutput):
            output = kwargs["output"]  # pylint: disable=W0612

        self._finished = False
        self.desc_transform()
        images = observation.images if self.conf.use_vision else None
        if self.conf.use_vision and not images and observation.image:
            images = [observation.image]
        messages = self.messages_transform(
            content=observation.content,
            image_urls=images,
            sys_prompt=self.system_prompt,
            agent_prompt=self.agent_prompt,
            output_prompt=self.output_prompt,
        )

        self.memory.add(
            MemoryItem(
                content=messages[-1]["content"],
                metadata={
                    "role": messages[-1]["role"],
                    "agent_name": self.name(),
                    "tool_call_id": messages[-1].get("tool_call_id"),
                },
            )
        )

        llm_response = None
        try:
            llm_response = call_llm_model(
                self.llm,
                messages=messages,
                model=self.model_name,
                temperature=self.conf.llm_config.llm_temperature,
                tools=self.tools if self.tools else None,
            )
            self._color_log(f"🤖 Execute response: {llm_response.message}", Color.orange)
        except Exception as e:
            self.logger.warning(traceback.format_exc())
            raise e
        finally:
            if llm_response:
                if llm_response.error:
                    self.logger.error(f"LLM result error: {llm_response.error}")
                else:
                    self.memory.add(
                        MemoryItem(
                            content=llm_response.content,
                            metadata={
                                "role": "assistant",
                                "agent_name": self.name(),
                                "tool_calls": llm_response.tool_calls,
                            },
                        )
                    )
            else:
                self.logger.error(f"{self.name()} failed to get LLM response")
                raise RuntimeError(f"{self.name()} failed to get LLM response")

        # output.add_part(MessageOutput(source=llm_response, json_parse=False))
        agent_result = sync_exec(self.resp_parse_func, llm_response)
        if not agent_result.is_call_tool and "<answer>" in llm_response.content:
            self._finished = True
        # output.mark_finished()

        actions: List[ActionModel] = agent_result.actions

        # LOG CKPT: Agent's Policy
        self._color_log(f"💡 Policy: {actions}", Color.cyan)

        return actions

    def _setup_logger(self, logger_name: str, output_folder_path: str, file_name: str = "app.log") -> logging.Logger:
        return setup_logger(
            logger_name=logger_name,
            output_folder_path=output_folder_path,
            file_name=file_name,
        )

    def _color_log(self, message: str, color: Color) -> None:
        color_log(self.logger, message, color)

    def _log_messages(self, messages: List[Dict[str, Any]]) -> None:
        """Log the sequence of messages for debugging purposes"""
        self.logger.debug(f"[agent] Invoking LLM with {len(messages)} messages:")
        for i, msg in enumerate(messages):
            prefix = msg.get("role")
            self.logger.debug(f"[agent] Message {i + 1}: {prefix} ===================================")
            if isinstance(msg["content"], list):
                for item in msg["content"]:
                    if item.get("type") == "text":
                        self.logger.debug(f"[agent] Text content: {item.get('text')}")
                    elif item.get("type") == "image_url":
                        image_url: str = item.get("image_url", {}).get("url", "")
                        if image_url.startswith("data:image"):
                            self.logger.debug("[agent] Image: [Base64 image data]")
                        else:
                            self.logger.debug(f"[agent] Image URL: {image_url[:30]}...")
            else:
                content = str(msg["content"])
                self.logger.debug(f"[agent] Content: {content}")
            if "tool_calls" in msg and msg["tool_calls"]:
                for tool_call in msg.get("tool_calls"):
                    if isinstance(tool_call, dict):
                        self.logger.debug(f"[agent] Tool call: {tool_call.get('name')} - ID: {{tool_call.get('id')}}")
                        args = str(tool_call.get("args", {}))[:1000]
                        self.logger.debug(f"[agent] Tool args: {args}...")
                    elif isinstance(tool_call, ToolCall):
                        self.logger.debug(f"[agent] Tool call: {tool_call.function.name} - ID: {tool_call.id}")
                        args = str(tool_call.function.arguments)[:1000]
                        self.logger.debug(f"[agent] Tool args: {args}...")
