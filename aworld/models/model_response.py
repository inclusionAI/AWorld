import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from aworld.logs.util import logger
from aworld.models.utils import normalize_usage


class LLMResponseError(Exception):
    """Represents an error in LLM response.
    
    Attributes:
        message: Error message
        model: Model name
        response: Original response object
        error_details: Optional structured error details (e.g. status_code, request_id)
    """

    def __init__(
        self,
        message: str,
        model: str = "unknown",
        response: Any = None,
        error_details: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize LLM response error
        
        Args:
            message: Error message
            model: Model name
            response: Original response object
            error_details: Optional structured error details
        """
        self.message = message
        self.model = model
        self.response = response
        self.error_details = error_details or None

        details_str = ""
        if self.error_details:
            try:
                details_str = f". Details: {json.dumps(self.error_details, ensure_ascii=False)}"
            except Exception:
                details_str = f". Details: {self.error_details}"

        super().__init__(f"LLM Error ({model}): {message}{details_str}. Response: {response}")


class Function(BaseModel):
    """
    Represents a function call made by a model
    """
    name: str
    arguments: Optional[str] = None


class ToolCall(BaseModel):
    """
    Represents a tool call made by a model
    """

    id: str
    type: str = "function"
    function: Function = None
    extra_content: Optional[dict] = None

    # name: str = None
    # arguments: str = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ToolCall':
        """
        Create ToolCall from dictionary representation

        Args:
            data: Dictionary containing tool call data

        Returns:
            ToolCall object
        """
        if not data:
            return None

        tool_id = data.get('id')
        if not tool_id:
            tool_id = f"call_{hash(str(data)) & 0xffffffff:08x}"
        tool_type = data.get('type')
        if not tool_type:
            tool_type = 'function'

        function_data = data.get('function', {})
        name = function_data.get('name')
        if not name:
            name = "unknown"

        arguments = function_data.get('arguments')
        # Ensure arguments is a string
        if arguments is not None and not isinstance(arguments, str):
            arguments = json.dumps(arguments, ensure_ascii=False)

        function = Function(name=name, arguments=arguments)
        if 'model_extra' in data and 'extra_content' in data['model_extra']:
            extra_content = data['model_extra']['extra_content']
        else:
            extra_content = None
        if 'extra_content' in data:
            extra_content = data['extra_content']

        return cls(
            id=tool_id,
            type=tool_type,
            function=function,
            extra_content=extra_content
            # name=name,
            # arguments=arguments,
        )

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert ToolCall to dictionary representation

        Returns:
            Dictionary representation
        """
        return {
            "id": self.id,
            "type": self.type,
            "function": {
                "name": self.function.name,
                "arguments": self.function.arguments
            },
            "extra_content": self.extra_content
        }

    def __repr__(self):
        return json.dumps(self.to_dict(), ensure_ascii=False)

    def __iter__(self):
        """
        Make ToolCall dict-like for JSON serialization
        """
        yield from self.to_dict().items()


class VideoGenerationResult:
    """Holds the result of a video generation task."""

    def __init__(
            self,
            task_id: str = None,
            video_url: str = None,
            status: str = None,
            duration: float = None,
            resolution: str = None,
            extra: Dict[str, Any] = None,
    ):
        """
        Args:
            task_id: Video generation task ID, used for async polling.
            video_url: URL of the generated video, available when task is completed.
            status: Task status, e.g. 'submitted', 'processing', 'succeeded', 'failed'.
            duration: Duration of the generated video in seconds.
            resolution: Resolution of the generated video, e.g. '1280x720'.
            extra: Additional provider-specific fields.
        """
        self.task_id = task_id
        self.video_url = video_url
        self.status = status
        self.duration = duration
        self.resolution = resolution
        self.extra = extra or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "video_url": self.video_url,
            "status": self.status,
            "duration": self.duration,
            "resolution": self.resolution,
            "extra": self.extra,
        }

    def __repr__(self):
        import json
        return json.dumps(self.to_dict(), ensure_ascii=False)


class ModelResponse:
    """
    Unified model response class for encapsulating responses from different LLM providers
    """

    def __init__(
            self,
            id: str,
            model: str,
            content: str = None,
            tool_calls: List[ToolCall] = None,
            usage: Dict[str, int] = None,
            raw_usage: Dict[str, Any] = None,
            provider_request_id: Optional[str] = None,
            error: str = None,
            raw_response: Any = None,
            message: Dict[str, Any] = None,
            reasoning_content: str = None,
            finish_reason: str = None,
            reasoning_details: Dict[str, Any] = None,
            video_result: VideoGenerationResult = None,
    ):
        """
        Initialize ModelResponse object

        Args:
            id: Response ID
            model: Model name used
            content: Generated text content
            tool_calls: List of tool calls
            usage: Usage statistics (token counts, etc.)
            error: Error message (if any)
            raw_response: Original response object
            message: Complete message object, can be used for subsequent API calls
            video_result: Video generation result, populated when using video generation interfaces.
        """
        self.id = id
        self.model = model
        self.content = content
        self.tool_calls = tool_calls
        self.usage = normalize_usage(usage) if usage is not None else {
            "completion_tokens": 0,
            "prompt_tokens": 0,
            "total_tokens": 0
        }
        self.raw_usage = raw_usage or dict(self.usage)
        self.provider_request_id = provider_request_id
        self.error = error
        self.raw_response = raw_response
        self.video_result = video_result

        # If message is not provided, construct one from other fields
        if message is None:
            self.message = {
                "role": "assistant",
                "content": content
            }

            if tool_calls:
                self.message["tool_calls"] = [tool_call.to_dict() for tool_call in tool_calls]
        else:
            self.message = message

        self.reasoning_content = reasoning_content

        self.created_at = datetime.now().isoformat()

        self.finish_reason = finish_reason

        self.reasoning_details = reasoning_details

        self.structured_output = dict()

    @classmethod
    def _get_item_from_openai_message(cls, message:Any, key: str, default_value: Any = None) -> Any:
        if not message:
            return default_value
        if hasattr(message, key):
            return getattr(message, key, default_value)
        elif isinstance(message, dict):
            return message.get(key, default_value)
        return default_value

    @classmethod
    def _normalize_openai_usage(cls, usage: Any) -> Dict[str, int]:
        return {
            "completion_tokens": cls._get_item_from_openai_message(usage, 'completion_tokens', 0) or 0,
            "prompt_tokens": cls._get_item_from_openai_message(usage, 'prompt_tokens', 0) or 0,
            "total_tokens": cls._get_item_from_openai_message(usage, 'total_tokens', 0) or 0,
        }

    @classmethod
    def _extract_openai_raw_usage(cls, usage: Any) -> Dict[str, Any]:
        if not usage:
            return {}
        if isinstance(usage, dict):
            return dict(usage)
        if hasattr(usage, "model_dump"):
            return usage.model_dump(exclude_none=True)
        if hasattr(usage, "__dict__"):
            return {
                key: value
                for key, value in usage.__dict__.items()
                if not key.startswith("_") and value is not None
            }
        return cls._normalize_openai_usage(usage)

    @classmethod
    def _extract_openai_provider_request_id(cls, response: Any) -> Optional[str]:
        if isinstance(response, dict):
            return response.get("request_id") or response.get("_request_id")
        return getattr(response, "request_id", None) or getattr(response, "_request_id", None)

    @classmethod
    def from_openai_response(cls, response: Any) -> 'ModelResponse':
        """
        Create ModelResponse from OpenAI response object

        Args:
            response: OpenAI response object

        Returns:
            ModelResponse object
            
        Raises:
            LLMResponseError: When LLM response error occurs
        """
        # Handle error cases
        if hasattr(response, 'error') or (isinstance(response, dict) and response.get('error')):
            error_msg = response.error if hasattr(response, 'error') else response.get('error', 'Unknown error')
            raise LLMResponseError(
                error_msg,
                response.model if hasattr(response, 'model') else response.get('model', 'unknown'),
                response
            )

        # Normal case
        message = None
        finish_reason = None
        if hasattr(response, 'choices') and response.choices:
            message = response.choices[0].message
            finish_reason = response.choices[0].finish_reason
        elif isinstance(response, dict) and response.get('choices'):
            message = response['choices'][0].get('message', {})
            finish_reason = response['choices'][0].get('finish_reason')

        if not message:
            raise LLMResponseError(
                "No message found in response",
                response.model if hasattr(response, 'model') else response.get('model', 'unknown'),
                response
            )

        # Extract usage information
        response_usage = response.usage if hasattr(response, 'usage') else response.get('usage') if isinstance(response, dict) else None
        usage = cls._normalize_openai_usage(response_usage) if response_usage else {}
        raw_usage = cls._extract_openai_raw_usage(response_usage)
        provider_request_id = cls._extract_openai_provider_request_id(response)

        # Build message object
        message_dict = {}
        if hasattr(message, '__dict__'):
            # Convert object to dictionary
            for key, value in message.__dict__.items():
                if not key.startswith('_'):
                    message_dict[key] = value
        elif isinstance(message, dict):
            message_dict = message
        else:
            # Extract common properties
            message_dict = {
                "role": "assistant",
                "content": message.content if hasattr(message, 'content') else "",
                "tool_calls": message.tool_calls if hasattr(message, 'tool_calls') else None,
            }

        message_dict["content"] = '' if message_dict.get('content') is None else message_dict.get('content', '')
        reasoning_content = cls._get_item_from_openai_message(message, 'reasoning_content')
        if not reasoning_content:
            model_extra = cls._get_item_from_openai_message(message, 'model_extra', {})
            reasoning_content = model_extra.get('reasoning', "")

        # Process tool calls
        processed_tool_calls = []
        raw_tool_calls = message.tool_calls if hasattr(message, 'tool_calls') else message_dict.get('tool_calls')

        message_content = cls._get_item_from_openai_message(message, 'content', "")
        if not message_content and not raw_tool_calls:
            logger.warning(f"No content or tool calls found in response: {response}")

        if raw_tool_calls:
            for tool_call in raw_tool_calls:
                if isinstance(tool_call, dict):
                    if tool_call.get("id") is None and tool_call.get("function",{}).get("name") is None:
                        logger.warning(f"Invalid tool call: {tool_call}")
                        continue
                    processed_tool_calls.append(ToolCall.from_dict(tool_call))
                else:
                    # Handle OpenAI object
                    if (tool_call.id is None and hasattr(tool_call, 'function')
                            and (tool_call.function is None
                                 or (hasattr(tool_call.function, 'name') and tool_call.function.name is None))):
                        logger.warning(f"Invalid tool call: {tool_call}")
                        continue
                    tool_call_dict = {
                        "id": tool_call.id if hasattr(tool_call,
                                                      'id') else f"call_{hash(str(tool_call)) & 0xffffffff:08x}",
                        "type": tool_call.type if hasattr(tool_call, 'type') else "function"
                    }

                    if hasattr(tool_call, 'function'):
                        function = tool_call.function
                        tool_call_dict["function"] = {
                            "name": function.name if hasattr(function, 'name') else None,
                            "arguments": function.arguments if hasattr(function, 'arguments') else None
                        }
                    if hasattr(tool_call, 'model_extra'):
                        model_extra = tool_call.model_extra
                        if model_extra:
                            tool_call_dict["model_extra"] = model_extra
                    processed_tool_calls.append(ToolCall.from_dict(tool_call_dict))

        if message_dict and processed_tool_calls:
            message_dict["tool_calls"] = [tool_call.to_dict() for tool_call in processed_tool_calls]

        # extract reasoning_details
        reasoning_details = cls._get_item_from_openai_message(message, 'reasoning_details')
        if not reasoning_details:
            model_extra = cls._get_item_from_openai_message(message, 'model_extra', {})
            reasoning_details = model_extra.get('reasoning_details', None)

        # Create and return ModelResponse
        return cls(
            id=response.id if hasattr(response, 'id') else response.get('id', 'unknown'),
            model=response.model if hasattr(response, 'model') else response.get('model', 'unknown'),
            content=cls._get_item_from_openai_message(message, 'content', ""),
            tool_calls=processed_tool_calls or None,
            usage=usage,
            raw_usage=raw_usage,
            provider_request_id=provider_request_id,
            raw_response=response,
            message=message_dict,
            reasoning_content=reasoning_content,
            finish_reason=finish_reason,
            reasoning_details=reasoning_details
        )

    @classmethod
    def from_openai_stream_chunk(cls, chunk: Any) -> 'ModelResponse':
        """
        Create ModelResponse from OpenAI stream response chunk

        Args:
            chunk: OpenAI stream chunk

        Returns:
            ModelResponse object
            
        Raises:
            LLMResponseError: When LLM response error occurs
        """
        # Handle error cases
        if hasattr(chunk, 'error') or (isinstance(chunk, dict) and chunk.get('error')):
            error_msg = chunk.error if hasattr(chunk, 'error') else chunk.get('error', 'Unknown error')
            raise LLMResponseError(
                error_msg,
                chunk.model if hasattr(chunk, 'model') else chunk.get('model', 'unknown'),
                chunk
            )

        # Extract usage information
        chunk_usage = chunk.usage if hasattr(chunk, 'usage') else chunk.get('usage') if isinstance(chunk, dict) else None
        usage = cls._normalize_openai_usage(chunk_usage) if chunk_usage else {}
        raw_usage = cls._extract_openai_raw_usage(chunk_usage)
        provider_request_id = cls._extract_openai_provider_request_id(chunk)

        # Handle finish reason chunk (end of stream)
        finish_reason = None
        if hasattr(chunk, 'choices') and chunk.choices:
            finish_reason = chunk.choices[0].finish_reason
        elif isinstance(chunk, dict) and chunk.get('choices'):
            finish_reason = chunk['choices'][0].get('finish_reason')
        if finish_reason:
            # Handle dict type (HTTP client) vs object type (SDK client)
            if isinstance(chunk, dict):
                delta = chunk['choices'][0].get('delta', {})
                return cls(
                    id=chunk.get('id', 'unknown'),
                    model=chunk.get('model', 'unknown'),
                    content=delta.get('content'),
                    usage=usage,
                    raw_usage=raw_usage,
                    provider_request_id=provider_request_id,
                    raw_response=chunk,
                    tool_calls=delta.get('tool_calls'),
                    message={"role": "assistant", "content": "", "finish_reason": finish_reason},
                    finish_reason=finish_reason
                )
            else:
                # Object type access for SDK client
                delta = chunk.choices[0].delta
                return cls(
                    id=chunk.id if hasattr(chunk, 'id') else 'unknown',
                    model=chunk.model if hasattr(chunk, 'model') else 'unknown',
                    content=delta.content if hasattr(delta, 'content') else None,
                    usage=usage,
                    raw_usage=raw_usage,
                    provider_request_id=provider_request_id,
                    raw_response=chunk,
                    tool_calls=delta.tool_calls if hasattr(delta, 'tool_calls') else None,
                    message={"role": "assistant", "content": "", "finish_reason": chunk.choices[0].finish_reason},
                    finish_reason=finish_reason
                )

        # Normal chunk with delta content
        content = ""
        processed_tool_calls = []

        if hasattr(chunk, 'choices') and chunk.choices:
            delta = chunk.choices[0].delta
            if hasattr(delta, 'content') and delta.content:
                content = delta.content
            if hasattr(delta, 'tool_calls') and delta.tool_calls:
                raw_tool_calls = delta.tool_calls
                for tool_call in raw_tool_calls:
                    if isinstance(tool_call, dict):
                        processed_tool_calls.append(ToolCall.from_dict(tool_call))
                    else:
                        # Handle OpenAI object
                        tool_call_dict = {
                            "id": tool_call.id if hasattr(tool_call,
                                                          'id') else f"call_{hash(str(tool_call)) & 0xffffffff:08x}",
                            "type": tool_call.type if hasattr(tool_call, 'type') else "function"
                        }

                        if hasattr(tool_call, 'function'):
                            function = tool_call.function
                            tool_call_dict["function"] = {
                                "name": function.name if hasattr(function, 'name') else None,
                                "arguments": function.arguments if hasattr(function, 'arguments') else None
                            }

                        processed_tool_calls.append(ToolCall.from_dict(tool_call_dict))
        elif isinstance(chunk, dict) and chunk.get('choices'):
            delta = chunk['choices'][0].get('delta', {})
            if not delta:
                delta = chunk['choices'][0].get('message', {})
            content = delta.get('content')
            raw_tool_calls = delta.get('tool_calls')
            if raw_tool_calls:
                for tool_call in raw_tool_calls:
                    processed_tool_calls.append(ToolCall.from_dict(tool_call))

        # Create message object
        message = {
            "role": "assistant",
            "content": content or "",
            "tool_calls": [tool_call.to_dict() for tool_call in processed_tool_calls] if processed_tool_calls else None,
            "is_chunk": True
        }

        # Create and return ModelResponse
        return cls(
            id=chunk.id if hasattr(chunk, 'id') else chunk.get('id', 'unknown'),
            model=chunk.model if hasattr(chunk, 'model') else chunk.get('model', 'unknown'),
            content=content or "",
            tool_calls=processed_tool_calls or None,
            usage=usage,
            raw_usage=raw_usage,
            provider_request_id=provider_request_id,
            raw_response=chunk,
            message=message
        )

    @classmethod
    def from_anthropic_stream_chunk(cls, chunk: Any) -> 'ModelResponse':
        """
        Create ModelResponse from Anthropic stream response chunk

        Args:
            chunk: Anthropic stream chunk

        Returns:
            ModelResponse object
            
        Raises:
            LLMResponseError: When LLM response error occurs
        """
        try:
            # Handle error cases
            if not chunk or (isinstance(chunk, dict) and chunk.get('error')):
                error_msg = chunk.get('error', 'Unknown error') if isinstance(chunk, dict) else 'Empty response'
                raise LLMResponseError(
                    error_msg,
                    chunk.model if hasattr(chunk, 'model') else chunk.get('model', 'unknown'),
                    chunk)

            # Handle stop reason (end of stream)
            if hasattr(chunk, 'stop_reason') and chunk.stop_reason:
                return cls(
                    id=chunk.id if hasattr(chunk, 'id') else 'unknown',
                    model=chunk.model if hasattr(chunk, 'model') else 'claude',
                    content=None,
                    raw_response=chunk,
                    message={"role": "assistant", "content": "", "stop_reason": chunk.stop_reason}
                )

            # Handle delta content
            content = None
            processed_tool_calls = []

            if hasattr(chunk, 'delta') and chunk.delta:
                delta = chunk.delta
                if hasattr(delta, 'text') and delta.text:
                    content = delta.text
                elif hasattr(delta, 'tool_use') and delta.tool_use:
                    tool_call_dict = {
                        "id": f"call_{delta.tool_use.id}",
                        "type": "function",
                        "function": {
                            "name": delta.tool_use.name,
                            "arguments": delta.tool_use.input if isinstance(delta.tool_use.input, str) else json.dumps(
                                delta.tool_use.input, ensure_ascii=False)
                        }
                    }
                    processed_tool_calls.append(ToolCall.from_dict(tool_call_dict))

            # Create message object
            message = {
                "role": "assistant",
                "content": content or "",
                "tool_calls": [tool_call.to_dict() for tool_call in
                               processed_tool_calls] if processed_tool_calls else None,
                "is_chunk": True
            }

            # Create and return ModelResponse
            return cls(
                id=chunk.id if hasattr(chunk, 'id') else 'unknown',
                model=chunk.model if hasattr(chunk, 'model') else 'claude',
                content=content,
                tool_calls=processed_tool_calls or None,
                raw_response=chunk,
                message=message
            )

        except Exception as e:
            if isinstance(e, LLMResponseError):
                raise e
            raise LLMResponseError(
                f"Error processing Anthropic stream chunk: {str(e)}",
                chunk.model if hasattr(chunk, 'model') else chunk.get('model', 'unknown'),
                chunk)

    @classmethod
    def from_anthropic_response(cls, response: Any) -> 'ModelResponse':
        """
        Create ModelResponse from Anthropic original response object

        Args:
            response: Anthropic response object

        Returns:
            ModelResponse object
            
        Raises:
            LLMResponseError: When LLM response error occurs
        """
        try:
            # Handle error cases
            if not response or (isinstance(response, dict) and response.get('error')):
                error_msg = response.get('error', 'Unknown error') if isinstance(response, dict) else 'Empty response'
                raise LLMResponseError(
                    error_msg,
                    response.model if hasattr(response, 'model') else response.get('model', 'unknown'),
                    response)

            # Build message content
            message = {
                "content": "",
                "role": "assistant",
                "tool_calls": None,
            }

            processed_tool_calls = []

            if hasattr(response, 'content') and response.content:
                for content_block in response.content:
                    if content_block.type == "text":
                        message["content"] = content_block.text
                    elif content_block.type == "tool_use":
                        tool_call_dict = {
                            "id": f"call_{content_block.id}",
                            "type": "function",
                            "function": {
                                "name": content_block.name,
                                "arguments": content_block.input if isinstance(content_block.input,
                                                                               str) else json.dumps(content_block.input)
                            }
                        }
                        processed_tool_calls.append(ToolCall.from_dict(tool_call_dict))
            else:
                message["content"] = ""

            if processed_tool_calls:
                message["tool_calls"] = [tool_call.to_dict() for tool_call in processed_tool_calls]

            # Extract usage information
            usage = {
                "completion_tokens": 0,
                "prompt_tokens": 0,
                "total_tokens": 0
            }

            if hasattr(response, 'usage'):
                if hasattr(response.usage, 'output_tokens'):
                    usage["completion_tokens"] = response.usage.output_tokens
                if hasattr(response.usage, 'input_tokens'):
                    usage["prompt_tokens"] = response.usage.input_tokens
                if hasattr(response.usage, 'input_tokens') and hasattr(response.usage, 'output_tokens'):
                    usage["total_tokens"] = response.usage.input_tokens + response.usage.output_tokens

            # Create ModelResponse
            return cls(
                id=response.id if hasattr(response,
                                          'id') else f"chatcmpl-anthropic-{hash(str(response)) & 0xffffffff:08x}",
                model=response.model if hasattr(response, 'model') else "claude",
                content=message["content"],
                tool_calls=processed_tool_calls or None,
                usage=usage,
                raw_response=response,
                message=message
            )
        except Exception as e:
            if isinstance(e, LLMResponseError):
                raise e
            raise LLMResponseError(
                f"Error processing Anthropic response: {str(e)}",
                response.model if hasattr(response, 'model') else response.get('model', 'unknown'),
                response)

    @classmethod
    def from_error(cls, error_msg: str, model: str = "unknown") -> 'ModelResponse':
        """
        Create ModelResponse from error message

        Args:
            error_msg: Error message
            model: Model name

        Returns:
            ModelResponse object
        """
        return cls(
            id="error",
            model=model,
            error=error_msg,
            message={"role": "assistant", "content": f"Error: {error_msg}"}
        )

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert ModelResponse to dictionary representation

        Returns:
            Dictionary representation
        """
        tool_calls_dict = None
        if self.tool_calls:
            tool_calls_dict = [tool_call.to_dict() for tool_call in self.tool_calls]

        return {
            "id": self.id,
            "model": self.model,
            "content": self.content,
            "tool_calls": tool_calls_dict,
            "usage": self.usage,
            "raw_usage": self.raw_usage,
            "provider_request_id": self.provider_request_id,
            "error": self.error,
            "message": self.message,
            "reasoning_content": self.reasoning_content,
            "created_at": self.created_at,
            "finish_reason": self.finish_reason,
            "structured_output": self.structured_output,
            "video_result": self.video_result.to_dict() if self.video_result else None,
        }

    def get_message(self) -> Dict[str, Any]:
        """
        Return message object that can be directly used for subsequent API calls

        Returns:
            Message object dictionary
        """
        return self.message

    def serialize_tool_calls(self) -> List[Dict[str, Any]]:
        """
        Convert tool call objects to JSON format, handling OpenAI object types

        Returns:
            List[Dict[str, Any]]: Tool calls list in JSON format
        """
        if not self.tool_calls:
            return []

        result = []
        for tool_call in self.tool_calls:
            if hasattr(tool_call, 'to_dict'):
                result.append(tool_call.to_dict())
            elif isinstance(tool_call, dict):
                result.append(tool_call)
            else:
                result.append(str(tool_call))
        return result

    def __repr__(self):
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=None,
                          default=lambda obj: obj.to_dict() if hasattr(obj, 'to_dict') else str(obj))

    def _serialize_message(self) -> Dict[str, Any]:
        """
        Serialize message object

        Returns:
            Dict[str, Any]: Serialized message dictionary
        """
        if not self.message:
            return {}

        result = {}

        # Copy basic fields
        for key, value in self.message.items():
            if key == 'tool_calls':
                # Handle tool_calls
                result[key] = self.serialize_tool_calls()
            else:
                result[key] = value

        return result
