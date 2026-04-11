"""示例回调函数，用于测试 CallbackHookWrapper"""

from aworld.core.context.base import Context
from aworld.core.event.base import Message
from aworld.runners.hook.v2.protocol import HookJSONOutput


def process_input(message: Message, context: Context) -> Message:
    """处理用户输入的示例回调函数

    展开 @filename 引用（简化版）
    """
    if not isinstance(message.payload, str):
        return message

    text = message.payload

    # 简单的文件引用展开（仅用于测试）
    if '@' in text:
        # 添加额外上下文
        if not hasattr(message, 'headers'):
            message.headers = {}
        message.headers['additional_context'] = "[File references expanded]"

    return message


def return_hook_output(message: Message, context: Context) -> HookJSONOutput:
    """返回 HookJSONOutput 的示例回调函数"""
    return HookJSONOutput(
        continue_=True,
        system_message="Callback executed",
        additional_context="From callback function"
    )


async def async_callback(message: Message, context: Context) -> Message:
    """异步回调函数示例"""
    import asyncio
    await asyncio.sleep(0.01)  # 模拟异步操作

    if not hasattr(message, 'headers'):
        message.headers = {}
    message.headers['async_callback_executed'] = True

    return message


def failing_callback(message: Message, context: Context) -> Message:
    """故意失败的回调函数（用于测试 fail-open）"""
    raise RuntimeError("Intentional failure for testing")
