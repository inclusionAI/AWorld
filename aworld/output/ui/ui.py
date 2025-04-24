from aworld.output.utils import consume_content

from aworld.output.base import MessageOutput, ToolResultOutput, StepOutput


class AworldMessageUIUtils:
    """"""

    @staticmethod
    def title(output) -> str:
        """
        Title
        """
        pass

    @staticmethod
    async def message_output(__output__: MessageOutput) -> str:
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
            if __output__.reason_generator:
                await consume_content(__output__.response_generator, __log_item)
        else:
            await consume_content(__output__.reasoning, __log_item)
            await consume_content(__output__.response, __log_item)
        # if __output__.tool_calls:
        #     await consume_content(__output__.tool_calls, __log_item)

        print("")
        return "".join(result)

    @staticmethod
    def mcp_tool_result(output: ToolResultOutput) -> str:
        """
            loading
        """
        return f"call tool {output.origin_tool_call.id}#{output.origin_tool_call.function.name} \n" \
               f"with params {output.origin_tool_call.function.arguments} \n" \
               f"with result {output.data}\n"

    @staticmethod
    def loading(output: StepOutput) -> str:
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


    @staticmethod
    def interrupt(output) -> str:
        """
            interrupt
        """
        pass

    @classmethod
    async def parse_output(cls, output) -> str:
        """
            parse_output
        """
        if isinstance(output, MessageOutput):
            return await cls.message_output(output)
        elif isinstance(output, ToolResultOutput):
            return cls.mcp_tool_result(output)
        elif isinstance(output, StepOutput):
            return cls.loading(output)
        else:
            return cls.interrupt(output)

