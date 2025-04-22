import os
from typing import Any, Dict, List, Generator, AsyncGenerator

from binascii import b2a_hex, a2b_hex
import ast
import requests
import json
import time
from typing import (
    Any,
    Optional,
    List,
    Dict,
    Union,
    Generator,
    AsyncGenerator,
)

from aworld.config.conf import ClientType
from aworld.models.llm_provider_base import LLMProviderBase
from aworld.models.llm_http_handler import LLMHTTPHandler
from aworld.models.model_response import ModelResponse, LLMResponseError, ToolCall
from aworld.logs.util import logger
from aworld.utils import import_package


# Custom JSON encoder to handle ToolCall and other special types
class CustomJSONEncoder(json.JSONEncoder):
    """Custom JSON encoder to handle ToolCall objects and other special types."""
    
    def default(self, obj):
        # Handle objects with to_dict method
        if hasattr(obj, 'to_dict') and callable(obj.to_dict):
            return obj.to_dict()
            
        # Handle objects with __dict__ attribute (most custom classes)
        if hasattr(obj, '__dict__'):
            return obj.__dict__
            
        # Let the base class handle it (will raise TypeError if not serializable)
        return super().default(obj)


class ShangshuProvider(LLMProviderBase):
    """Shangshu provider implementation.
    """

    def _init_provider(self):
        """Initialize Shangshu provider.
        
        Returns:
            Shangshu provider instance.
        """
        import_package("Crypto.Cipher.AES")
        # Get API key
        api_key = self.api_key
        if not api_key:
            env_var = "SHANGSHU_API_KEY"
            api_key = os.getenv(env_var, "")
            if not api_key:
                raise ValueError(
                    f"SHANGSHU API key not found, please set {env_var} environment variable or provide it in the parameters")
        base_url = self.base_url
        if not base_url:
            base_url = os.getenv("SHANGSHU_ENDPOINT", "https://zdfmng.alipay.com")

        self.aes_key = os.getenv("SHANGSHU_AES_KEY", "")

        self.is_http_provider = True
        self.kwargs["client_type"] = ClientType.HTTP
        logger.info(f"Using HTTP provider for Shangshu")
        self.http_provider = LLMHTTPHandler(
            base_url=base_url,
            api_key=api_key,
            model_name=self.model_name,
        )
        self.is_http_provider = True
        self.request_params = {
            # "serviceName": "chatgpt_response_query_dataview",
            "visitDomain": "BU_general",
            "visitBiz": "BU_general_gpt4",
            "visitBizLine": "BU_general_gpt4_wuman",
            "cacheInterval": "-1",
            "queryConditions": {}
        }
        return self.http_provider

    def _init_async_provider(self):
        """Initialize async Shangshu provider.

        Returns:
            Async Shangshu provider instance.
        """
        # Get API key
        if not self.provider:
            provider = self._init_provider()
            return provider

    @classmethod
    def supported_models(cls) -> list[str]:
        return [""]

    def aes_encrypt(self, data, key):
        """AES encryption function. If data is not a multiple of 16 [encrypted data must be a multiple of 16!], pad it to a multiple of 16.
        
        Args:
            key: Encryption key
            data: Data to encrypt
        
        Returns:
            Encrypted data
        """
        from Crypto.Cipher import AES

        iv = "1234567890123456"
        cipher = AES.new(key.encode('utf-8'), AES.MODE_CBC, iv.encode('utf-8'))
        block_size = AES.block_size

        # Check if data is a multiple of 16, if not, pad with b'\0'
        if len(data) % block_size != 0:
            add = block_size - (len(data) % block_size)
        else:
            add = 0
        data = data.encode('utf-8') + b'\0' * add
        encrypted = cipher.encrypt(data)
        result = b2a_hex(encrypted)
        return result.decode('utf-8')

    def query_response_data(self, message_key, timeout=60):
        """
        Get the LLM response result by polling once per second until a result is received or timeout occurs.
        
        Args:
            message_key: Unique identifier for the message
            timeout: Timeout in seconds, default is 60 seconds
            
        Returns:
            Parsed LLM response data
            
        Raises:
            LLMResponseError: When timeout or query failure occurs
        """
        message_key_str = str(message_key) if message_key is not None else ""

        param = self.request_params.copy()
        param["serviceName"] = "chatgpt_response_query_dataview"
        param["queryConditions"]["messageKey"] = message_key_str

        url = 'commonQuery/queryData'
        data = json.dumps(param, cls=CustomJSONEncoder)
        encryptedParam = self.aes_encrypt(data, self.aes_key)
        post_data = {
            "encryptedParam": encryptedParam
        }
        headers = {
            'Content-Type': 'application/json'
        }
        
        # Start polling until valid result or timeout
        start_time = time.time()
        elapsed_time = 0
        
        while elapsed_time < timeout:
            try:
                response = self.http_provider.sync_call(post_data, endpoint=url, headers=headers)
                
                logger.debug(f"Poll attempt at {elapsed_time}s, response: {response}")
                
                # Check if valid result is received
                if (response and 
                    "data" in response and 
                    "values" in response["data"] and 
                    response["data"]["values"] and 
                    "response" in response["data"]["values"]):
                    
                    x = response["data"]["values"]["response"]
                    ast_str = ast.literal_eval("'" + x + "'")
                    
                    js = ast_str.replace('&quot;', '"')
                    js = js.replace("&#39;", "'")
                    data = json.loads(js)
                    return data
                
                # If no result, wait 1 second and query again
                time.sleep(1)
                elapsed_time = time.time() - start_time
                logger.debug(f"Polling... Elapsed time: {elapsed_time:.1f}s")
                
            except Exception as e:
                logger.warn(f"Error during polling: {e}")
                time.sleep(1)
                elapsed_time = time.time() - start_time
        
        # Timeout handling
        raise LLMResponseError(
            f"Timeout after {timeout} seconds waiting for response from Shangshu API",
            self.model_name or "unknown"
        )

    def preprocess_message_with_args(self, messages: List[Dict[str, str]], ext_params: Dict[str, Any]) -> List[Dict[str, str]]:
        """Preprocess messages, use Shangshu format directly.

        Args:
            messages: Shangshu format message list.

        Returns:
            Processed message list.
        """
        query_conditions = {
            "model": self.model_name,
            "n": "1",
            "api_key": self.api_key,
            "messageKey": self.message_key,
            "outputType": "PULL",
            "messages": messages,
        }
        param = self.request_params.copy()
        param["serviceName"] = "asyn_chatgpt_prompts_completions_query_dataview"
        param["queryConditions"].update(query_conditions)

        for k in ext_params.keys():
            if k not in param["queryConditions"]:
                param["queryConditions"][k] = ext_params[k]

        data = json.dumps(param, cls=CustomJSONEncoder)
        encrypted_param = self.aes_encrypt(data, self.aes_key)
        return [{"encryptedParam": encrypted_param}]


    def postprocess_response(self, response: Any) -> ModelResponse:
        """Process Shangshu response.

        Args:
            response: Shangshu response object.

        Returns:
            ModelResponse object.
            
        Raises:
            LLMResponseError: When LLM response error occurs.
        """
        if ((not isinstance(response, dict) and (not hasattr(response, 'choices') or not response.choices))
                or (isinstance(response, dict) and not response.get("choices"))):
            error_msg = ""
            if hasattr(response, 'error') and response.error and isinstance(response.error, dict):
                error_msg = response.error.get('message', '')
            elif hasattr(response, 'msg'):
                error_msg = response.msg

            raise LLMResponseError(
                error_msg if error_msg else "Unknown error",
                self.model_name or "unknown",
                response
            )

        return ModelResponse.from_openai_response(response)

    def postprocess_stream_response(self, chunk: Any) -> ModelResponse:
        """Process Shangshu streaming response chunk.

        Args:
            chunk: Shangshu response chunk.

        Returns:
            ModelResponse object.
            
        Raises:
            LLMResponseError: When LLM response error occurs.
        """
        # Check if chunk contains error
        if hasattr(chunk, 'error') or (isinstance(chunk, dict) and chunk.get('error')):
            error_msg = chunk.error if hasattr(chunk, 'error') else chunk.get('error', 'Unknown error')
            raise LLMResponseError(
                error_msg,
                self.model_name or "unknown",
                chunk
            )

        return ModelResponse.from_openai_stream_chunk(chunk)

    def completion(self,
                   messages: List[Dict[str, str]],
                   temperature: float = 0.0,
                   max_tokens: int = None,
                   stop: List[str] = None,
                   **kwargs) -> ModelResponse:
        """Synchronously call Shangshu to generate response.
        
        Args:
            messages: Message list.
            temperature: Temperature parameter.
            max_tokens: Maximum number of tokens to generate.
            stop: List of stop sequences.
            **kwargs: Other parameters.

        Returns:
            ModelResponse object.
            
        Raises:
            LLMResponseError: When LLM response error occurs.
        """
        if not self.provider:
            raise RuntimeError(
                "Sync provider not initialized. Make sure 'sync_enabled' parameter is set to True in initialization.")
        start_time = time.time()
        timestamp = int(time.time())
        self.message_key = f"llm_call_{timestamp}"
        message_key_literal = self.message_key
        self.aes_key = kwargs.get("aes_key", self.aes_key)
        processed_messages = self.preprocess_message_with_args(messages, self.build_openai_params(temperature, max_tokens, stop, **kwargs))
        if not processed_messages:
            raise LLMResponseError("Failed to get post data", self.model_name or "unknown")

        try:
            # Use http_provider to make the request instead of directly using requests
            response = self.http_provider.sync_call(processed_messages[0], endpoint="commonQuery/queryData")

            # Call query_response_data with string literal, set timeout to 120 seconds or get from kwargs
            timeout = kwargs.get("response_timeout", 10)
            raw_output = self.query_response_data(message_key_literal, timeout=timeout)
            logger.debug(f"llm raw_output is: {raw_output}")
            logger.info(f"completion cost time: {time.time() - start_time}s.")

            return self.postprocess_response(raw_output)
        except Exception as e:
            if isinstance(e, LLMResponseError):
                raise e
            logger.warn(f"Error in Shangshu completion: {e}")
            raise LLMResponseError(str(e), kwargs.get("model_name", self.model_name or "unknown"))

    def stream_completion(self,
                          messages: List[Dict[str, str]],
                          temperature: float = 0.0,
                          max_tokens: int = None,
                          stop: List[str] = None,
                          **kwargs) -> Generator[ModelResponse, None, None]:
        """Synchronously call Shangshu to generate streaming response.
        
        Args:
            messages: Message list.
            temperature: Temperature parameter.
            max_tokens: Maximum number of tokens to generate.
            stop: List of stop sequences.
            **kwargs: Other parameters.

        Returns:
            Generator yielding ModelResponse chunks.
            
        Raises:
            LLMResponseError: When LLM response error occurs.
        """
        if not self.provider:
            raise RuntimeError(
                "Sync provider not initialized. Make sure 'sync_enabled' parameter is set to True in initialization.")
        
        start_time = time.time()
        # Generate message_key
        timestamp = int(time.time())
        self.message_key = f"llm_call_{timestamp}"
        message_key_literal = self.message_key  # Ensure it's a direct string literal
        self.aes_key = kwargs.get("aes_key", self.aes_key)
        
        # Add streaming parameter
        kwargs["stream"] = True
        processed_messages = self.preprocess_message_with_args(messages, self.build_openai_params(temperature, max_tokens, stop, **kwargs))
        if not processed_messages:
            raise LLMResponseError("Failed to get post data", self.model_name or "unknown")

        try:
            # Send request
            response = self.http_provider.sync_call(processed_messages[0], endpoint="commonQuery/queryData")
            
            # Get timeout value from kwargs or use default
            timeout = kwargs.get("response_timeout", 10)
            raw_output = self.query_response_data(message_key_literal, timeout=timeout)
            logger.debug(f"llm raw_output is: {raw_output}")
            
            # For streaming response, we need to parse the raw_output and yield chunks
            # Assuming raw_output contains a list of deltas or chunks in the format compatible with Shangshu's streaming
            if isinstance(raw_output, dict) and "choices" in raw_output and isinstance(raw_output["choices"], list):
                # First yield the initial response
                yield self.postprocess_response(raw_output)
                
                # If there's streaming data, process and yield it
                if "stream_data" in raw_output and isinstance(raw_output["stream_data"], list):
                    for chunk in raw_output["stream_data"]:
                        yield self.postprocess_stream_response(chunk)
            else:
                # If streaming data format is not as expected, yield the entire response as one chunk
                yield self.postprocess_response(raw_output)
                
            logger.info(f"stream_completion cost time: {time.time() - start_time}s.")
        except Exception as e:
            if isinstance(e, LLMResponseError):
                raise e
            logger.warn(f"Error in Shangshu stream completion: {e}")
            raise LLMResponseError(str(e), kwargs.get("model_name", self.model_name or "unknown"))

    async def acompletion(self,
                          messages: List[Dict[str, str]],
                          temperature: float = 0.0,
                          max_tokens: int = None,
                          stop: List[str] = None,
                          **kwargs) -> ModelResponse:
        """Asynchronously call Shangshu to generate response.
        
        Args:
            messages: Message list.
            temperature: Temperature parameter.
            max_tokens: Maximum number of tokens to generate.
            stop: List of stop sequences.
            **kwargs: Other parameters.

        Returns:
            ModelResponse object.
            
        Raises:
            LLMResponseError: When LLM response error occurs.
        """
        if not self.async_provider:
            self._init_async_provider()
            
        start_time = time.time()
        # Generate message_key
        timestamp = int(time.time())
        self.message_key = f"llm_call_{timestamp}"
        message_key_literal = self.message_key  # Ensure it's a direct string literal
        self.aes_key = kwargs.get("aes_key", self.aes_key)
        
        processed_messages = self.preprocess_message_with_args(messages, self.build_openai_params(temperature, max_tokens, stop, **kwargs))
        if not processed_messages:
            raise LLMResponseError("Failed to get post data", self.model_name or "unknown")

        try:
            # Use async version of the http call
            response = await self.http_provider.async_call(processed_messages[0], endpoint="commonQuery/queryData")
            
            # Get timeout value from kwargs or use default
            timeout = kwargs.get("response_timeout", 10)
            
            # For async version, we need to implement an async version of query_response_data
            # Since we don't have that yet, we'll use the synchronous version in an async wrapper
            import asyncio
            raw_output = await asyncio.to_thread(self.query_response_data, message_key_literal, timeout=timeout)
            
            logger.debug(f"llm raw_output is: {raw_output}")
            logger.info(f"acompletion cost time: {time.time() - start_time}s.")

            return self.postprocess_response(raw_output)
        except Exception as e:
            if isinstance(e, LLMResponseError):
                raise e
            logger.warn(f"Error in async Shangshu completion: {e}")
            raise LLMResponseError(str(e), kwargs.get("model_name", self.model_name or "unknown"))

    async def astream_completion(self,
                                 messages: List[Dict[str, str]],
                                 temperature: float = 0.0,
                                 max_tokens: int = None,
                                 stop: List[str] = None,
                                 **kwargs) -> AsyncGenerator[ModelResponse, None]:
        """Asynchronously call Shangshu to generate streaming response.
        
        Args:
            messages: Message list.
            temperature: Temperature parameter.
            max_tokens: Maximum number of tokens to generate.
            stop: List of stop sequences.
            **kwargs: Other parameters.

        Returns:
            AsyncGenerator yielding ModelResponse chunks.
            
        Raises:
            LLMResponseError: When LLM response error occurs.
        """
        if not self.async_provider:
            self._init_async_provider()
            
        start_time = time.time()
        # Generate message_key
        timestamp = int(time.time())
        self.message_key = f"llm_call_{timestamp}"
        message_key_literal = self.message_key  # Ensure it's a direct string literal
        self.aes_key = kwargs.get("aes_key", self.aes_key)
        
        # Add streaming parameter
        kwargs["stream"] = True
        processed_messages = self.preprocess_message_with_args(messages, self.build_openai_params(temperature, max_tokens, stop, **kwargs))
        if not processed_messages:
            raise LLMResponseError("Failed to get post data", self.model_name or "unknown")

        try:
            # Use async version of the http call
            response = await self.http_provider.async_call(processed_messages[0], endpoint="commonQuery/queryData")
            
            # Get timeout value from kwargs or use default
            timeout = kwargs.get("response_timeout", 10)
            
            # For async version, we need to implement an async version of query_response_data
            # Since we don't have that yet, we'll use the synchronous version in an async wrapper
            import asyncio
            raw_output = await asyncio.to_thread(self.query_response_data, message_key_literal, timeout=timeout)
            
            logger.debug(f"llm raw_output is: {raw_output}")
            
            # For streaming response, we need to parse the raw_output and yield chunks
            # Assuming raw_output contains a list of deltas or chunks in the format compatible with Shangshu's streaming
            if isinstance(raw_output, dict) and "choices" in raw_output and isinstance(raw_output["choices"], list):
                # First yield the initial response
                yield self.postprocess_response(raw_output)
                
                # If there's streaming data, process and yield it
                if "stream_data" in raw_output and isinstance(raw_output["stream_data"], list):
                    for chunk in raw_output["stream_data"]:
                        yield self.postprocess_stream_response(chunk)
            else:
                # If streaming data format is not as expected, yield the entire response as one chunk
                yield self.postprocess_response(raw_output)
                
            logger.info(f"astream_completion cost time: {time.time() - start_time}s.")
        except Exception as e:
            if isinstance(e, LLMResponseError):
                raise e
            logger.warn(f"Error in async Shangshu stream completion: {e}")
            raise LLMResponseError(str(e), kwargs.get("model_name", self.model_name or "unknown"))

    def build_openai_params(self,
                          messages: List[Dict[str, str]],
                          temperature: float = 0.0,
                          max_tokens: int = None,
                          stop: List[str] = None,
                          **kwargs) -> Dict[str, Any]:
        openai_params = {
            "model": kwargs.get("model_name", self.model_name or ""),
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stop": stop
        }

        supported_params = [
            "frequency_penalty", "logit_bias", "logprobs", "top_logprobs",
            "presence_penalty", "response_format", "seed", "stream", "top_p",
            "user", "function_call", "functions", "tools", "tool_choice"
        ]

        for param in supported_params:
            if param in kwargs:
                openai_params[param] = kwargs[param]

        return openai_params