import json
import logging
from dataclasses import dataclass
from typing import Any, Callable

from aworld.output import MessageOutput, WorkSpace, AworldUI, get_observer, Artifact, Output
from aworld.output.base import StepOutput, ToolResultOutput
from aworld.output.utils import consume_content
import re


tool_call_template = """


**call {function_name}**

```tool_call_arguments
{re.sub(r"^```$"," ```",function_arguments)}
```
        
```tool_call_result
{re.sub(r"^```$"," ```",function_result)}
```


"""

step_loading_template = """
```loading
{data}
```
"""

@dataclass
class MarkdownAworldUI(AworldUI):
    chat_id: str
    workspace: WorkSpace = None

    def __init__(self, chat_id: str = None, workspace: WorkSpace = None, **kwargs):
        """
        Initialize MarkdownAworldUI
        Args:
            chat_id: chat identifier
            workspace: workspace instance
        """
        super().__init__(**kwargs)
        self.chat_id = chat_id
        self.workspace = workspace

    async def message_output(self, __output__: MessageOutput):

        items = []

        async def __log_item(item):
            print(f">>> Gaia Agent Event Ouput: {item}")
            items.append(item)

        await consume_content(__output__.reasoning, __log_item)
        await consume_content(__output__.response, __log_item)

        return items

    async def tool_result(self, output: ToolResultOutput):
        """
        tool_result
        """
        tool_data = tool_call_template.format(
            function_name=output.origin_tool_call.function.name,
            function_arguments=await self.json_parse(
                output.origin_tool_call.function.arguments
            ),
            function_result=await self.json_parse(output.data),
        )

        return tool_data

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
            return f"## {output.name} 🛫START"
        elif output.status == "FINISHED":
            return f"{output.name} 🛬FINISHED {emptyLine}"
        elif output.status == "FAILED":
            return f"{output.name} 💥FAILED {emptyLine}"
        else:
            return f"{output.name} ❓❓❓UNKNOWN#{output.status} {emptyLine}"

    async def custom_output(self, output: Output):
        return output.data
