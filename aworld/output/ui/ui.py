from aworld.output.base import MessageOutput, ToolCallOutput, ToolResultOutput, StepOutput


class AworldMessageUIUtils:
    """"""

    @staticmethod
    def title(self, output) -> str:
        """
        Title
        """
        pass

    @staticmethod
    def message_output(self, output: MessageOutput) -> str:
        """
        message_output
        """


        pass

    @staticmethod
    def mcp_tool_result(self, output: ToolResultOutput) -> str:
        """
            loading
        """
        pass

    @staticmethod
    def loading(self, output: StepOutput) -> str:
        """
            loading
        """
        pass

    @staticmethod
    def interrupt(self, output) -> str:
        """
            interrupt
        """
        pass

