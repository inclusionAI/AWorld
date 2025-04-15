import abc
import logging
from builtins import anext
import json
import time
from typing import Any, Dict, Generator, AsyncGenerator, Union, Optional, TypedDict

from openpyxl.styles.builtins import output
from pydantic import Field, BaseModel, model_validator


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

    @model_validator(mode='after')
    def setup_defaults(self):
        # Ensure metadata and parts are initialized
        if self.metadata is None:
            self.metadata = {}
        if self.parts is None:
            self.parts = []
        return self

class CommonOutput(Output):
    data: Any = Field(default=None, exclude=True, description="Output Data")

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
    """

    source: Any = Field(default=None, exclude=True, description="Source of the message")
    
    reason_generator: Any = Field(default=None, exclude=True, description="reasoning generator of the message")
    response_generator: Any = Field(default=None, exclude=True, description="response generator of the message")

    """
    result
    """
    reasoning: str = Field(default=None, description="reasoning of the message")
    response: Any = Field(default=None, description="response of the message")

    
    """
    other config
    """
    reasoning_format_start: str = Field(default="<think>", description="reasoning format start of the message")
    reasoning_format_end: str = Field(default="</think>", description="reasoning format end of the message")

    json_parse: bool = Field(default=False, description="json parse of the message", exclude=True)
    has_reasoning: bool = Field(default=True, description="has reasoning of the message")
    finished: bool = Field(default=False, description="finished of the message")

    @model_validator(mode='after')
    def setup_generators(self):
        """
        Setup generators for reasoning and response
        """
        if self.source is not None and isinstance(self.source, AsyncGenerator):
            # Create empty generators first, they will be initialized when actually used
            self.reason_generator = self.__aget_reasoning_generator()
            self.response_generator = self.__aget_response_generator()
        elif self.source is not None and isinstance(self.source, Generator):
            self.reason_generator, self.response_generator = self.__split_reasoning_and_response__()
        elif self.source is not None and isinstance(self.source, str):
            self.reasoning, self.response = self.__resolve_think__(self.source, self.json_parse)    
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
                if chunk.startswith(self.reasoning_format_start):
                    is_in_reasoning = True
                    reasoning_buffer = chunk
                    yield chunk
                elif chunk.endswith(self.reasoning_format_end) and is_in_reasoning:
                    reasoning_buffer += chunk
                    yield chunk
                    self.reasoning = reasoning_buffer
                    break
                elif is_in_reasoning:
                    reasoning_buffer += chunk
                    yield chunk
        except StopAsyncIteration:
            logging.info("StopAsyncIteration")

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
                response_buffer += chunk
                yield chunk
        except StopAsyncIteration:
            self.finished = True
            self.response = self.__resolve_json__(response_buffer, self.json_parse)

    def __split_reasoning_and_response__(self) -> tuple[Generator[str, None, None], Generator[str, None, None]]: # type: ignore
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
                    if chunk.startswith(self.reasoning_format_start):
                        is_in_reasoning = True
                        reasoning_buffer = chunk
                        yield chunk
                    elif chunk.endswith(self.reasoning_format_end) and is_in_reasoning:
                        reasoning_buffer += chunk
                        self.reasoning = reasoning_buffer
                        yield chunk
                        break
                    elif is_in_reasoning:
                        yield chunk
                        reasoning_buffer += chunk
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
                    response_buffer += chunk
                    self.response = response_buffer
                    yield chunk
            except StopIteration:
                self.response = self.__resolve_json__(response_buffer,self.json_parse)
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
        llm_result = self.__resolve_json__(llm_result, True)

        return llm_think, llm_result

    def __resolve_json__(self, content, json_parse = False):
        if json_parse:
            if content.__contains__("```json"):
                content = content.replace("```json", "").replace("```", "")
            return json.loads(content)
        return content


class Event(Output):
    pass

class ToolOutput(Output):
    pass


class SearchItem(BaseModel):
    title: str = Field(default="", description="search result title")
    url: str= Field(default="", description="search result url")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="metadata")


class SearchOutput(ToolOutput):
    query: str = Field(..., description="Search query string")
    results: list[SearchItem] = Field(default_factory=list, description="List of search results")

    @classmethod
    def from_dict(cls, data: dict) -> "SearchOutput":
        if not isinstance(data, dict):
            data = {}

        query = data.get("query")
        if query is None:
            raise ValueError("query is required")

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
            results=search_items
        )