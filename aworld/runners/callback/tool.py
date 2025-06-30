import asyncio
import json
from typing import AsyncGenerator, Tuple

from streamlit import status

from aworld.core.task import TaskResponse
from aworld.models.model_response import ModelResponse
from aworld.output import Output, MessageOutput
from aworld.runners.callback.decorator import CallbackRegistry
from aworld.runners.handler.base import DefaultHandler
from aworld.core.common import TaskItem, Observation, CallbackItem
from aworld.core.context.base import Context
from aworld.core.event.base import Message, Constants, TopicType, AgentMessage
from aworld.logs.util import logger
from aworld.runners.state_manager import RuntimeStateManager, HandleResult, RunNodeStatus


class ToolCallbackHandler(DefaultHandler):
    def __init__(self, runner):
        self.runner = runner

    async def handle(self, message):
        if message.category != Constants.TOOL_CALLBACK:
            return
        logger.info(f"-------ToolCallbackHandler start handle message----: {message}")
        self.context = message.context
        outputs = self.runner.task.outputs
        output = None
        observation = None
        input_node_id = None
        actions = []
        try:
            payload = message.payload
            if not payload:
                return
            if isinstance(payload, CallbackItem):
                observation = payload.data[0] if isinstance(payload.data, Tuple) else payload.data
                input_node_id = payload.node_id
                actions = payload.actions
                logger.info(f"-------ToolCallbackHandler callback_func-actions: {actions}")
            elif isinstance(payload, Tuple) and isinstance(payload[0], Observation):
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

            # 这里模拟更新message的状态和result
            state_mng = RuntimeStateManager.instance()
            results = [HandleResult(
                result = AgentMessage(payload=payload.data,
                            caller=actions[0].agent_name,
                            sender=self.name(),
                            receiver=actions[0].agent_name,
                            session_id=self.context.session_id,
                            headers={"context": self.context}),
                status=RunNodeStatus.SUCCESS
            )]
            state_mng.run_succeed(input_node_id, "test callback", results)
            state_mng.run_succeed(message.id, "test callback succ", results)
            # 1\Update the current message node status
            # 2\Update the incoming message node status


        return


