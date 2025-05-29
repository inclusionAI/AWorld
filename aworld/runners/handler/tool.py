# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from typing import Union, Dict, AsyncGenerator, Any

from aworld.core.agent.base import is_agent
from aworld.core.common import ActionModel, TaskItem
from aworld.core.context.base import Context
from aworld.core.event.base import Message, Constants
from aworld.core.tool.base import AsyncTool, Tool, ToolFactory
from aworld.logs.util import logger
from aworld.runners.handler.base import DefaultHandler
from aworld.runners.utils import TaskType


class DefaultToolHandler(DefaultHandler):
    def __init__(self, tools: Dict[str, Union[Tool, AsyncTool]], tools_conf: Dict[str, Any]):
        self.tools = tools
        self.tools_conf = tools_conf

    async def handle(self, message: Message) -> AsyncGenerator[Message, None]:
        if message.category != Constants.TOOL:
            return

        # data is List[ActionModel]
        data = message.payload
        if not data:
            # error message, p2p
            yield Message(
                category=Constants.TASK,
                payload=TaskItem(msg="no data to process.", data=data, stop=True),
                sender='agent_handler',
                session_id=Context.instance().session_id,
                topic=TaskType.ERROR
            )
            return

        for action in data:
            if not isinstance(action, ActionModel):
                # error message, p2p
                yield Message(
                    category=Constants.TASK,
                    payload=TaskItem(msg="action not a ActionModel.", data=data, stop=True),
                    sender=self.name(),
                    session_id=Context.instance().session_id,
                    topic=TaskType.ERROR
                )
                return

        new_tools = dict()
        tool_mapping = dict()
        # Directly use or use tools after creation.
        for act in data:
            if is_agent(act):
                logger.warning(f"somethings wrong, {act} is an agent.")
                continue

            if not self.tools or (self.tools and act.tool_name not in self.tools):
                # dynamic only use default config in module.
                conf = self.tools_conf.get(act.tool_name)
                tool = ToolFactory(act.tool_name, conf=conf, asyn=conf.use_async if conf else False)
                tool.event_driven = True
                if isinstance(tool, Tool):
                    tool.reset()
                elif isinstance(tool, AsyncTool):
                    await tool.reset()
                tool_mapping[act.tool_name] = []
                self.tools[act.tool_name] = tool
                new_tools[act.tool_name] = tool
            if act.tool_name not in tool_mapping:
                tool_mapping[act.tool_name] = []
            tool_mapping[act.tool_name].append(act)

        if new_tools:
            yield Message(
                category=Constants.TASK,
                payload=TaskItem(data=new_tools),
                sender=self.name(),
                session_id=Context.instance().session_id,
                topic=TaskType.SUBSCRIBE_TOOL
            )

        for tool_name, actions in tool_mapping.items():
            if not (isinstance(self.tools[tool_name], Tool) or isinstance(self.tools[tool_name], AsyncTool)):
                logger.warning(f"Unsupported tool type: {self.tools[tool_name]}")
                continue

            # send to the tool
            yield Message(
                category=Constants.TOOL,
                payload=actions,
                sender=actions[0].agent_name if actions else '',
                session_id=Context.instance().session_id,
                receiver=tool_name
            )
