# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import time

from aworld.core.context.base import Context
from aworld.core.event.base import Message, Constants
from aworld.core.task import TaskResponse
from aworld.logs.util import logger
from aworld.runners.event_runner import TaskEventRunner
from aworld.runners.handler.agent import DefaultAgentSocialHandler
from aworld.runners.handler.task import DefaultTaskHandler
from aworld.runners.handler.tool import DefaultToolHandler


class SocialRunner(TaskEventRunner):
    async def pre_run(self):
        await super().pre_run()
        self.swarm.max_steps = self.task.conf.get('max_steps', 100)

        self.agent_handler = DefaultAgentSocialHandler(swarm=self.swarm, endless_threshold=self.endless_threshold)
        self.tool_handler = DefaultToolHandler(tools=self.tools, tools_conf=self.tools_conf)
        self.task_handler = DefaultTaskHandler(runner=self)

    async def _do_run(self, context: Context = None):
        start = time.time()
        msg = None
        answer = None

        while True:
            if await self.is_stopped():
                logger.info("stop task...")
                if self._task_response is None:
                    # send msg to output
                    self._task_response = TaskResponse(msg=msg,
                                                       answer=answer,
                                                       success=True if not msg else False,
                                                       id=self.task.id,
                                                       time_cost=(time.time() - start),
                                                       usage=self.context.token_usage)
                break

            # consume message
            message: Message = await self.event_mng.consume()
            # use handler to process message
            results = await self._common_process(message)
            if not results:
                raise RuntimeError(f'{message} handler can not get the valid message')

            # process in framework
            async for event in self._inner_handler_process(
                    results=results,
                    handlers=[self.agent_handler, self.tool_handler, self.task_handler]
            ):
                await self.event_mng.emit_message(event)
