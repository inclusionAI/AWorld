
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

    def __init__(self, task, transport: Transport, *args, **kwargs):
        super().__init__(task, *args, **kwargs)
        self.transport = transport
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
                bidi_msg = await self.transport.receive()
                if bidi_msg:
                    if self.event_mng.streaming_eventbus:
                        await self.event_mng.streaming_eventbus.publish(bidi_msg)
                    else:
                        logger.error("streaming_eventbus not available")

                    # await self.event_mng.emit_message(bidi_msg)

                # sleep for a while to avoid high CPU usage
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            logger.info("Streaming receive loop cancelled")
        except Exception as e:
            logger.error(f"Streaming receive loop error: {e}")

    async def _do_run(self):
        start = time.time()
        message = None

        try:
            while True:
                should_stop_task = await self.should_stop_task(message)
                if should_stop_task:
                    logger.warn(f"Runner {message.context.get_task().id if message else 'unknown'} task should stop.")
                    await self.stop()
                if await self.is_stopped():
                    logger.info(f"task {self.task.id} stopped and will break")
                    await self.event_mng.done()
                    if self._task_response is None:
                        self._task_response = self._create_default_response(message)
                    break

                #
                try:
                    message = await self.event_mng.consume(nowait=False)
                    if message:
                        logger.debug(f"consume message {message} of task: {self.task.id}")
                        await self._common_process(message)
                except Exception as e:
                    logger.error(f"Error consuming message: {e}")

                await asyncio.sleep(0.01)
        except Exception as e:
            logger.error(f"Streaming runner error: {e}")
        finally:
            await self.clean_background_tasks()

    def _create_default_response(self, message):
        from aworld.core.task import TaskResponse, TaskStatusValue
        return TaskResponse(
            msg="Streaming completed",
            answer='',
            success=True,
            context=message.context if message else self.context,
            id=self.task.id,
            time_cost=time.time() - self.start_time,
            usage=self.context.token_usage,
            status=TaskStatusValue.SUCCESS
        )

    async def stop(self):
        """Stop the streaming runner."""
        self.is_streaming = False
        await super().stop()
        if self.transport and hasattr(self.transport, 'close'):
            try:
                await self.transport.close()
            except Exception as e:
                logger.warning(f"Error closing transport: {e}")
