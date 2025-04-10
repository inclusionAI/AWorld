from typing import Any, Dict, List, Optional
import json
from pydantic import BaseModel
from dataclasses import dataclass


class ToolCall:
    """
    Represents a tool call made by a model
    """

    def __init__(
        self,
        id: str,
        type: str = "function",
        name: str = None,
        arguments: str = None
    ):
        """
        Initialize ToolCall object

        Args:
            id: Tool call ID
            type: Tool call type (usually "function")
            name: Function name
            arguments: Function arguments (JSON string)
        """
        self.id = id
        self.type = type
        self.name = name
        self.arguments = arguments

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

        tool_id = data.get('id', f"call_{hash(str(data)) & 0xffffffff:08x}")
        tool_type = data.get('type', 'function')

        function_data = data.get('function', {})
        name = function_data.get('name')

        arguments = function_data.get('arguments')
        # Ensure arguments is a string
        if arguments and not isinstance(arguments, str):
            arguments = json.dumps(arguments)

        return cls(
            id=tool_id,
            type=tool_type,
            name=name,
            arguments=arguments,
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
                "name": self.name,
                "arguments": self.arguments
            }
        }

    def __json__(self):
        """
        Make ToolCall JSON serializable
        """
        return self.to_dict()

    def __repr__(self):
        return json.dumps(self.to_dict())

    def toJSON(self):
        """
        Convert to JSON serializable format
        """
        return self.to_dict()

    def __iter__(self):
        """
        Make ToolCall dict-like for JSON serialization
        """
        yield from self.to_dict().items()

    def __getstate__(self):
        """
        Return state for pickle serialization (also used by json serialization)
        """
        return self.to_dict()

    def to_json(self):
        """
        Convert to JSON string
        """
        return json.dumps(self.to_dict())

    def default(self):
        """
        Support for json.dumps default parameter
        """
        return self.to_dict()


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
        error: str = None,
        raw_response: Any = None,
        message: Dict[str, Any] = None
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
        """
        self.id = id
        self.model = model
        self.content = content
        self.tool_calls = tool_calls
        self.usage = usage or {
            "completion_tokens": 0,
            "prompt_tokens": 0,
            "total_tokens": 0
        }
        self.error = error
        self.raw_response = raw_response

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

    @classmethod
    def from_openai_response(cls, response: Any) -> 'ModelResponse':
        """
        Create ModelResponse from OpenAI response object

        Args:
            response: OpenAI response object

        Returns:
            ModelResponse object
        """
        # Handle error cases
        if hasattr(response, 'error') or (isinstance(response, dict) and response.get('error')):
            error_msg = response.error if hasattr(response, 'error') else response.get('error', 'Unknown error')
            return cls(
                id=response.id if hasattr(response, 'id') else response.get('id', 'error'),
                model=response.model if hasattr(response, 'model') else response.get('model', 'unknown'),
                error=error_msg,
                raw_response=response
            )

        # Normal case
        message = None
        if hasattr(response, 'choices') and response.choices:
            message = response.choices[0].message
        elif isinstance(response, dict) and response.get('choices'):
            message = response['choices'][0].get('message', {})

        if not message:
            return cls(
                id=response.id if hasattr(response, 'id') else response.get('id', 'unknown'),
                model=response.model if hasattr(response, 'model') else response.get('model', 'unknown'),
                error="No message found in response",
                raw_response=response
            )

        # Extract usage information
        usage = {}
        if hasattr(response, 'usage'):
            usage = {
                "completion_tokens": response.usage.completion_tokens if hasattr(response.usage,
                                                                                 'completion_tokens') else 0,
                "prompt_tokens": response.usage.prompt_tokens if hasattr(response.usage, 'prompt_tokens') else 0,
                "total_tokens": response.usage.total_tokens if hasattr(response.usage, 'total_tokens') else 0
            }
        elif isinstance(response, dict) and response.get('usage'):
            usage = response['usage']

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
                "content": message.content if hasattr(message, 'content') else None,
                "tool_calls": message.tool_calls if hasattr(message, 'tool_calls') else None,
            }

        # Process tool calls
        processed_tool_calls = []
        raw_tool_calls = message.tool_calls if hasattr(message, 'tool_calls') else message_dict.get('tool_calls')
        if raw_tool_calls:
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

        # Create and return ModelResponse
        return cls(
            id=response.id if hasattr(response, 'id') else response.get('id', 'unknown'),
            model=response.model if hasattr(response, 'model') else response.get('model', 'unknown'),
            content=message.content if hasattr(message, 'content') else message.get('content'),
            tool_calls=processed_tool_calls or None,
            usage=usage,
            raw_response=response,
            message=message_dict
        )

    @classmethod
    def from_openai_stream_chunk(cls, chunk: Any) -> 'ModelResponse':
        """
        Create ModelResponse from OpenAI stream response chunk

        Args:
            chunk: OpenAI stream chunk

        Returns:
            ModelResponse object
        """
        # Handle error cases
        if hasattr(chunk, 'error') or (isinstance(chunk, dict) and chunk.get('error')):
            error_msg = chunk.error if hasattr(chunk, 'error') else chunk.get('error', 'Unknown error')
            return cls(
                id=chunk.id if hasattr(chunk, 'id') else chunk.get('id', 'error'),
                model=chunk.model if hasattr(chunk, 'model') else chunk.get('model', 'unknown'),
                error=error_msg,
                raw_response=chunk
            )

        # Handle finish reason chunk (end of stream)
        if hasattr(chunk, 'choices') and chunk.choices and chunk.choices[0].finish_reason:
            return cls(
                id=chunk.id if hasattr(chunk, 'id') else chunk.get('id', 'unknown'),
                model=chunk.model if hasattr(chunk, 'model') else chunk.get('model', 'unknown'),
                content=None,
                raw_response=chunk,
                message={"role": "assistant", "content": None, "finish_reason": chunk.choices[0].finish_reason}
            )

        # Normal chunk with delta content
        message = None
        content = None
        tool_calls = None
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
            content = delta.get('content')
            raw_tool_calls = delta.get('tool_calls')
            if raw_tool_calls:
                for tool_call in raw_tool_calls:
                    processed_tool_calls.append(ToolCall.from_dict(tool_call))

        # Create message object
        message = {
            "role": "assistant",
            "content": content,
            "tool_calls": [tool_call.to_dict() for tool_call in processed_tool_calls] if processed_tool_calls else None,
            "is_chunk": True
        }

        # Create and return ModelResponse
        return cls(
            id=chunk.id if hasattr(chunk, 'id') else chunk.get('id', 'unknown'),
            model=chunk.model if hasattr(chunk, 'model') else chunk.get('model', 'unknown'),
            content=content,
            tool_calls=processed_tool_calls or None,
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
        """
        try:
            # Handle error cases
            if not chunk or (isinstance(chunk, dict) and chunk.get('error')):
                error_msg = chunk.get('error', 'Unknown error') if isinstance(chunk, dict) else 'Empty response'
                return cls.from_error(error_msg, "claude")

            # Handle stop reason (end of stream)
            if hasattr(chunk, 'stop_reason') and chunk.stop_reason:
                return cls(
                    id=chunk.id if hasattr(chunk, 'id') else 'unknown',
                    model=chunk.model if hasattr(chunk, 'model') else 'claude',
                    content=None,
                    raw_response=chunk,
                    message={"role": "assistant", "content": None, "stop_reason": chunk.stop_reason}
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
                                delta.tool_use.input)
                        }
                    }
                    processed_tool_calls.append(ToolCall.from_dict(tool_call_dict))

            # Create message object
            message = {
                "role": "assistant",
                "content": content,
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
            return cls.from_error(f"Error processing Anthropic stream chunk: {str(e)}", "claude")

    @classmethod
    def from_anthropic_response(cls, response: Any) -> 'ModelResponse':
        """
        Create ModelResponse from Anthropic original response object

        Args:
            response: Anthropic response object

        Returns:
            ModelResponse object
        """
        try:
            # Handle error cases
            if not response or (isinstance(response, dict) and response.get('error')):
                error_msg = response.get('error', 'Unknown error') if isinstance(response, dict) else 'Empty response'
                return cls.from_error(error_msg, "claude")

            # Build message content
            message = {
                "content": None,
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
            return cls.from_error(f"Error processing Anthropic response: {str(e)}", "claude")

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
            "error": self.error,
            "message": self.message
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

    def to_json(self) -> str:
        """
        Convert ModelResponse to JSON string representation

        Returns:
            str: JSON string
        """
        json_dict = {
            "id": self.id,
            "model": self.model,
            "content": self.content,
            "tool_calls": self.serialize_tool_calls(),
            "usage": self.usage,
            "error": self.error,
            "message": self._serialize_message()
        }

        return json.dumps(json_dict, ensure_ascii=False, indent=None,
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