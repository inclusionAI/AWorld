# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import uuid
from typing import List, Dict, Any

from aworld.core.llm_provider import LLMProviderBase
from aworld.logs.util import logger
from aworld.models.llm import register_llm_provider
from aworld.models.model_response import ModelResponse, ToolCall
from aworld.utils.common import sync_exec
from train.adapter.verl.common import encode_messages

from vllm.entrypoints.openai.protocol import ExtractedToolCallInformation
from vllm.entrypoints.openai.tool_parsers import ToolParserManager, ToolParser


class VerlProvider(LLMProviderBase):
    """Verl vllm provider implementation.
    """

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
        print("provider params: ", params)
        self.provider = params.get("client")
        self.tokenizer = params.get("tokenizer")
        self.sampling_params = params.get("sampling_params", {})
        self.request_id = params.get("task_id")
        self.tool_parser = params.get("tool_parser")

    def _init_provider(self):
        pass

    def _init_async_provider(self):
        pass

    @classmethod
    def supported_models(cls) -> list[str]:
        return [""]

    def postprocess_response(self, response: Any) -> ModelResponse:
        pass

    def completion(self, messages: List[Dict[str, str]], temperature: float = 0.0, max_tokens: int = None,
                   stop: List[str] = None, **kwargs) -> ModelResponse:
        return sync_exec(self.acompletion, messages, temperature, max_tokens, stop, **kwargs)

    async def acompletion(self,
                          messages: List[Dict[str, str]],
                          temperature: float = 0.0,
                          max_tokens: int = None,
                          stop: List[str] = None,
                          **kwargs) -> ModelResponse:
        sampling_params = {
            "temperature": temperature,
            "top_p": kwargs.get('top_p', 1.0),
            "repetition_penalty": kwargs.get('repetition_penalty', 1.0),
        }
        sampling_params.update(self.sampling_params)

        prompt_ids, _, response_mask = await encode_messages(self.tokenizer, messages, tools=kwargs.get("tools"))
        response_ids = await self.provider.generate(
            request_id=self.request_id, prompt_ids=prompt_ids, sampling_params=sampling_params
        )
        content = self.tokenizer.decode(response_ids, skip_special_tokens=True)

        logger.warning(f"content: {content}")

        tool_parser = ToolParserManager.get_tool_parser(self.tool_parser)
        res: ExtractedToolCallInformation = tool_parser(self.tokenizer).extract_tool_calls(content, request=None)

        rid = uuid.uuid4().hex
        if res.tools_called:
            tool_calls = [ToolCall(**tool_call.model_dump()) for tool_call in res.tool_calls]
            return ModelResponse(id=rid, tool_calls=tool_calls, model=self.model_name, raw_response=content)
        else:
            return ModelResponse(id=rid, content=res.content, model=self.model_name, raw_response=content)


register_llm_provider("verl", VerlProvider)
