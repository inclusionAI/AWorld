# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import importlib
import inspect
import os
import traceback
from typing import Callable, Any

from aworld.core.context.base import Context
from aworld.logs.util import logger
from aworld.runners.hook.template import HOOK_TEMPLATE
from aworld.utils.common import snake_to_camel


def hook(hook_point: str, name: str = None):
    """Hook decorator.

    NOTE: Hooks can be annotated, but they need to comply with the protocol agreement.
    The input parameter of the hook function is `Message` type, and the @hook needs to specify `hook_point`.

    Examples:
        >>> @hook(hook_point=HookPoint.ERROR)
        >>> def error_process(message: Message) -> Message | None:
        >>>     print("process error")
    The function `error_process` will be executed when an error message appears in the task,
    you can choose return nothing or return a message.

    Args:
        hook_point: Hook point that wants to process the message.
        name: Hook name.
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        # converts python function into a hoop with associated hoop point
        func_import = func.__module__
        if func_import == '__main__':
            path = inspect.getsourcefile(func)
            package = path.replace(os.getcwd(), '').replace('.py', '')
            if package[0] == '/':
                package = package[1:]
            func_import = f"from {package} "
        else:
            func_import = f"from {func_import} "

        real_name = name if name else func.__name__
        con = HOOK_TEMPLATE.format(func_import=func_import,
                                   func=func.__name__,
                                   point=snake_to_camel(hook_point),
                                   name=real_name,
                                   topic=hook_point,
                                   desc='')
        with open(f"{real_name}.py", 'w+') as write:
            write.writelines(con)
        importlib.import_module(real_name)
        return func

    return decorator

async def run_hooks(context: Context, hook_point: str, hook_from: str, payload: Any = None, **kwargs):
    """Execute hooks and break by exception"""
    from aworld.runners.hook.hook_factory import HookFactory
    from aworld.core.event.base import Message

    # Get all hooks for the specified hook point
    all_hooks = HookFactory.hooks(hook_point)
    hooks = all_hooks.get(hook_point, [])

    for hook in hooks:
        try:
            # Create a temporary Message object to pass to the hook
            message = Message(
                category="agent_hook",
                payload=payload,
                sender=hook_from,
                session_id=context.session_id if hasattr(
                    context, 'session_id') else None,
                headers={"context": context, **kwargs}
            )

            # Execute hook
            msg = await hook.exec(message, context)
            if msg:
                logger.debug(f"Hook {hook.point()} executed successfully")
                yield msg
        except Exception as e:
            logger.warning(f"Hook {hook.point()} execution failed: {traceback.format_exc()}")
            raise e