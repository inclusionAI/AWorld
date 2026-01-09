import asyncio
import abc
import uuid

from dataclasses import dataclass, field
from typing import List, Dict, Literal

from aworld.core.llm_provider import LLMProviderBase
from aworld.models.model_response import ModelResponse, ToolCall, Function
from aworld.utils.common import sync_exec
from aworld.core.context.base import Context
from aworld.logs.util import logger


@dataclass
class TokenIdModelResponse:
    output_token_ids: List[int] = field(default_factory=list)
    output_logprobs: List[float] = field(default_factory=list)
    output_versions: List[int] = field(default_factory=list)
    finish_reason: Literal["length", "stop", "interrupt"] = "stop"


class RolloutLLMProvider(LLMProviderBase):

    def __init__(self,
                 api_key: str = None,
                 base_url: str = None,
                 model_name: str = None,
                 sync_enabled: bool = None,
                 async_enabled: bool = None,
                 **kwargs):
        super().__init__(api_key=api_key,
                         base_url=base_url,
                         model_name=model_name,
                         sync_enabled=sync_enabled,
                         async_enabled=async_enabled, **kwargs)

        params = kwargs.get("params")
        self.tokenizer = params.get("tokenizer")
        self.tool_parser = params.get("tool_parser") or HermesToolParser(self.tokenizer)
        self.request_id = params.get("request_id") or uuid.uuid4().hex

    def _init_provider(self):
        pass

    def _init_async_provider(self):
        pass

    @abc.abstractmethod
    async def agenerate(self, input_ids: List[int],
                        temperature: float = 0.0,
                        max_tokens: int = None,
                        stop: List[str] = None,
                        **kwargs) -> TokenIdModelResponse:
        """
        Generate token ids asynchronously.
        """

    def completion(self, messages: List[Dict[str, str]], temperature: float = 0.0, max_tokens: int = None,
                   stop: List[str] = None, **kwargs) -> ModelResponse:
        return sync_exec(self.acompletion, messages, temperature, max_tokens, stop, **kwargs)

    async def acompletion(self,
                          messages: List[Dict[str, str]],
                          temperature: float = 0.0,
                          max_tokens: int = None,
                          stop: List[str] = None,
                          context: Context = None,
                          **kwargs) -> ModelResponse:
        loop = asyncio.get_running_loop()
        current_step_input_token_ids = await loop.run_in_executor(None, self._get_current_step_input_token_ids, messages)
        current_agent_token_id_traj = context.get_agent_token_id_traj()

        input_ids = current_agent_token_id_traj.all_token_id_seq + current_step_input_token_ids
        token_id_response = await self.agenerate(input_ids, temperature, max_tokens, stop, **kwargs)

        content = await loop.run_in_executor(
            None,
            lambda: self.tokenizer.decode(token_id_response.output_token_ids, skip_special_tokens=True)
        )

        res, tool_calls = await self.tool_parser.extract_tool_calls(content)
        if tool_calls:
            tool_calls = [ToolCall(id=uuid.uuid4().hex, function=tool_call) for tool_call in tool_calls]

        context.add_llm_resp_token_ids(input_token_ids=current_step_input_token_ids,
                                       prompt_token_ids=input_ids,
                                       response=token_id_response)

        usage = {
            "completion_tokens": len(token_id_response.output_token_ids),
            "prompt_tokens": len(input_ids),
            "total_tokens": len(input_ids) + len(token_id_response.output_token_ids)
        }

        return ModelResponse(id=self.request_id,
                             content=content,
                             tool_calls=tool_calls,
                             usage=usage,
                             model=self.model_name)

    def _get_current_step_input_token_ids(self, messages: List[Dict[str, str]], **kwargs) -> List[int]:
        """
        Get the token ids of the current step input.
        Only use messages after the last assistant message.
        """
        # Find the last assistant message
        last_assistant_index = -1
        for i, message in enumerate(messages):
            if message.get('role') == 'assistant':
                last_assistant_index = i

        # Get messages after the last assistant message
        # If no assistant message found, use all messages
        filtered_messages = messages[last_assistant_index + 1:] if last_assistant_index >= 0 else messages

        if last_assistant_index == -1:
            llm_params = kwargs.get("params", {})
            llm_params.update(kwargs)
            tools = llm_params.get("tools")
            return self.tokenizer.apply_chat_template(filtered_messages, tools=tools, tokenize=True, add_generation_prompt=True)

        return self.apply_chat_template(filtered_messages)

    def apply_chat_template(self, messages: List[Dict[str, str]]) -> List[int]:
        """
        Apply the chat template to the messages.
        """
        placeholder_messages = [{"role": "assistant", "content": "some random message."}]
        s1 = self.tokenizer.apply_chat_template(placeholder_messages, tokenize=True)
        messages = placeholder_messages + messages
        s2 = self.tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True)
        return s2[len(s1):]


class HermesToolParser:
    def __init__(self, tokenizer) -> None:
        import re

        self.tokenizer = tokenizer
        self.tool_call_start_token: str = "<tool_call>"
        self.tool_call_end_token: str = "</tool_call>"
        self.tool_call_regex = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL)

    async def extract_tool_calls(self, text) -> tuple[str, list[Function]]:
        import json

        if self.tool_call_start_token not in text or self.tool_call_end_token not in text:
            return text, []

        matches = self.tool_call_regex.findall(text)
        function_calls = []
        for match in matches:
            try:
                function_call = json.loads(match)
                name, arguments = function_call["name"], function_call["arguments"]
                function_calls.append(Function(name=name, arguments=json.dumps(arguments, ensure_ascii=False)))
            except Exception as e:
                print(f"Failed to decode tool call: {e}")

        # remaing text exclude tool call tokens
        content = self.tool_call_regex.sub("", text)

        return content, function_calls
