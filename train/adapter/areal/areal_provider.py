# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
import uuid
from typing import List, Dict, Any

from areal.api.cli_args import GenerationHyperparameters
from areal.api.io_struct import ModelRequest, ModelResponse as ArealModelResponse
from aworld.core.llm_provider import LLMProviderBase
from aworld.models.llm import register_llm_provider
from aworld.models.model_response import ModelResponse, ToolCall
from aworld.utils.common import sync_exec

from vllm.entrypoints.openai.protocol import ExtractedToolCallInformation
from vllm.entrypoints.openai.tool_parsers import ToolParserManager


class ArealProvider(LLMProviderBase):
    """AReaL provider implementation."""

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
        self.provider = params.get("client")
        self.tokenizer = params.get("tokenizer")
        self.sampling_params = params.get("sampling_params", {})
        self.request_id = params.get("request_id")
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
        loop = asyncio.get_running_loop()
        prompt_ids = await loop.run_in_executor(
            None,
            lambda: self.tokenizer.apply_chat_template(
                messages,
                tools=kwargs.get("tools"),
                add_generation_prompt=True,
                tokenize=True,
            ),
        )
        rid = self.request_id or uuid.uuid4().hex
        req = ModelRequest(
            rid=rid,
            input_ids=prompt_ids,
            gconfig=GenerationHyperparameters(n_samples=1, **self.sampling_params),
            tokenizer=self.tokenizer,
        )
        response: ArealModelResponse = await self.provider.agenerate(req)
        content = self.tokenizer.decode(response.output_tokens, skip_special_tokens=True)

        tool_parser = ToolParserManager.get_tool_parser(self.tool_parser)
        res: ExtractedToolCallInformation = await tool_parser(self.tokenizer).extract_tool_calls(content)

        tool_calls = []
        if res.tools_called:
            tool_calls = [ToolCall(**tool_call.model_dump()) for tool_call in res.tool_calls]
        return ModelResponse(id=rid,
                             content=res.content,
                             tool_calls=tool_calls,
                             model=self.model_name,
                             raw_response=response)


register_llm_provider("areal", ArealProvider)
