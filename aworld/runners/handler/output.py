# aworld/runners/handler/output.py
import json
from typing import AsyncGenerator
from aworld.core.task import TaskResponse
from aworld.models.model_response import ModelResponse
from aworld.runners.handler.base import DefaultHandler
from aworld.output.base import StepOutput, MessageOutput, Output
from aworld.core.common import TaskItem
from aworld.core.event.base import Message, Constants, TopicType
from aworld.logs.util import logger


class DefaultOutputHandler(DefaultHandler):
    def __init__(self, runner):
        self.runner = runner

    async def handle(self, message):
        if message.category != Constants.OUTPUT:
            return
        # 1. get outputs
        outputs = self.runner.task.outputs
        if not outputs:
            yield Message(
                category=Constants.TASK,
                payload=TaskItem(msg="Cannot get outputs.",
                                 data=message, stop=True),
                sender=self.name(),
                session_id=self.runner.context.session_id,
                topic=TopicType.ERROR,
                headers={"context": message.context}
            )
            return
        # 2. build Output
        payload = message.payload
        mark_complete = False
        output = None
        try:
            if isinstance(payload, Output):
                output = payload
            elif isinstance(payload, TaskResponse):
                logger.info(
                    f"output get task_response with usage: {json.dumps(payload.usage)}")
                if message.topic == TopicType.FINISHED or message.topic == TopicType.ERROR:
                    mark_complete = True
            elif isinstance(payload, ModelResponse) or isinstance(payload, AsyncGenerator):
                output = MessageOutput(source=payload)
        except Exception as e:
            logger.warning(f"Failed to parse output: {e}")
            yield Message(
                category=Constants.TASK,
                payload=TaskItem(msg="Failed to parse output.",
                                 data=payload, stop=True),
                sender=self.name(),
                session_id=message.context.session_id,
                topic=TopicType.ERROR,
                headers={"context": message.context}
            )
        finally:
            if output:
                if not output.metadata:
                    output.metadata = {}
                output.metadata['sender'] = message.sender
                output.metadata['receiver'] = message.receiver
                await outputs.add_output(output)
            if mark_complete:
                await outputs.mark_completed()

        return
