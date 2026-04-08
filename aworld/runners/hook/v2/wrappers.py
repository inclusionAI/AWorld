"""Hook 适配器包装类

提供 CommandHookWrapper（Shell 命令）和 CallbackHookWrapper（Python 函数）两种 Hook 类型。
"""

import asyncio
import json
import logging
import os
from typing import Any, Callable, Dict, Optional

from aworld.core.context.base import Context
from aworld.core.event.base import Message
from aworld.runners.hook.hooks import Hook
from .protocol import HookJSONOutput

logger = logging.getLogger(__name__)


class CommandHookWrapper(Hook):
    """Shell 命令 Hook 适配器

    将 Shell 命令包装成 Hook 接口，支持：
    - 执行任意 Shell 命令
    - 解析 JSON 或纯文本输出
    - 超时控制
    - 环境变量注入
    - Fail-open 策略（脚本失败不阻塞主流程）

    Attributes:
        _config: Hook 配置字典
        _hook_point: Hook 点名称
        _command: Shell 命令
        _timeout: 超时时间（毫秒）
        _shell: Shell 路径
        _async: 是否异步执行
        _env: 自定义环境变量

    Example:
        >>> config = {
        ...     'name': 'validate-path',
        ...     'hook_point': 'before_tool_call',
        ...     'command': '.aworld/hooks/validate_path.sh',
        ...     'timeout': 5000,
        ...     'env': {'ALLOWED_PATHS': '/tmp,/workspace'}
        ... }
        >>> hook = CommandHookWrapper(config)
        >>> message = Message(category='tool_call', payload={'tool_name': 'terminal', 'args': {'path': '/tmp/test.txt'}})
        >>> result = await hook.exec(message, context)
    """

    def __init__(self, config: Dict[str, Any]):
        """初始化 CommandHookWrapper

        Args:
            config: Hook 配置字典，必须包含：
                - name: Hook 名称
                - hook_point: Hook 点
                - command: Shell 命令
                可选字段：
                - timeout: 超时时间（毫秒），默认 600000（10分钟）
                - shell: Shell 路径，默认 /bin/bash
                - async: 是否异步执行，默认 False
                - env: 自定义环境变量字典
                - description: 描述
        """
        self._config = config
        self._hook_point = config['hook_point']
        self._command = config['command']
        self._timeout = config.get('timeout', 600000)  # 默认 10 分钟
        self._shell = config.get('shell', '/bin/bash')
        self._async = config.get('async', False)
        self._custom_env = config.get('env', {})
        self._name = config.get('name', 'unnamed-hook')
        self._description = config.get('description', '')

    def point(self) -> str:
        """返回 hook 点名称"""
        return self._hook_point

    async def exec(
        self,
        message: Message,
        context: Context
    ) -> Message:
        """执行 Shell 命令并解析输出

        执行流程：
        1. 构造环境变量（注入 AWORLD_* 变量）
        2. 执行 Shell 命令（带超时控制）
        3. 解析输出（JSON 或纯文本）
        4. 应用输出到 Message

        Fail-open 策略：
        - 脚本执行失败 → 记录警告，返回原始 message
        - 脚本超时 → 记录警告，返回原始 message
        - JSON 解析失败 → 将输出作为 additional_context

        Args:
            message: 输入消息
            context: 执行上下文

        Returns:
            处理后的消息对象
        """
        try:
            # 1. 构造环境变量
            env = self._build_env(message, context)

            # 2. 执行命令
            logger.debug(
                f"Executing hook '{self._name}' at point '{self._hook_point}': {self._command}"
            )

            proc = await asyncio.create_subprocess_shell(
                self._command,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                shell=True,
                executable=self._shell
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=self._timeout / 1000  # 转换为秒
                )
            except asyncio.TimeoutError:
                proc.kill()
                # 不等待进程结束，立即返回
                logger.warning(
                    f"Hook '{self._name}' timeout after {self._timeout}ms at point '{self._hook_point}'. "
                    f"Continuing execution (fail-open)."
                )
                return message

            # 3. 检查返回码
            if proc.returncode != 0:
                logger.warning(
                    f"Hook '{self._name}' failed with exit code {proc.returncode} at point '{self._hook_point}'. "
                    f"stderr: {stderr.decode().strip()[:200]}. "
                    f"Continuing execution (fail-open)."
                )
                return message

            # 4. 解析输出
            output_text = stdout.decode().strip()
            if not output_text:
                logger.debug(f"Hook '{self._name}' returned empty output")
                return message

            output = self._parse_output(output_text)

            # 5. 应用输出到消息
            return self._apply_output(message, output)

        except Exception as e:
            # Fail-open: 任何未捕获的异常都不阻塞主流程
            import traceback
            logger.error(
                f"Hook '{self._name}' raised exception at point '{self._hook_point}': {e}. "
                f"Continuing execution (fail-open)."
            )
            logger.debug(f"Full traceback:\n{''.join(traceback.format_tb(e.__traceback__))}")
            return message

    def _build_env(
        self,
        message: Message,
        context: Context
    ) -> Dict[str, str]:
        """构造环境变量

        注入的环境变量：
        - AWORLD_SESSION_ID: 会话 ID
        - AWORLD_TASK_ID: 任务 ID
        - AWORLD_CWD: 当前工作目录
        - AWORLD_HOOK_POINT: Hook 点名称
        - AWORLD_MESSAGE_JSON: Message 对象的 JSON 序列化
        - AWORLD_CONTEXT_JSON: Context 关键信息的 JSON 序列化
        - 自定义环境变量（来自配置的 env 字段）

        Args:
            message: 输入消息
            context: 执行上下文

        Returns:
            环境变量字典
        """
        # 基础环境变量（继承父进程）
        env = dict(os.environ)

        # 注入 AWORLD_* 变量
        env.update({
            'AWORLD_SESSION_ID': getattr(context, 'session_id', 'unknown'),
            'AWORLD_TASK_ID': getattr(context, 'task_id', 'unknown'),
            'AWORLD_CWD': os.getcwd(),
            'AWORLD_HOOK_POINT': self._hook_point,
            'AWORLD_HOOK_NAME': self._name,
        })

        # 序列化 Message
        try:
            # 过滤掉不可序列化的对象（如 Context）
            headers = getattr(message, 'headers', {})
            serializable_headers = {
                k: v for k, v in headers.items()
                if not isinstance(v, Context)  # 排除 Context 对象
            }

            message_dict = {
                'category': message.category,
                'payload': message.payload,
                'headers': serializable_headers,
            }
            env['AWORLD_MESSAGE_JSON'] = json.dumps(message_dict, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Failed to serialize message to JSON: {e}")
            env['AWORLD_MESSAGE_JSON'] = '{}'

        # 序列化 Context（关键信息）
        try:
            context_dict = {
                'session_id': getattr(context, 'session_id', None),
                'task_id': getattr(context, 'task_id', None),
                'agent_id': getattr(context, 'agent_id', None),
            }
            env['AWORLD_CONTEXT_JSON'] = json.dumps(context_dict, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Failed to serialize context to JSON: {e}")
            env['AWORLD_CONTEXT_JSON'] = '{}'

        # 添加自定义环境变量
        env.update(self._custom_env)

        # 过滤掉 None 值（subprocess 不支持 None 值的环境变量）
        none_keys = [k for k, v in env.items() if v is None]
        if none_keys:
            logger.warning(f"Removing environment variables with None values: {none_keys}")
            for k in none_keys:
                del env[k]

        return env

    def _parse_output(self, output_text: str) -> HookJSONOutput | str:
        """解析 hook 输出

        尝试解析为 JSON，如果失败则返回纯文本。

        Args:
            output_text: Hook 输出文本

        Returns:
            HookJSONOutput 对象（JSON 解析成功）或纯文本字符串
        """
        # 尝试 JSON 解析
        if output_text.startswith('{'):
            try:
                return HookJSONOutput.from_json(output_text)
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(
                    f"Hook '{self._name}' returned invalid JSON: {e}. "
                    f"Treating as plain text. Output preview: {output_text[:100]}"
                )
                return output_text
        else:
            # 纯文本输出
            return output_text

    def _apply_output(
        self,
        message: Message,
        output: HookJSONOutput | str
    ) -> Message:
        """将 hook 输出应用到消息

        应用规则：
        - 纯文本 → 作为 additional_context
        - JSON 输出 → 应用各个字段到 message.headers

        Args:
            message: 原始消息
            output: Hook 输出（HookJSONOutput 或字符串）

        Returns:
            修改后的消息
        """
        # 确保 message 有 headers 属性
        if not hasattr(message, 'headers'):
            message.headers = {}

        # 纯文本输出
        if isinstance(output, str):
            existing_context = message.headers.get('additional_context', '')
            message.headers['additional_context'] = (
                f"{existing_context}\n{output}".strip()
            )
            return message

        # JSON 输出
        if output.additional_context:
            existing_context = message.headers.get('additional_context', '')
            message.headers['additional_context'] = (
                f"{existing_context}\n{output.additional_context}".strip()
            )

        if output.system_message:
            message.headers['system_message'] = output.system_message

        if output.permission_decision:
            message.headers['permission_decision'] = output.permission_decision
            if output.permission_decision_reason:
                message.headers['permission_decision_reason'] = output.permission_decision_reason

        if output.updated_input and hasattr(message, 'payload'):
            # Merge updated_input into payload
            if isinstance(message.payload, dict):
                # 如果 payload 有 'args' 字段（工具调用场景）
                if 'args' in message.payload and isinstance(message.payload['args'], dict):
                    message.payload['args'].update(output.updated_input)
                else:
                    message.payload.update(output.updated_input)
            message.headers['updated_input'] = output.updated_input

        if output.updated_output:
            message.headers['updated_output'] = output.updated_output

        if output.watch_paths:
            message.headers['watch_paths'] = output.watch_paths

        if not output.continue_:
            message.headers['prevent_continuation'] = True
            message.headers['stop_reason'] = output.stop_reason or 'Hook stopped execution'

        if output.async_:
            message.headers['async'] = True
            message.headers['async_task_id'] = output.async_task_id
            message.headers['async_timeout'] = output.async_timeout

        if output.hook_specific_output:
            message.headers['hook_specific_output'] = output.hook_specific_output

        return message

    def __repr__(self) -> str:
        """字符串表示"""
        return (
            f"CommandHookWrapper(name={self._name!r}, "
            f"point={self._hook_point!r}, "
            f"command={self._command!r})"
        )


class CallbackHookWrapper(Hook):
    """Python 函数 Hook 适配器

    将 Python 函数包装成 Hook 接口，支持：
    - 动态导入 Python 函数（module:function 格式）
    - 同步和异步函数
    - 返回 HookJSONOutput 或修改后的 Message

    Attributes:
        _config: Hook 配置字典
        _hook_point: Hook 点名称
        _callback: Python 函数对象

    Example:
        >>> config = {
        ...     'name': 'expand-files',
        ...     'hook_point': 'user_input_received',
        ...     'callback': 'aworld_cli.hooks.file_parser:expand'
        ... }
        >>> hook = CallbackHookWrapper(config)
        >>> result = await hook.exec(message, context)
    """

    def __init__(self, config: Dict[str, Any]):
        """初始化 CallbackHookWrapper

        Args:
            config: Hook 配置字典，必须包含：
                - name: Hook 名称
                - hook_point: Hook 点
                - callback: Python 函数路径 "module:function"
                可选字段：
                - description: 描述

        Raises:
            ImportError: 模块导入失败
            AttributeError: 函数不存在
        """
        self._config = config
        self._hook_point = config['hook_point']
        self._callback_path = config['callback']
        self._name = config.get('name', 'unnamed-callback-hook')
        self._description = config.get('description', '')

        # 动态导入函数
        self._callback = self._import_callback(self._callback_path)

    def _import_callback(self, callback_path: str) -> Callable:
        """动态导入 Python 函数

        Args:
            callback_path: 函数路径，格式 "module.submodule:function_name"

        Returns:
            函数对象

        Raises:
            ImportError: 模块导入失败
            AttributeError: 函数不存在
            ValueError: 路径格式错误
        """
        if ':' not in callback_path:
            raise ValueError(
                f"Invalid callback path: {callback_path!r}. "
                f"Expected format: 'module:function'"
            )

        module_path, function_name = callback_path.split(':', 1)

        try:
            # 导入模块
            import importlib
            module = importlib.import_module(module_path)

            # 获取函数
            callback = getattr(module, function_name)

            if not callable(callback):
                raise TypeError(f"{callback_path} is not callable")

            return callback

        except ImportError as e:
            raise ImportError(
                f"Failed to import module '{module_path}': {e}"
            )
        except AttributeError:
            raise AttributeError(
                f"Module '{module_path}' has no function '{function_name}'"
            )

    def point(self) -> str:
        """返回 hook 点名称"""
        return self._hook_point

    async def exec(
        self,
        message: Message,
        context: Context
    ) -> Message:
        """执行 Python 函数

        Args:
            message: 输入消息
            context: 执行上下文

        Returns:
            处理后的消息对象
        """
        try:
            logger.debug(
                f"Executing callback hook '{self._name}' at point '{self._hook_point}'"
            )

            # 调用函数
            if asyncio.iscoroutinefunction(self._callback):
                result = await self._callback(message, context)
            else:
                result = self._callback(message, context)

            # 处理返回值
            if result is None:
                return message
            elif isinstance(result, Message):
                return result
            elif isinstance(result, HookJSONOutput):
                # 应用 HookJSONOutput 到 message
                return self._apply_hook_output(message, result)
            else:
                logger.warning(
                    f"Callback hook '{self._name}' returned unexpected type: {type(result)}. "
                    f"Expected Message or HookJSONOutput. Ignoring result."
                )
                return message

        except Exception as e:
            # Fail-open: 回调函数失败不阻塞主流程
            logger.error(
                f"Callback hook '{self._name}' raised exception at point '{self._hook_point}': {e}. "
                f"Continuing execution (fail-open)."
            )
            return message

    def _apply_hook_output(
        self,
        message: Message,
        output: HookJSONOutput
    ) -> Message:
        """将 HookJSONOutput 应用到 Message

        复用 CommandHookWrapper 的逻辑。

        Args:
            message: 原始消息
            output: Hook 输出

        Returns:
            修改后的消息
        """
        # 创建临时 CommandHookWrapper 实例以复用 _apply_output 逻辑
        temp_wrapper = CommandHookWrapper({
            'name': self._name,
            'hook_point': self._hook_point,
            'command': 'dummy'
        })
        return temp_wrapper._apply_output(message, output)

    def __repr__(self) -> str:
        """字符串表示"""
        return (
            f"CallbackHookWrapper(name={self._name!r}, "
            f"point={self._hook_point!r}, "
            f"callback={self._callback_path!r})"
        )
