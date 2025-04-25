import asyncio
import time
from dataclasses import dataclass, field
from time import sleep

from rich.table import Table

from aworld.output.utils import consume_content
from rich.status import Status
from rich.console import Console

from aworld.output import MessageOutput, WorkSpace
from aworld.output.base import StepOutput, ToolResultOutput
from aworld.output.ui.base import AworldUI

@dataclass
class RichAworldUI(AworldUI):
    console: Console = field(default_factory=Console)
    status: Status = None
    workspace: WorkSpace = None

    async def message_output(self, __output__: MessageOutput):
        result = []
        async def __log_item(item):
            result.append(item)
            self.console.print(item, end="")

        if __output__.reason_generator or __output__.response_generator:
            if __output__.reason_generator:
                await consume_content(__output__.reason_generator, __log_item)
            if __output__.reason_generator:
                await consume_content(__output__.response_generator, __log_item)
        else:
            await consume_content(__output__.reasoning, __log_item)
            await consume_content(__output__.response, __log_item)
        # if __output__.tool_calls:
        #     await consume_content(__output__.tool_calls, __log_item)
        self.console.print("")

    async def tool_result(self, output: ToolResultOutput):
        """
            tool_result
        """
        table = Table(show_header=False, header_style="bold magenta", title=f"Call Tools#ID_{output.origin_tool_call.id}")
        table.add_column("name", style="dim", width=12)
        table.add_column("content")
        table.add_row("function_name", output.origin_tool_call.function.name)
        table.add_row("arguments", output.origin_tool_call.function.arguments)
        table.add_row("result", output.data)
        self.console.print(table)

    async def step(self, output: StepOutput):
        if output.status == "START":
            self.console.print(f"[bold green]{output.name} ✈️START ...")
            self.status = self.console.status(f"[bold green]{output.name} RUNNING ...")
            self.status.start()
        elif output.status == "FINISHED":
            self.status.stop()
            self.console.print(f"[bold green]{output.name} 🛬FINISHED ...")
        elif output.status == "FAILED":
            self.status.stop()
            self.console.print(f"[bold red]{output.name} 💥FAILED ...")
        else:
            self.status.stop()
            self.console.print(f"============={output.name} ❓❓❓UNKNOWN#{output.status} ======================")
