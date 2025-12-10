
import asyncio
import time
from typing import Dict, List, Optional

import aworld.trace as trace
from aworld.runners.event_runner import TaskEventRunner
from aworld.logs.util import logger
from aworld.experimental.bidi_streaming.transport import Transport, BidiMessage
from aworld.core.event.base import Message, Constants, TopicType
from aworld.core.context.base import Context


class StreamingRunner(TaskEventRunner):

    def __init__(self, task,  *args, **kwargs):
        super().__init__(task, *args, **kwargs)
        self.streaming_task: Optional[asyncio.Task] = None
        self.is_streaming = True

    async def pre_run(self):
        logger.debug(f"task {self.task.id} pre run start...")
        await super().pre_run()

        # Register streaming handlers for agents
        if self.swarm:
            logger.debug(f"registering streaming handlers for swarm agents...")
            for _, agent in self.swarm.agents.items():
                # Check if agent has streaming_process method
                if hasattr(agent, 'process_streaming_input'):
                    logger.debug(f"registering streaming handler for agent {agent.id()}")
                    await self.event_mng.register_streaming_handler(agent.id(), agent.process_streaming_input)

    async def do_run(self, context: Context = None):
        if self.swarm and not self.swarm.initialized:
            raise RuntimeError("swarm needs to use `reset` to init first.")
        if not self.init_messages:
            raise RuntimeError("no question event to solve.")

        async with trace.task_span(self.init_messages[0].session_id, self.task):
            try:
                # start streaming receive loop
                self.streaming_task = asyncio.create_task(self._streaming_receive_loop())

                # emit init messages
                for msg in self.init_messages:
                    await self.event_mng.emit_message(msg)

                # execute main task loop
                await self._do_run()

                # wait for streaming task to complete
                if self.streaming_task:
                    self.streaming_task.cancel()
                    try:
                        await self.streaming_task
                    except asyncio.CancelledError:
                        pass

                await self._save_trajectories()
                resp = self._response()
                logger.info(f'task {self.task.id} finished, time cost: {time.time() - self.start_time}s')
                return resp
            finally:
                # clean up resources
                if not self.task.is_sub_task:
                    await self.task.outputs.mark_completed()
                if self.streaming_task and not self.streaming_task.done():
                    self.streaming_task.cancel()

    async def _streaming_receive_loop(self):
        '''
        streaming receive loop, receive messages from transport and publish to streaming_eventbus.
        '''
        try:
            while self.is_streaming:
                
        except asyncio.CancelledError:
            logger.info("Streaming receive loop cancelled")
        except Exception as e:
            logger.error(f"Streaming receive loop error: {e}")

    async def stop(self):
        """Stop the streaming runner."""
        self.is_streaming = False
        await super().stop()
        if self.transport and hasattr(self.transport, 'close'):
            try:
                await self.transport.close()
            except Exception as e:
                logger.warning(f"Error closing transport: {e}")
