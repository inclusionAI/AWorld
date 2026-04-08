# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc

from aworld.core.context.base import Context
from aworld.core.event.base import Message


class HookPoint:
    START = "start"
    FINISHED = "finished"
    ERROR = "error"
    OUTPUT_PROCESS = "output_process"
    ON_START_LLM_CALL = "on_start_llm_call"
    ON_FINISHED_LLM_CALL = "on_finished_llm_call"
    ON_LLM_CALL = "on_llm_call"
    ON_SUCCESS_LLM_CALL = "on_success_llm_call"
    ON_ERROR_LLM_CALL = "on_error_llm_call"
    ON_START_TOOL_CALL = "on_start_tool_call"
    ON_TOOL_CALL = "on_tool_call"
    ON_FINISHED_TOOL_CALL = "on_finished_tool_call"
    ON_SUCCESS_TOOL_CALL = "on_success_tool_call"
    ON_ERROR_TOOL_CALL = "on_error_tool_call"
    ON_RUN_TASK = "on_run_task"
    ON_SUCCESS_TASK = "on_success_task"
    ON_ERROR_TASK = "on_error_task"
    ON_START_TASK = "on_start_task"
    ON_FINISHED_TASK = "on_finished_task"


class Hook:
    """Runner hook."""
    __metaclass__ = abc.ABCMeta

    @abc.abstractmethod
    def point(self):
        """Hook point."""

    def name(self):
        """Hook name."""
        return self.__class__.__name__

    async def exec(self, message: Message, context: Context = None) -> Message:
        """Execute hook function."""
        pass


class StartHook(Hook):
    """Process in the hook point of the start."""

    def point(self):
        return HookPoint.START


class FinishedHook(Hook):
    """Process in the hook point of the finished."""

    def point(self):
        return HookPoint.FINISHED


class ErrorHook(Hook):
    """Process in the hook point of the error."""

    def point(self):
        return HookPoint.ERROR


class OnStartLLMCallHook(Hook):
    def point(self):
        return HookPoint.ON_START_LLM_CALL


class OnFinishedLLMCallHook(Hook):
    def point(self):
        return HookPoint.ON_FINISHED_LLM_CALL


class OnLLMCallHook(Hook):
    def point(self):
        return HookPoint.ON_LLM_CALL


class OnSuccessLLMCallHook(Hook):
    def point(self):
        return HookPoint.ON_SUCCESS_LLM_CALL


class OnErrorLLMCallHook(Hook):
    def point(self):
        return HookPoint.ON_ERROR_LLM_CALL


class OutputProcessHook(Hook):
    """Output process hook for processing output data for display."""

    def point(self):
        return HookPoint.OUTPUT_PROCESS

    def process_output_content(self, content: str) -> str:
        """process output content

        Args:
            content: original content

        Returns:
            processed content
        """
        return content
