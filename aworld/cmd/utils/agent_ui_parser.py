import json
import uuid
from dataclasses import dataclass

from pydantic import Field, BaseModel

from aworld.output import (
    MessageOutput,
    AworldUI,
    Output,
    Artifact,
    ArtifactType,
    WorkSpace,
    SearchOutput,
)
from aworld.output.base import StepOutput, ToolResultOutput
from aworld.output.utils import consume_content
from abc import ABC, abstractmethod
from typing_extensions import override


class ToolCard(BaseModel):
    tool_type: str = Field(None, description="tool type")
    tool_name: str = Field(None, description="tool name")
    function_name: str = Field(None, description="function name")
    tool_call_id: str = Field(None, description="tool call id")
    arguments: str = Field(None, description="arguments")
    results: str = Field(None, description="results")
    card_type: str = Field(None, description="card type")
    card_data: dict = Field(None, description="card data")

    @staticmethod
    def from_tool_result(output: ToolResultOutput) -> "ToolCard":
        return ToolCard(
            tool_type=output.tool_type,
            tool_name=output.tool_name,
            function_name=output.origin_tool_call.function.name,
            tool_call_id=output.origin_tool_call.id,
            arguments=output.origin_tool_call.function.arguments,
            results=output.data,
        )


class BaseToolResultParser(ABC):

    def __init__(self, tool_name: str = None):
        self.tool_name = tool_name

    @abstractmethod
    async def parse(self, output: ToolResultOutput):
        pass


class DefaultToolResultParser(BaseToolResultParser):

    @override
    async def parse(self, output: ToolResultOutput):
        tool_card = ToolCard.from_tool_result(output)

        tool_card.card_type = "tool_call_card_default"

        return f"""\
**🔧 Tool: {tool_card.tool_name}#{tool_card.function_name}**\n\n
```tool_card
{json.dumps(tool_card.model_dump(), ensure_ascii=False, indent=2)}
```
"""


class GooglePseSearchToolResultParser(BaseToolResultParser):

    @override
    async def parse(self, output: ToolResultOutput):
        tool_card = ToolCard.from_tool_result(output)

        query = ""
        try:
            args = json.loads(tool_card.arguments)
            query = args.get("query")
        except Exception:
            pass

        result_items = []
        try:
            result_items = json.loads(tool_card.results)
        except Exception:
            pass

        tool_card.card_type = "tool_call_card_link_list"
        tool_card.card_data = {
            "title": "🔎 Google Search",
            "query": query,
            "search_items": result_items,
        }

        return f"""\
**🔎 Google Search**\n\n
```tool_card
{json.dumps(tool_card.model_dump(), ensure_ascii=False, indent=2)}
```
"""


class ToolResultParserFactory:
    _parsers = {}

    @staticmethod
    def register_parser(parser: BaseToolResultParser):
        ToolResultParserFactory._parsers[parser.tool_name] = parser

    @staticmethod
    def get_parser(tool_type: str, tool_name: str):
        if "search" in tool_name:
            return GooglePseSearchToolResultParser()
        else:
            return DefaultToolResultParser()


@dataclass
class AWorldAgentUI(AworldUI):

    session_id: str = Field(default="", description="session id")
    workspace: WorkSpace = Field(default=None, description="workspace")
    cur_agent_name: str = Field(default=None, description="cur agent name")

    def __init__(self, session_id: str = None, workspace: WorkSpace = None, **kwargs):
        """
        Initialize MarkdownAworldUI
        Args:"""
        super().__init__(**kwargs)
        self.session_id = session_id
        self.workspace = workspace

    @override
    async def message_output(self, __output__: MessageOutput):
        """
        Returns an async generator that yields each message item.
        """
        # Sentinel object for queue completion
        _SENTINEL = object()

        async def async_generator():
            async def __log_item(item):
                await queue.put(item)

            from asyncio import Queue

            queue = Queue()

            async def consume_all():
                # Consume all relevant generators
                if __output__.reason_generator or __output__.response_generator:
                    if __output__.reason_generator:
                        await consume_content(__output__.reason_generator, __log_item)
                    if __output__.response_generator:
                        await consume_content(__output__.response_generator, __log_item)
                else:
                    await consume_content(__output__.reasoning, __log_item)
                    await consume_content(__output__.response, __log_item)
                # Only after all are done, put the sentinel
                await queue.put(_SENTINEL)

            # Start the consumer in the background
            import asyncio

            consumer_task = asyncio.create_task(consume_all())

            while True:
                item = await queue.get()
                if item is _SENTINEL:
                    break
                yield item
            await consumer_task  # Ensure background task is finished

        return async_generator()

    @override
    async def tool_result(self, output: ToolResultOutput):
        """
        tool_result
        """
        parser = ToolResultParserFactory.get_parser(output.tool_type, output.tool_name)
        return await parser.parse(output)

    async def _gen_custom_output(self, output):
        """
        hook for custom output
        """
        custom_output = f"{output.tool_name}#{output.origin_tool_call.function.name}"
        if (
            output.tool_name == "aworld-playwright"
            and output.origin_tool_call.function.name == "browser_navigate"
        ):
            custom_output = f"🔍 search `{json.loads(output.origin_tool_call.function.arguments)['url']}`"
        if (
            output.tool_name == "aworldsearch-server"
            and output.origin_tool_call.function.name == "search"
        ):
            custom_output = f"🔍 search keywords: {' '.join(json.loads(output.origin_tool_call.function.arguments)['query_list'])}"
        return custom_output

    @override
    async def step(self, output: StepOutput):
        emptyLine = "\n\n----\n\n"
        if output.status == "START":
            self.cur_agent_name = output.name
            return f"\n\n # {output.show_name} \n\n"
        elif output.status == "FINISHED":
            return f"{emptyLine}"
        elif output.status == "FAILED":
            return f"\n\n{output.name} 💥FAILED: reason is {output.data} {emptyLine}"
        else:
            return f"\n\n{output.name} ❓❓❓UNKNOWN#{output.status} {emptyLine}"

    @override
    async def custom_output(self, output: Output):
        return output.data

    async def _parse_tool_artifacts(self, metadata):
        result = []
        if not metadata:
            return result

        # screenshots
        if (
            metadata.get("screenshots")
            and isinstance(metadata.get("screenshots"), list)
            and len(metadata.get("screenshots")) > 0
        ):
            for index, screenshot in enumerate(metadata.get("screenshots")):
                image_artifact = Artifact(
                    artifact_id=str(uuid.uuid4()),
                    artifact_type=ArtifactType.IMAGE,
                    content=screenshot.get("ossPath"),
                )
                await self.workspace.add_artifact(image_artifact)
                result.append(
                    {
                        "artifact_type": "IMAGE",
                        "artifact_id": image_artifact.artifact_id,
                    }
                )

        # web_pages
        if metadata.get("artifact_type") == "WEB_PAGES":
            search_output = SearchOutput.from_dict(metadata.get("artifact_data"))
            artifact_id = str(uuid.uuid4())
            await self.workspace.create_artifact(
                artifact_type=ArtifactType.WEB_PAGES,
                artifact_id=artifact_id,
                content=search_output,
                metadata={
                    "query": search_output.query,
                },
            )
            result.append({"artifact_type": "WEB_PAGES", "artifact_id": artifact_id})
        return result
