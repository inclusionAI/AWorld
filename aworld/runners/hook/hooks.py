# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc

from aworld.core.context.base import Context
from aworld.core.event.base import Message
from aworld.models.model_response import ModelResponse


class HookPoint:
    """Hook 触发点常量定义

    Hooks V2 新增 16 个标准化 hook 点，覆盖 AWorld 核心生命周期。
    保留向后兼容性（START/FINISHED/ERROR 等旧常量仍可用）。

    实现状态：
    - ✅ 已实现并可用：11 个（会话、任务、Agent、用户交互、LLM、工具）
    - 🚧 规划中待实现：3 个（上下文压缩、文件变化）
    """

    # === 会话生命周期（3个）✅ 已实现 ===
    SESSION_STARTED = "session_started"        # 会话开始 - 在 event_runner.py 触发
    SESSION_FINISHED = "session_finished"      # 会话完成 - 在 event_runner.py 触发
    SESSION_FAILED = "session_failed"          # 会话失败 - 在 event_runner.py 触发

    # === 上下文管理（2个）🚧 规划中 ===
    BEFORE_CONTEXT_COMPACT = "before_context_compact"  # 上下文压缩前 - TODO: 需在 context manager 中实现触发
    AFTER_CONTEXT_COMPACT = "after_context_compact"    # 上下文压缩后 - TODO: 需在 context manager 中实现触发

    # === 任务管理（2个）✅ 已实现 ===
    TASK_CREATED = "task_created"              # 任务创建 - 在 event_runner.py 触发
    TASK_COMPLETED = "task_completed"          # 任务完成 - 在 event_runner.py 触发

    # === Agent 管理（2个）✅ 已实现 ===
    AGENT_STARTED = "agent_started"            # Agent 启动（包括子 Agent）- 在 event_runner.py 触发
    AGENT_STOPPED = "agent_stopped"            # Agent 停止 - 在 event_runner.py 触发

    # === 用户交互（1个）✅ 已实现 ===
    USER_INPUT_RECEIVED = "user_input_received"  # 用户输入 - 在 console.py 触发

    # === LLM 调用（2个）✅ 已实现 ===
    BEFORE_LLM_CALL = "before_llm_call"        # LLM 调用前 - 在 models/llm.py 触发（支持 transform）
    AFTER_LLM_CALL = "after_llm_call"          # LLM 返回后 - 在 models/llm.py 触发（支持 transform）

    # === 工具执行（3个）✅ 已实现 ===
    BEFORE_TOOL_CALL = "before_tool_call"      # 工具调用前 - 在 core/tool/base.py 触发（支持拦截和 transform）
    AFTER_TOOL_CALL = "after_tool_call"        # 工具调用成功后 - 在 core/tool/base.py 触发
    TOOL_CALL_FAILED = "tool_call_failed"      # 工具调用失败后 - 在 core/tool/base.py 触发

    # === 文件系统（1个）🚧 规划中 ===
    FILE_CHANGED = "file_changed"              # 文件变化 - TODO: 需实现文件监控机制

    # 向后兼容：保留旧常量（已废弃，映射到新常量）
    START = SESSION_STARTED
    FINISHED = SESSION_FINISHED
    ERROR = SESSION_FAILED
    PRE_LLM_CALL = BEFORE_LLM_CALL
    POST_LLM_CALL = AFTER_LLM_CALL
    OUTPUT_PROCESS = "output_process"  # 已废弃
    PRE_TOOL_CALL = BEFORE_TOOL_CALL
    POST_TOOL_CALL = AFTER_TOOL_CALL
    PRE_TASK_CALL = "pre_task_call"    # 已废弃，使用 TASK_CREATED
    POST_TASK_CALL = "post_task_call"  # 已废弃，使用 TASK_COMPLETED

class Hook:
    """Runner hook."""
    __metaclass__ = abc.ABCMeta

    @abc.abstractmethod
    def point(self):
        """Hook point."""

    @abc.abstractmethod
    async def exec(self, message: Message, context: Context = None) -> Message:
        """Execute hook function."""


class StartHook(Hook):
    """Process in the hook point of the start."""
    __metaclass__ = abc.ABCMeta

    def point(self):
        return HookPoint.START


class FinishedHook(Hook):
    """Process in the hook point of the finished."""
    __metaclass__ = abc.ABCMeta

    def point(self):
        return HookPoint.FINISHED


class ErrorHook(Hook):
    """Process in the hook point of the error."""
    __metaclass__ = abc.ABCMeta

    def point(self):
        return HookPoint.ERROR

class PreLLMCallHook(Hook):
    """Process in the hook point of the pre_llm_call."""
    __metaclass__ = abc.ABCMeta

    def point(self):
        return HookPoint.PRE_LLM_CALL
        
class PostLLMCallHook(Hook):
    """Process in the hook point of the post_llm_call."""
    __metaclass__ = abc.ABCMeta

    def point(self):
        return HookPoint.POST_LLM_CALL

class PostToolCallHook(Hook):
    """Process in the hook point of the post_tool_call."""
    __metaclass__ = abc.ABCMeta

    def point(self):
        return HookPoint.POST_TOOL_CALL

class OutputProcessHook(Hook):
    """Output process hook for processing output data for display."""
    __metaclass__ = abc.ABCMeta

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


class PreToolCallHook(Hook):
    """Process in the hook point of the pre_tool_call."""
    __metaclass__ = abc.ABCMeta

    def point(self):
        return HookPoint.PRE_TOOL_CALL


class PostToolCallHook(Hook):
    """Process in the hook point of the post_tool_call."""
    __metaclass__ = abc.ABCMeta

    def point(self):
        return HookPoint.POST_TOOL_CALL

class PreTaskCallHook(Hook):
    """Process in the hook point of the post_task_call."""
    __metaclass__ = abc.ABCMeta

    def point(self):
        return HookPoint.PRE_TASK_CALL

class PostTaskCallHook(Hook):
    """Process in the hook point of the post_task_call."""
    __metaclass__ = abc.ABCMeta

    def point(self):
        return HookPoint.POST_TASK_CALL


# === Hooks V2: 新增 Hook 基类 ===

class SessionStartedHook(Hook):
    """会话开始 hook"""
    __metaclass__ = abc.ABCMeta

    def point(self):
        return HookPoint.SESSION_STARTED


class SessionFinishedHook(Hook):
    """会话完成 hook"""
    __metaclass__ = abc.ABCMeta

    def point(self):
        return HookPoint.SESSION_FINISHED


class SessionFailedHook(Hook):
    """会话失败 hook"""
    __metaclass__ = abc.ABCMeta

    def point(self):
        return HookPoint.SESSION_FAILED


class UserInputReceivedHook(Hook):
    """用户输入接收 hook"""
    __metaclass__ = abc.ABCMeta

    def point(self):
        return HookPoint.USER_INPUT_RECEIVED


class ToolCallFailedHook(Hook):
    """工具调用失败 hook"""
    __metaclass__ = abc.ABCMeta

    def point(self):
        return HookPoint.TOOL_CALL_FAILED


class BeforeContextCompactHook(Hook):
    """上下文压缩前 hook"""
    __metaclass__ = abc.ABCMeta

    def point(self):
        return HookPoint.BEFORE_CONTEXT_COMPACT


class AfterContextCompactHook(Hook):
    """上下文压缩后 hook"""
    __metaclass__ = abc.ABCMeta

    def point(self):
        return HookPoint.AFTER_CONTEXT_COMPACT


class TaskCreatedHook(Hook):
    """任务创建 hook"""
    __metaclass__ = abc.ABCMeta

    def point(self):
        return HookPoint.TASK_CREATED


class TaskCompletedHook(Hook):
    """任务完成 hook"""
    __metaclass__ = abc.ABCMeta

    def point(self):
        return HookPoint.TASK_COMPLETED


class AgentStartedHook(Hook):
    """Agent 启动 hook"""
    __metaclass__ = abc.ABCMeta

    def point(self):
        return HookPoint.AGENT_STARTED


class AgentStoppedHook(Hook):
    """Agent 停止 hook"""
    __metaclass__ = abc.ABCMeta

    def point(self):
        return HookPoint.AGENT_STOPPED


class BeforeLLMCallHook(Hook):
    """LLM 调用前 hook"""
    __metaclass__ = abc.ABCMeta

    def point(self):
        return HookPoint.BEFORE_LLM_CALL


class AfterLLMCallHook(Hook):
    """LLM 返回后 hook"""
    __metaclass__ = abc.ABCMeta

    def point(self):
        return HookPoint.AFTER_LLM_CALL


class FileChangedHook(Hook):
    """文件变化 hook"""
    __metaclass__ = abc.ABCMeta

    def point(self):
        return HookPoint.FILE_CHANGED

