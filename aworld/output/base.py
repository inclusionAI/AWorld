import json
from builtins import anext
from datetime import datetime
from typing import Any, Dict, Generator, AsyncGenerator, Optional

from pydantic import Field, BaseModel, model_validator

from aworld.logs.util import logger
from aworld.models.model_response import ModelResponse, ToolCall


class OutputPart(BaseModel):
    content: Any
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="metadata")

    @model_validator(mode='after')
    def setup_metadata(self):
        # Ensure metadata is initialized
        if self.metadata is None:
            self.metadata = {}
        return self


class Output(BaseModel):
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="metadata")
    parts: Any = Field(default_factory=list, exclude=True, description="parts of Output")
    data: Any = Field(default=None, exclude=True, description="Output Data")
    task_id: Optional[str] = Field(default=None, description="Task Id")

    @model_validator(mode='after')
    def setup_defaults(self):
        # Ensure metadata and parts are initialized
        if self.metadata is None:
            self.metadata = {}
        if self.parts is None:
            self.parts = []
        return self

    def add_part(self, content: Any):
        if self.parts is None:
            self.parts = []
        self.parts.append(OutputPart(content=content))

    def output_type(self):
        return "default"


class ToolCallOutput(Output):

    @classmethod
    def from_tool_call(cls, tool_call: ToolCall, task_id: str):
        return cls(data=tool_call, task_id=task_id)

    def output_type(self):
        return "tool_call"


class ToolResultOutput(Output):
    origin_tool_call: Optional[ToolCall] = Field(default=None, description="origin tool call", exclude=True)

    image: str = Field(default=None)

    images: list[str] = Field(default_factory=list)

    tool_type: str = Field(default=None)

    tool_name: str = Field(default=None)

    action_name: str = Field(default=None)

    def output_type(self):
        return "tool_call_result"

    pass


class RunFinishedSignal(Output):

    def output_type(self):
        return "finished_signal"


RUN_FINISHED_SIGNAL = RunFinishedSignal()


class MessageOutput(Output):
    """
    MessageOutput structure of LLM output
    if you want to get the only response, you must first call reasoning_generator or set parameter only_response to True , then call response_generator
    if you model not reasoning, you do not need care about reasoning_generator and reasoning

    1. source: async/sync generator of the message
    2. reasoning_generator: async/sync reasoning generator of the message
    3. response_generator: async/sync response generator of the message;
    4. reasoning: reasoning of the message
    5. response: response of the message
    6. tool_calls

    """

    source: Any = Field(default=None, exclude=True, description="Source of the message")

    reason_generator: Any = Field(default=None, exclude=True, description="reasoning generator of the message")
    response_generator: Any = Field(default=None, exclude=True, description="response generator of the message")

    """
    result
    """
    reasoning: str = Field(default=None, description="reasoning of the message")
    response: Any = Field(default=None, description="response of the message")
    tool_calls: list[ToolCallOutput] = Field(default_factory=list, description="tool_calls")

    """
    other config
    """
    reasoning_format_start: str = Field(default="<think>", description="reasoning format start of the message")
    reasoning_format_end: str = Field(default="</think>", description="reasoning format end of the message")

    json_parse: bool = Field(default=False, description="json parse of the message", exclude=True)
    has_reasoning: bool = Field(default=False, description="has reasoning of the message")
    finished: bool = Field(default=False, description="finished of the message")

    @model_validator(mode='after')
    def setup_generators(self):
        """
        Setup generators for reasoning and response
        """
        source = self.source

        # if ModelResponse
        if isinstance(self.source, ModelResponse):
            source = self.source.content
            if self.source.tool_calls:
                [self.tool_calls.append(ToolCallOutput.from_tool_call(tool_call, self.task_id)) for tool_call in
                 self.source.tool_calls]

        if source is not None and isinstance(source, AsyncGenerator):
            # Create empty generators first, they will be initialized when actually used
            self.reason_generator = self.__aget_reasoning_generator()
            self.response_generator = self.__aget_response_generator()
        elif source is not None and isinstance(source, Generator):
            self.reason_generator, self.response_generator = self.__split_reasoning_and_response__()
        elif source is not None and isinstance(source, str):
            self.reasoning, self.response = self.__resolve_think__(source)

        if not self.reasoning and isinstance(self.source, ModelResponse) and self.source.reasoning_content:
            self.reasoning = self.source.reasoning_content
        return self

    async def get_finished_reasoning(self):
        if self.reasoning:
            return self.reasoning
        else:
            if self.has_reasoning and not self.reasoning:
                async for reason in self.reason_generator:
                    pass
                return self.reasoning
            else:
                return self.reasoning

    async def get_finished_response(self):
        if self.response:
            return self.response
        else:
            if self.response_generator:
                async for item in self.response_generator:
                    pass
            return self.response

    async def __aget_reasoning_generator(self) -> AsyncGenerator[str, None]:
        """
        Get reasoning content as async generator
        """
        if not self.has_reasoning:
            yield ""
            self.reasoning = ""
            return

        reasoning_buffer = ""
        is_in_reasoning = False
        if self.reasoning and len(self.reasoning) > 0:
            yield self.reasoning
            return

        try:
            while True:
                chunk = await anext(self.source)
                chunk_content = self.get_chunk_content(chunk)
                if not chunk_content:
                    continue
                if chunk_content.startswith(self.reasoning_format_start):
                    is_in_reasoning = True
                    reasoning_buffer = chunk_content
                    yield chunk_content
                elif chunk_content.endswith(self.reasoning_format_end) and is_in_reasoning:
                    reasoning_buffer += chunk_content
                    yield chunk_content
                    self.reasoning = reasoning_buffer
                    break
                elif is_in_reasoning:
                    reasoning_buffer += chunk_content
                    yield chunk_content
        except StopAsyncIteration:
            logger.info("StopAsyncIteration")

    async def __aget_response_generator(self) -> AsyncGenerator[str, None]:
        """
        Get response content as async generator

        if has_reasoning is True, system will first call reasoning_generator if you not call it;
        else it will return content contains reasoning and response
        """
        response_buffer = ""

        if self.response and len(self.response) > 0:
            yield self.response
            return

        # if has_reasoning is True, system will first call reasoning_generator if you not call it;
        if self.has_reasoning and not self.reasoning:
            async for reason in self.reason_generator:
                pass

        try:
            while True:
                chunk = await anext(self.source)
                chunk_content = self.get_chunk_content(chunk)

                if not chunk_content:
                    continue
                response_buffer += chunk_content
                yield chunk_content
        except StopAsyncIteration:
            self.finished = True
            self.response = self.__resolve_json__(response_buffer, self.json_parse)

    def get_chunk_content(self, chunk):
        if isinstance(chunk, ModelResponse):
            return chunk.content
        else:
            return chunk

    def __split_reasoning_and_response__(self) -> tuple[
        Generator[str, None, None], Generator[str, None, None]]:  # type: ignore
        """
        Split source into reasoning and response generators for sync source
        Returns:
            tuple: (reasoning_generator, response_generator)
        """
        if not self.has_reasoning:
            yield ""
            self.reasoning = ""
            return

        if not isinstance(self.source, Generator):
            raise ValueError("Source must be a Generator")

        def reasoning_generator():
            if self.reasoning and len(self.reasoning) > 0:
                yield self.reasoning
                return

            reasoning_buffer = ""
            is_in_reasoning = False

            try:
                while True:
                    chunk = next(self.source)
                    chunk_content = self.get_chunk_content(chunk)
                    if chunk_content.startswith(self.reasoning_format_start):
                        is_in_reasoning = True
                        reasoning_buffer = chunk_content
                        yield chunk_content
                    elif chunk_content.endswith(self.reasoning_format_end) and is_in_reasoning:
                        reasoning_buffer += chunk_content
                        self.reasoning = reasoning_buffer
                        yield chunk_content
                        break
                    elif is_in_reasoning:
                        yield chunk_content
                        reasoning_buffer += chunk_content
            except StopIteration:
                print("StopIteration")
                self.reasoning = reasoning_buffer

        def response_generator():
            if self.response and len(self.response) > 0:
                yield self.response
                return

            # if has_reasoning is True, system will first call reasoning_generator if you not call it;
            if self.has_reasoning and not self.reasoning:
                for reason in self.reason_generator:
                    pass

            response_buffer = ""
            try:
                while True:
                    chunk = next(self.source)
                    chunk_content = self.get_chunk_content(chunk)
                    response_buffer += chunk_content
                    self.response = response_buffer
                    yield chunk_content
            except StopIteration:
                self.response = self.__resolve_json__(response_buffer, self.json_parse)
                self.finished = True

        return reasoning_generator(), response_generator()

    def __resolve_think__(self, content):
        import re
        start_tag = self.reasoning_format_start.replace("<", "").replace(">", "")
        end_tag = self.reasoning_format_end.replace("<", "").replace(">", "")

        llm_think = ""
        match = re.search(
            rf"<{re.escape(start_tag)}(.*?)>(.|\n)*?<{re.escape(end_tag)}>",
            content,
            flags=re.DOTALL,
        )
        if match:
            llm_think = match.group(0).replace("<think>", "").replace("</think>", "")
        llm_result = re.sub(
            rf"<{re.escape(start_tag)}(.*?)>(.|\n)*?<{re.escape(end_tag)}>",
            "",
            content,
            flags=re.DOTALL,
        )
        llm_result = self.__resolve_json__(llm_result, self.json_parse)

        return llm_think, llm_result

    def __resolve_json__(self, content, json_parse=False):
        if json_parse:
            if content.__contains__("```json"):
                content = content.replace("```json", "").replace("```", "")
            return json.loads(content)
        return content

    def output_type(self):
        return "message_output"


class StepOutput(Output):
    name: str
    step_num: int
    alias_name: Optional[str] = Field(default=None, description="alias_name of the step")
    status: Optional[str] = Field(default="START", description="step_status")
    started_at: str = Field(default_factory=lambda: datetime.now().isoformat(), description="started at")
    finished_at: str = Field(default_factory=lambda: datetime.now().isoformat(), description="finished at")

    @classmethod
    def build_start_output(cls, name, step_num, alias_name=None, data=None, task_id: str = None):
        return cls(name=name, step_num=step_num, alias_name=alias_name, data=data, task_id=task_id)

    @classmethod
    def build_finished_output(cls, name, step_num, alias_name=None, data=None, task_id: str = None):
        return cls(name=name, step_num=step_num, alias_name=alias_name, status='FINISHED', data=data, task_id=task_id)

    @classmethod
    def build_failed_output(cls, name, step_num, alias_name=None, data=None, task_id: str = None):
        return cls(name=name, step_num=step_num, alias_name=alias_name, status='FAILED', data=data, task_id=task_id)

    def output_type(self):
        return "step_output"

    @property
    def show_name(self):
        return self.alias_name if self.alias_name else self.name


class SearchItem(BaseModel):
    title: str = Field(default="", description="search result title")
    url: str = Field(default="", description="search result url")
    type: str = Field(default="PAGE", description="search result type, such as PAGE, VIDEO, IMAGE")
    snippet: str = Field(default="", description="search result snippet")
    content: str = Field(default="", description="search content", exclude=True)
    raw_content: Optional[str] = Field(default="", description="search raw content", exclude=True)
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="metadata")


class SearchOutput(ToolResultOutput):
    query: str = Field(..., description="Search query string")
    results: list[SearchItem] = Field(default_factory=list, description="List of search results")

    @classmethod
    def from_dict(cls, data: dict) -> "SearchOutput":
        if not isinstance(data, dict):
            data = {}

        query = data.get("query")
        if query is None:
            raise ValueError("query is required")

        task_id = data.get("task_id")

        results_data = data.get("results", [])

        search_items = []
        for result in results_data:
            if isinstance(result, SearchItem):
                search_items.append(result)
            elif isinstance(result, dict):
                search_items.append(SearchItem(**result))
            else:
                raise ValueError(f"Invalid result type: {type(result)}")

        return cls(
            query=query,
            results=search_items,
            task_id=task_id
        )

    def output_type(self):
        return "search_output"
