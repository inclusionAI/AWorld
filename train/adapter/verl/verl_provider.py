# # coding: utf-8
# # Copyright (c) 2025 inclusionAI.
# import asyncio
# import uuid
# from typing import List, Dict, Any

# from aworld.core.llm_provider import LLMProviderBase
# from aworld.logs.util import logger
# from aworld.models.llm import register_llm_provider
# from aworld.models.model_response import ModelResponse, ToolCall, Function
# from aworld.utils.common import sync_exec

# from vllm.entrypoints.openai.protocol import ExtractedToolCallInformation
# from vllm.entrypoints.openai.tool_parsers import ToolParserManager, ToolParser


# class VerlProvider(LLMProviderBase):
#     """Verl vllm provider implementation."""

#     def __init__(self,
#                  api_key: str = None,
#                  base_url: str = None,
#                  model_name: str = None,
#                  sync_enabled: bool = None,
#                  async_enabled: bool = None,
#                  **kwargs):
#         super().__init__(api_key=api_key,
#                          base_url=base_url,
#                          model_name=model_name,
#                          sync_enabled=sync_enabled,
#                          async_enabled=async_enabled, **kwargs)

#         params = kwargs.get("params")
#         self.provider = params.get("client")
#         self.tokenizer = params.get("tokenizer")
#         self.sampling_params = params.get("sampling_params", {})
#         self.request_id = params.get("request_id")
#         self.tool_parser = params.get("tool_parser")

#     def _init_provider(self):
#         pass

#     def _init_async_provider(self):
#         pass

#     @classmethod
#     def supported_models(cls) -> list[str]:
#         return [""]

#     def postprocess_response(self, response: Any) -> ModelResponse:
#         pass

#     def completion(self, messages: List[Dict[str, str]], temperature: float = 0.0, max_tokens: int = None,
#                    stop: List[str] = None, **kwargs) -> ModelResponse:
#         return sync_exec(self.acompletion, messages, temperature, max_tokens, stop, **kwargs)

#     async def acompletion(self,
#                           messages: List[Dict[str, str]],
#                           temperature: float = 0.0,
#                           max_tokens: int = None,
#                           stop: List[str] = None,
#                           **kwargs) -> ModelResponse:
#         sampling_params = {
#             "temperature": temperature,
#             "top_p": kwargs.get('top_p', 1.0),
#             "top_k": kwargs.get('top_k', 80),
#             "repetition_penalty": kwargs.get('repetition_penalty', 1.0),
#         }
#         sampling_params.update(self.sampling_params)

#         loop = asyncio.get_running_loop()
#         prompt_ids = await loop.run_in_executor(
#             None,
#             lambda: self.tokenizer.apply_chat_template(
#                 messages,
#                 tools=kwargs.get("tools"),
#                 add_generation_prompt=True,
#                 tokenize=True,
#             ),
#         )
#         rid = self.request_id
#         response_output = await self.provider.generate(
#             request_id=rid, prompt_ids=prompt_ids, sampling_params=sampling_params
#         )
#         content = self.tokenizer.decode(response_output.token_ids, skip_special_tokens=True)
#         logger.warning(f"verl content: {content}")

#         # tool_parser = ToolParserManager.get_tool_parser(self.tool_parser)
#         # res: ExtractedToolCallInformation = tool_parser(self.tokenizer).extract_tool_calls(content, request=None)
#         # tool_calls = []
#         # if res.tools_called:
#         #     tool_calls = [ToolCall(**tool_call.model_dump()) for tool_call in res.tool_calls]

#         extract_con, tool_calls, error_info = HermesToolParser(self.tokenizer).extract_tool_calls(content)
#         if error_info:
#             logger.warning(f"extract_tool_calls: parse fail: {content}")
#             response_output = await self.provider.generate(
#                 request_id=rid, prompt_ids=prompt_ids, sampling_params=sampling_params
#             )
#             content = self.tokenizer.decode(response_output.token_ids, skip_special_tokens=True)
#             extract_con, tool_calls, error_info = HermesToolParser(self.tokenizer).extract_tool_calls(content)
#         return ModelResponse(id=rid,
#                              content=extract_con,
#                              tool_calls=tool_calls,
#                              model=self.model_name,
#                              raw_response=content)


# register_llm_provider("verl", VerlProvider)


# class HermesToolParser:
#     def __init__(self, tokenizer) -> None:
#         import regex as re
#         self.tokenizer = tokenizer

#         self.tool_call_start_token: str = "<tool_call>"
#         self.tool_call_end_token: str = "</tool_call>"
#         self.tool_call_regex = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL)

#     def extract_tool_calls(self, text):
#         import json
#         import logging

#         if self.tool_call_start_token not in text or self.tool_call_end_token not in text:
#             return text, []

#         matches = self.tool_call_regex.findall(text)
#         function_calls = []
#         error_info = None
#         for match in matches:
#             try:
#                 function_call = json.loads(match)
#                 function_calls.append(
#                     ToolCall(
#                         id=uuid.uuid4().hex,
#                         function=Function(
#                             name=function_call["name"],
#                             arguments=json.dumps(function_call["arguments"], ensure_ascii=False)
#                         )
#                     )
#                 )
#             except Exception as e:
#                 error_info = str(e)
#                 logging.error(f"Failed to decode tool call: {e}")

#         content = self.tool_call_regex.sub("", text)
#         return content, function_calls, error_info


# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
import uuid
import os
import time
from typing import List, Dict, Any

from aworld.core.llm_provider import LLMProviderBase
from aworld.logs.util import logger
from aworld.models.llm import register_llm_provider
from aworld.models.model_response import ModelResponse, ToolCall
from aworld.utils.common import sync_exec
from train.adapter.common import encode_messages

from aworld.logs.util import logger
from aworld.models.model_response import ModelResponse, ToolCall, Function
from verl.experimental.agent_loop.tool_parser import ToolParser
import json

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
        logger.info(f"[VerlProvider] Starting initialization - model_name={model_name}, sync_enabled={sync_enabled}, async_enabled={async_enabled}")

        super().__init__(api_key=api_key,
                         base_url=base_url,
                         model_name=model_name,
                         sync_enabled=sync_enabled,
                         async_enabled=async_enabled, **kwargs)

        params = kwargs.get("params")
        logger.info(f"[VerlProvider] Initialization parameters: {params}")

        self.provider = params.get("client")
        self.tokenizer = params.get("tokenizer")
        self.sampling_params = params.get("sampling_params", {'temperature': 1.0, 'top_p': 1.0, 'top_k': 80, 'repetition_penalty': 1.0})
        self.request_id = params.get("request_id")
        self.tool_parser = params.get("tool_parser")

        # Validate key components
        if not self.provider:
            logger.error("[VerlProvider] Missing provider client")
        if not self.tokenizer:
            logger.error("[VerlProvider] Missing tokenizer")
        if not self.request_id:
            logger.warning("[VerlProvider] Missing request_id, will use default value")

        logger.info(f"[VerlProvider] Initialization completed - request_id={self.request_id}, param_task_id={params.get('task_id')}, param_request_id={params.get('request_id')}, tool_parser={self.tool_parser}")
        logger.info(f"[VerlProvider] Sampling parameters: {self.sampling_params}")
        logger.info(f"[VerlProvider] Provider type: {type(self.provider).__name__ if self.provider else 'None'}")
        logger.info(f"[VerlProvider] Tokenizer type: {type(self.tokenizer).__name__ if self.tokenizer else 'None'}")

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
        logger.info(f"[VerlProvider] Starting async completion - request_id={self.request_id}, temperature={temperature}, max_tokens={max_tokens}")
        logger.info(f"[VerlProvider] Message count: {len(messages)}, role distribution: {[msg.get('role', 'unknown') for msg in messages]}")
        logger.info(f"[VerlProvider] Tools provided: {bool(kwargs.get('tools'))}")
        if kwargs.get('tools'):
            logger.info(f"[VerlProvider] Tool count: {len(kwargs.get('tools', []))}")
            logger.info(f"[VerlProvider] Tool names: {[tool.get('function', {}).get('name', 'unknown') for tool in kwargs.get('tools', [])]}")

        start_time = time.time()

        sampling_params = {
            "temperature": temperature,
            "top_p": kwargs.get('top_p', 1.0),
            "top_k": kwargs.get('top_k', 80),
            "repetition_penalty": kwargs.get('repetition_penalty', 1.0),
        }
        sampling_params.update(self.sampling_params)
        logger.info(f"[VerlProvider] Final sampling parameters: {sampling_params}")

        loop = asyncio.get_running_loop()
        logger.info(f"[VerlProvider] Starting chat template application - request_id={self.request_id}")
        template_start = time.time()

        try:
            prompt_ids = await loop.run_in_executor(
                None,
                lambda: self.tokenizer.apply_chat_template(
                    messages,
                    tools=kwargs.get("tools"),
                    add_generation_prompt=True,
                    tokenize=True,
                ),
            )
            template_time = time.time() - template_start
            logger.info(f"[VerlProvider] Chat template application completed - time taken: {template_time:.3f}s, prompt_ids length: {len(prompt_ids)}")
            logger.debug(f"[VerlProvider] Message content: {messages}")
            logger.debug(f"[VerlProvider] prompt_ids: {prompt_ids}")

        except Exception as e:
            template_time = time.time() - template_start
            logger.error(f"[VerlProvider] Chat template application failed - time taken: {template_time:.3f}s, error: {e}")
            raise

        rid = self.request_id
        logger.info(f"[VerlProvider] Starting response generation - request_id={rid}, timeout: 120s")

        try:
            generation_start = time.time()
            response_output = await self.provider.generate(
                request_id=rid,
                prompt_ids=prompt_ids,
                sampling_params=sampling_params
            )
            generation_time = time.time() - generation_start
            logger.info(f"[VerlProvider] Generation completed - time taken: {generation_time:.3f}s, request_id={rid}")

            decode_start = time.time()
            decoded_content = self.tokenizer.decode(response_output.token_ids, skip_special_tokens=True)
            decode_time = time.time() - decode_start
            logger.info(f"[VerlProvider] Content decoding completed - time taken: {decode_time:.3f}s, content length: {len(decoded_content)}")
            logger.info(f"[VerlProvider] Generated content preview: {decoded_content[:200]}...")
            logger.debug(f"[VerlProvider] Full generated content: {decoded_content}")

        except asyncio.TimeoutError:
            elapsed_time = time.time() - start_time
            logger.warning(f"[VerlProvider] Request timeout - request_id={rid}, total time taken: {elapsed_time:.2f}s, process ID: {os.getpid()}")
            decoded_content = "Request timed out. Please try again."
            
            class DefaultResponse:
                def __init__(self, tokenizer, content):
                    self.tokenizer = tokenizer
                    self.token_ids = tokenizer.encode(content, add_special_tokens=False)

            response_output = DefaultResponse(self.tokenizer, decoded_content)
            logger.warning(f"[VerlProvider] Creating default response for timeout request - request_id={rid}")

        except Exception as e:
            elapsed_time = time.time() - start_time
            logger.error(f"[VerlProvider] Generation failed - request_id={rid}, time taken: {elapsed_time:.2f}s, error: {e}")
            logger.error(f"[VerlProvider] Error details: {type(e).__name__}: {str(e)}")
            raise

        logger.info(f"[VerlProvider] Starting tool call extraction - parser: {self.tool_parser}")
        tool_parser = ToolParser.get_tool_parser(self.tool_parser, self.tokenizer)

        extract_start = time.time()
        try:
            content, function_calls = await tool_parser.extract_tool_calls(response_output.token_ids)
            extract_time = time.time() - extract_start
            logger.info(f"[VerlProvider] Tool call extraction completed - time taken: {extract_time:.3f}s, found tool calls: {len(function_calls)}")
        except Exception as e:
            extract_time = time.time() - extract_start
            logger.error(f"[VerlProvider] Tool call extraction failed - time taken: {extract_time:.3f}s, error: {e}")
            content = decoded_content
            function_calls = []

        tool_calls = []
        valid_tool_calls = 0
        invalid_tool_calls = 0

        logger.info(f"[VerlProvider] Starting tool call validation - total count: {len(function_calls)}")
        for i, function_call in enumerate(function_calls):
            error = None
            try:
                args = json.loads(function_call.arguments)
                if not isinstance(args, dict):
                    error = f"Tool parameters must be JSON object, actual type: {type(args).__name__}"
            except json.JSONDecodeError as e:
                error = f"Invalid JSON tool parameters: {e}"

            if error:
                logger.warning(f"[VerlProvider] Tool call {i+1} validation failed: {error}")
                logger.debug(f"[VerlProvider] Failed tool call details: name={function_call.name}, arguments={function_call.arguments}")
                invalid_tool_calls += 1
            else:
                tool_calls.append(
                    ToolCall(
                        id=str(uuid.uuid4()),
                        function=Function(
                            name=function_call.name,
                            arguments=function_call.arguments,
                        )
                    )
                )
                valid_tool_calls += 1
                logger.info(f"[VerlProvider] Tool call {i+1} validation successful: {function_call.name}")
                logger.debug(f"[VerlProvider] Successful tool call details: name={function_call.name}, arguments={function_call.arguments}")

        # Calculate token usage
        prompt_tokens = len(prompt_ids) if prompt_ids else 0
        completion_tokens = len(response_output.token_ids) if hasattr(response_output, 'token_ids') else 0
        total_tokens = prompt_tokens + completion_tokens

        total_time = time.time() - start_time
        logger.info(f"[VerlProvider] Request completed - request_id={rid}, total time taken: {total_time:.2f}s")
        logger.info(f"[VerlProvider] Tool call statistics - valid: {valid_tool_calls}, invalid: {invalid_tool_calls}")
        logger.info(f"[VerlProvider] Token usage statistics - input: {prompt_tokens}, output: {completion_tokens}, total: {total_tokens}")
        logger.info(f"[VerlProvider] Final response - content length: {len(content)}, tool call count: {len(tool_calls)}")

        return ModelResponse(
            id=rid,
            content=content,
            tool_calls=tool_calls,
            model=self.model_name,
            raw_response=content,
            usage={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens
            }
        )

register_llm_provider("verl", VerlProvider)
