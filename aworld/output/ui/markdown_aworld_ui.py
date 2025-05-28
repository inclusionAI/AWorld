import json
from dataclasses import dataclass

from aworld.output import (
    MessageOutput,
    AworldUI,
    Output,
)
from aworld.output.base import StepOutput, ToolResultOutput
from aworld.output.utils import consume_content
import re


tool_call_template = """

**call {function_name}**

```tool_call_arguments
{function_arguments}
```
        
```tool_call_result
{function_result}
```

"""

step_loading_template = """
```loading
{data}
```
"""


@dataclass
class MarkdownAworldUI(AworldUI):

    def __init__(self, **kwargs):
        """
        Initialize MarkdownAworldUI
        Args:"""
        super().__init__(**kwargs)

    async def message_output(self, __output__: MessageOutput):

        items = []

        async def __log_item(item):
            items.append(item)

        await consume_content(__output__.reasoning, __log_item)
        await consume_content(__output__.response, __log_item)

        return items

    async def tool_result(self, output: ToolResultOutput):
        """
        tool_result
        """
        fn_args = await self.json_parse(output.origin_tool_call.function.arguments)
        fn_args = re.sub(r"```", "````", fn_args, flags=re.MULTILINE)
        fn_results = await self.json_parse(output.data)
        fn_results = re.sub(r"```", "````", fn_results, flags=re.MULTILINE)
        tool_data = tool_call_template.format(
            function_name=output.origin_tool_call.function.name,
            function_arguments=fn_args,
            function_result=fn_results,
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
            return f"\n### {output.name} 🛫START"
        elif output.status == "FINISHED":
            return f"\n{output.name} 🛬FINISHED {emptyLine}"
        elif output.status == "FAILED":
            return f"\n{output.name} 💥FAILED {emptyLine}"
        else:
            return f"\n{output.name} ❓❓❓UNKNOWN#{output.status} {emptyLine}"

    async def custom_output(self, output: Output):
        return output.data
