import traceback

from aworld.logs.util import logger
from aworld.output.utils import consume_content

from aworld.output.base import MessageOutput, ToolResultOutput, StepOutput, Output


class AworldUI:
    """"""

    async def title(self, output):
        """
        Title
        """
        pass

    async def message_output(self, __output__: MessageOutput):
        """
        message_output
        """
        pass

    async def tool_result(self, output: ToolResultOutput):
        """
            loading
        """
        pass


    async def step(self, output: StepOutput):
        """
            loading
        """
        pass

    async def custom_output(self, output: Output) -> str:
        """
            custom
        """
        pass

    @classmethod
    async def parse_output(cls, output, ui: "AworldUI"):
        """
            parse_output
        """

        try:
            if isinstance(output, MessageOutput):
                return await ui.message_output(output)
            elif isinstance(output, ToolResultOutput):
                return await ui.tool_result(output)
            elif isinstance(output, StepOutput):
                return await ui.step(output)
            else:
                return await ui.custom_output(output)
        except Exception as err:
            logger.warning(f"parse_output failed. err is {err}, traceback is {traceback.format_exc()}")
            return ""


class PrinterAworldUI(AworldUI):
    """"""

    async def title(self, output) -> str:
        """
        Title
        """
        pass

    async def message_output(self, __output__: MessageOutput) -> str:
        """
        message_output
        """
        result=[]

        async def __log_item(item):
            result.append(item)
            print(item, end="", flush=True)

        if __output__.reason_generator or __output__.response_generator:
            if __output__.reason_generator:
                await consume_content(__output__.reason_generator, __log_item)
            if __output__.response_generator:
                await consume_content(__output__.response_generator, __log_item)
        else:
            await consume_content(__output__.reasoning, __log_item)
            await consume_content(__output__.response, __log_item)
        # if __output__.tool_calls:
        #     await consume_content(__output__.tool_calls, __log_item)

        print("")
        return "".join(result)

    async def tool_result(self, output: ToolResultOutput) -> str:
        """
            loading
        """
        return f"call tool {output.origin_tool_call.id}#{output.origin_tool_call.function.name} \n" \
               f"with params {output.origin_tool_call.function.arguments} \n" \
               f"with result {output.data}\n"


    async def step(self, output: StepOutput) -> str:
        """
            loading
        """
        if output.status == "START":
            return f"=============✈️START {output.name}======================"
        elif output.status == "FINISHED":
            return f"=============🛬FINISHED {output.name}======================"
        elif output.status == "FAILED":
            return f"=============🛬💥FAILED {output.name}======================"
        return f"=============？UNKNOWN#{output.status} {output.name}======================"

    async def custom_output(self, output: Output) -> str:
        pass


