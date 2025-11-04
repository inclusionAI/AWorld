# aworld/runners/handler/output.py
import json
from typing import AsyncGenerator
from aworld.core.task import TaskResponse
from aworld.models.model_response import ModelResponse
from aworld.runners import HandlerFactory
from aworld.runners.handler.base import DefaultHandler
from aworld.output.base import StepOutput, MessageOutput, Output
from aworld.core.common import TaskItem
from aworld.core.event.base import Message, Constants, TopicType
from aworld.logs.util import logger
from aworld.runners.hook.hook_factory import HookFactory
from aworld.runners.hook.hooks import HookPoint


@HandlerFactory.register(name=f'__stream__')
class DefaultStreamMessageHandler(DefaultHandler):
    def __init__(self, runner):
        super().__init__(runner)
        self.runner = runner
        self.hooks = {}
        if runner.task.hooks:
            for k, vals in runner.task.hooks.items():
                self.hooks[k] = []
                for v in vals:
                    cls = HookFactory.get_class(v)
                    if cls:
                        self.hooks[k].append(cls)

    def is_stream_handler(self):
        return True

    def is_valid_message(self, message: Message):
        streaming_mode = self.runner.task.streaming_mode
        if not streaming_mode:
            return False
        if streaming_mode == "core" and message.category in [Constants.AGENT, Constants.TOOL, Constants.CHUNK, Constants.TASK, Constants.GROUP]:
            return True
        if streaming_mode == "chunk_output" and message.category == Constants.CHUNK:
            return True
        if streaming_mode == "custom":
            streaming_config = self.runner.task.streaming_config
            # todo: customize
        if streaming_mode == "all":
            return True
        return False

    async def _do_handle(self, message):
        if not self.is_valid_message(message):
            return

        queue_provider = self.runner.task.streaming_queue_provider

        if not queue_provider:
            yield Message(
                category=Constants.TASK,
                payload=TaskItem(msg="Cannot get streaming queue.",
                                 data=message, stop=True),
                sender=self.name(),
                session_id=self.runner.context.session_id,
                topic=TopicType.ERROR,
                headers={"context": message.context}
            )
            return
        
        # Use new provider interface if available, otherwise fallback to old queue
        if queue_provider:
            await queue_provider.put(message)
        return
