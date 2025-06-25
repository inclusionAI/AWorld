import json
from typing import AsyncGenerator

from aworld.core.task import TaskResponse
from aworld.models.model_response import ModelResponse
from aworld.output import Output, MessageOutput
from aworld.runners.callback.decorator import CallbackRegistry
from aworld.runners.handler.base import DefaultHandler
from aworld.core.common import TaskItem, Observation
from aworld.core.context.base import Context
from aworld.core.event.base import Message, Constants, TopicType
from aworld.logs.util import logger

class ToolCallbackHandler(DefaultHandler):
    def __init__(self, runner):
        self.runner = runner

    async def handle(self, message):
        if message.category != Constants.TOOL_CALLBACK:
            return
        logger.info(f"-------ToolCallbackHandler start handle message----: {message}")
        outputs = self.runner.task.outputs
        output = None
        try:
            payload = message.payload
            if not payload or not payload[0]:
                return
            observation=payload[0]
            if not isinstance(observation, Observation):
                return
            if not observation.action_result:
                return
            for res in observation.action_result:
                if not res or not res.content or not res.tool_name or not res.action_name:
                    continue
                callback_func = CallbackRegistry.get(res.tool_name + "__" + res.action_name)
                if not callback_func:
                    continue
                callback_func(res)
                logger.info(f"-------ToolCallbackHandler callback_func-res: {res}")
            logger.info(f"-------ToolCallbackHandler end  handle message: {observation}")
        except Exception as e:
            # todo
            logger.warning(f"ToolCallbackHandler Failed to parse payload: {e}")
            yield Message(
                category=Constants.TASK,
                payload=TaskItem(msg="Failed to parse output.", data=payload, stop=True),
                sender=self.name(),
                session_id=Context.instance().session_id,
                topic=TopicType.ERROR
            )
        finally:
            #todo
            if output:
                if not output.metadata:
                    output.metadata = {}
                output.metadata['sender'] = message.sender
                output.metadata['receiver'] = message.receiver
                await outputs.add_output(output)
            # 1\Update the current message node status
            # 2\Update the incoming message node status


        return


