import json
import logging
from dataclasses import dataclass
from typing import Any

from aworld.output import (
    MessageOutput,
    WorkSpace,
    AworldUI,
    Output,
)
from aworld.output.base import StepOutput, ToolResultOutput
from aworld.output.utils import consume_content

logger = logging.getLogger(__name__)


@dataclass
class OpenAworldUI(AworldUI):
    chat_id: str
    workspace: WorkSpace = None

    def __init__(self, chat_id: str = None, workspace: WorkSpace = None, **kwargs):
        """
        Initialize OpenWebuiAworldUI
        Args:
            chat_id: chat identifier
            workspace: workspace instance
        """
        super().__init__(**kwargs)
        self.chat_id = chat_id
        self.workspace = workspace

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

    async def tool_result(self, output: ToolResultOutput):
        """
        tool_result
        """
        tool_data = tool_call_template.format(
            tool_type=output.tool_type,
            tool_name=output.tool_name,
            function_name=output.origin_tool_call.function.name,
            function_arguments=await self.json_parse(
                output.origin_tool_call.function.arguments
            ),
            function_result=await self.parse_tool_output(output.data),
        )

        return tool_data

    async def parse_tool_output(self, tool_result):
        def _wrap_line(line: str) -> str:
            line = line.replace("<think>", "<_think_>")
            line = line.replace("</think>", "<_think_/>")
            return f"{line}\n"

        if isinstance(tool_result, str):
            tool_result = await self.json_parse(tool_result)
            tool_result = tool_result.replace("```", "``")
            tool_result = _wrap_line(tool_result)
            return tool_result
        return tool_result

    async def json_parse(self, json_str):
        try:
            function_result = json.dumps(
                json.loads(json_str), indent=2, ensure_ascii=False
            )
        except Exception:
            function_result = json_str
        return function_result

    async def step(self, output: StepOutput):
        emptyLine = "\n\n----\n\n"
        if output.status == "START":
            # await self.emit_message(step_loading_template.format(data = output.name))
            return f"{output.name} 🛫START \n\n"
        elif output.status == "FINISHED":
            return f"{output.name} 🛬FINISHED {emptyLine}"
        elif output.status == "FAILED":
            return f"{output.name} 💥FAILED: reason is {output.data} {emptyLine}"
        else:
            return f"{output.name} ❓❓❓UNKNOWN#{output.status} {emptyLine}"

    async def custom_output(self, output: Output):
        return output.data
