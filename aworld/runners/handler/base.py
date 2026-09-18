# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc
import time

from typing import TypeVar, Generic, AsyncGenerator

from aworld.events.util import send_message

from aworld.core.common import TaskItem

from aworld.core.event.base import Message, Constants, TopicType, CancelMessage
from aworld.logs.util import logger

IN = TypeVar('IN')
OUT = TypeVar('OUT')


class Handler(Generic[IN, OUT]):
    __metaclass__ = abc.ABCMeta

    @abc.abstractmethod
    async def handle(self, data: IN) -> AsyncGenerator[OUT, None]:
        """Process the data as the expected result.

        Args:
            data: Data generated while running the task.
        """

    @classmethod
    def name(cls):
        """Handler name."""
        return cls.__name__


class DefaultHandler(Handler[Message, AsyncGenerator[Message, None]]):
    """Default handler."""

    def __init__(self, runner: 'TaskEventRunner'):
        self.runner = runner
        self.hooks = None

    def get_registered_name(self):
        """Get the registered name of the handler.
        
        If the class has a REGISTERED_NAME attribute, return the value of the attribute;
        otherwise return None.
        """
        return getattr(self.__class__, "REGISTERED_NAME", None)

    def is_valid_message(self, message: Message):
        """Validate if the message is valid for this handler.
        
        If the class has a REGISTERED_NAME attribute, check if the message's category matches the registered name;
        otherwise return True.
        """
        registered_name = self.get_registered_name()
        if registered_name is not None:
            return message.category == registered_name
        return True

    async def handle(self, message: Message) -> AsyncGenerator[Message, None]:
        if not self.is_valid_message(message):
            return
        if await self.runner.should_stop_task(message):
            await self.runner.stop()
            return
        # The runner owns the absolute deadline and cancellation projection.
        # Recomputing a duration here used to reset retry lifetimes and required
        # every caller to manufacture a numeric timeout for unbounded tasks.
        async for event in self._do_handle(message):
            msg = await self.post_handle(input=message, output=event)
            if msg:
                yield msg

    async def _do_handle(self, message: Message) -> AsyncGenerator[Message, None]:
        yield message

    async def post_handle(self, input:Message, output: Message) -> Message:
        """Post handle the message.
        Args:
            message: Message generated while running the task.
        """
        return output

    async def run_hooks(self, message: Message, hook_point: str) -> AsyncGenerator[Message, None]:
        if not self.hooks:
            return
        hooks = self.hooks.get(hook_point, [])
        for hook in hooks:
            try:
                # Execute hook
                msg = await hook.exec(message, message.context)
                if msg:
                    # Check for permission_decision='ask'
                    permission_decision = msg.headers.get('permission_decision')
                    if permission_decision == 'ask':
                        # Resolve permission decision
                        from aworld.runners.hook.v2.permission import get_permission_handler

                        permission_handler = get_permission_handler()
                        reason = msg.headers.get('permission_decision_reason', None)

                        # Build context for permission resolution
                        perm_context = {
                            'hook_name': hook.name() if hasattr(hook, 'name') else 'unknown',
                            'hook_point': hook_point,
                            'tool_name': msg.payload.get('tool_name') if isinstance(msg.payload, dict) else None,
                            'args': msg.payload.get('args') if isinstance(msg.payload, dict) else None,
                        }

                        final_decision, resolution_reason = await permission_handler.resolve_permission(
                            decision='ask',
                            reason=reason,
                            context=perm_context
                        )

                        # Update message headers with final decision
                        msg.headers['permission_decision'] = final_decision
                        msg.headers['permission_decision_reason'] = resolution_reason

                        logger.info(
                            f"Permission 'ask' resolved to '{final_decision}' "
                            f"for hook '{perm_context['hook_name']}' at point '{hook_point}'. "
                            f"Reason: {resolution_reason}"
                        )

                    yield msg
            except Exception as e:
                logger.warning(f"{self.name()}|{hook.point()} {hook.name()} execute fail: {e}")
