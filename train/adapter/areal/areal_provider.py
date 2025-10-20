# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
import os
import time
import uuid
import random
from typing import List, Dict, Any

import aiohttp

from areal.api.cli_args import GenerationHyperparameters
from areal.api.io_struct import ModelRequest, ModelResponse as ArealModelResponse
from areal.engine.sglang_remote import RID_CACHE_SIZE
from areal.utils.http import get_default_connector, arequest_with_retry
from aworld.core.llm_provider import LLMProviderBase
from aworld.models.llm import register_llm_provider
from aworld.models.model_response import ModelResponse, ToolCall, Function
from aworld.utils.common import sync_exec
from aworld.logs.util import logger

from vllm.entrypoints.openai.protocol import ExtractedToolCallInformation
from vllm.entrypoints.openai.tool_parsers import ToolParserManager, ToolParser


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
        self.tokenizer = params.get("tokenizer")
        self.sampling_params = params.get("sampling_params", {})
        self.request_id = params.get("request_id")
        self.tool_parser = params.get("tool_parser")
        self.request_timeout = params.get("request_timeout", 3600)
        self.request_retries = params.get("request_retries", 3)

        self.rid_to_address = {}
        # Maintain the addresses for the recent 128 requests
        self.rid_queue = []
        self.addresses = []
        self.addresses = os.getenv("AREAL_LLM_SERVER_ADDRS").split(",")
        self.server_idx = random.randint(0, len(self.addresses) - 1)

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
            )
        )
        rid = self.request_id or uuid.uuid4().hex
        req = ModelRequest(
            rid=rid,
            input_ids=prompt_ids,
            gconfig=GenerationHyperparameters(n_samples=1, **self.sampling_params),
            tokenizer=self.tokenizer,
        )

        response: ArealModelResponse = await self.agenerate(req)

        content = await loop.run_in_executor(
            None,
            lambda: self.tokenizer.decode(response.output_tokens, skip_special_tokens=True)
        )

        tool_parser = ToolParserManager.get_tool_parser(self.tool_parser)
        res: ExtractedToolCallInformation = await loop.run_in_executor(
            None,
            lambda: tool_parser(self.tokenizer).extract_tool_calls(content, request=None)
        )

        tool_calls = []
        if res.tools_called:
            tool_calls = [ToolCall(**tool_call.model_dump()) for tool_call in res.tool_calls]
        return ModelResponse(id=rid,
                             content=res.content,
                             tool_calls=tool_calls,
                             model=self.model_name,
                             raw_response=ArealModelResponse(input_tokens=list(response.input_tokens),
                                                             output_tokens=list(response.output_tokens),
                                                             output_logprobs=list(response.output_logprobs),
                                                             output_versions=[-1] * len(prompt_ids)))

    async def agenerate(self, req: ModelRequest) -> ModelResponse:
        # from AReaL
        gconfig = req.gconfig
        stop_token_ids = gconfig.stop_token_ids
        stop = gconfig.stop

        sample_params = {
            "top_p": gconfig.top_p,
            "top_k": gconfig.top_k,
            "max_new_tokens": gconfig.max_new_tokens,
            "temperature": 0.0 if gconfig.greedy else gconfig.temperature,
            "stop_token_ids": stop_token_ids,
            "frequency_penalty": gconfig.frequency_penalty,
        }
        if stop:
            sample_params["stop"] = stop

        payload = {
            "input_ids": req.input_ids.copy(),
            "image_data": req.image_data,  # ImageObject or str
            "sampling_params": sample_params,
            "return_logprob": True,
            "stream": False,
        }

        # Make request
        start_time = time.perf_counter()
        accumulated_output_tokens = []
        accumulated_output_logprobs = []
        accumulated_versions = []

        # A single "rid" shares the same sever to allow KV cache reuse
        if req.rid in self.rid_to_address:
            server_addr = self.rid_to_address[req.rid]
        else:
            server_addr = self.addresses[self.server_idx]
            self.server_idx = (self.server_idx + 1) % len(self.addresses)
            if len(self.rid_queue) >= RID_CACHE_SIZE:
                # Remove the oldest entry if cache is full
                oldest_rid = self.rid_queue.pop(0)
                self.rid_to_address.pop(oldest_rid, None)
            self.rid_to_address[req.rid] = server_addr
            self.rid_queue.append(req.rid)

        # Create a new session because we don't know whether this method
        # is called in the workflow thread or the main thread.
        session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(
                total=self.request_timeout,
                sock_connect=self.request_timeout,
                connect=self.request_timeout,
            ),
            read_bufsize=1024 * 1024 * 10,
            connector=get_default_connector(),
        )

        # Deal with rollout interruption
        # "abort" is the stop reason for later v0.4.9.post2 after
        # we call the pause_generation endpoint
        stop_reason = None
        while (
                stop_reason not in ["stop", "tool_calls", "length"]
                and len(accumulated_output_tokens) < gconfig.max_new_tokens
        ):
            # loop until the generation is complete
            result = await arequest_with_retry(
                session=session,
                addr=server_addr,
                endpoint="/generate",
                payload=payload,
                method="POST",
                max_retries=self.request_retries,
                timeout=self.request_timeout,
            )

            meta_info = result["meta_info"]
            # Check if generation is complete
            finish_reason = meta_info["finish_reason"]
            stop_reason = finish_reason["type"]
            if (
                    stop_reason == "abort"
                    and finish_reason.get("message") == "Abort before prefill"
            ):
                continue

            # Parse response
            output_tokens = [x[1] for x in meta_info["output_token_logprobs"]]
            output_logprobs = [x[0] for x in meta_info["output_token_logprobs"]]

            # Update accumulated outputs
            accumulated_output_tokens.extend(output_tokens)
            accumulated_output_logprobs.extend(output_logprobs)
            # FIXME: Update with actual server versions
            accumulated_versions.extend([-1] * len(output_tokens))

            payload["input_ids"] += output_tokens
            sample_params["max_new_tokens"] -= len(output_tokens)

        if stop_reason == "abort":
            # If stop_reason is "abort", the only reason we exit the loop is
            # len(accumulated_output_tokens) >= gconfig.max_new_tokens
            # so the actual reason is length
            stop_reason = "length"
        await session.close()
        latency = time.perf_counter() - start_time

        response = ArealModelResponse(
            input_tokens=req.input_ids,
            input_images=req.image_data,
            output_tokens=accumulated_output_tokens,
            output_logprobs=accumulated_output_logprobs,
            output_versions=accumulated_versions,
            stop_reason=stop_reason,
            latency=latency,
            ttft=latency,  # Simplified for non-streaming
            tokenizer=req.tokenizer,
            processor=req.processor,
        )
        return response


register_llm_provider("areal", ArealProvider)
