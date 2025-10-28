# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc
import asyncio
import traceback
from typing import Optional, List, Tuple, AsyncGenerator

from aworld.core.context.amni.event import ContextMessagePayload, BaseMessagePayload
from aworld.core.context.amni.processor import ProcessorFactory
from aworld.core.context.amni.utils.context_log import _generate_top_border, _generate_bottom_border
from aworld.core.event.base import Constants, Message, ContextMessage
from aworld.logs.util import logger
from aworld.runners import HandlerFactory
from aworld.runners.handler import DefaultHandler
from aworld.runners.state_manager import HandleResult, RunNodeStatus, RuntimeStateManager


@HandlerFactory.register(name=f'__{Constants.CONTEXT}__')
class ContextProcessorHandler(DefaultHandler):
    """Basic memory processor that abstracts workflow parsing and execution logic"""

    __metaclass__ = abc.ABCMeta

    def __init__(self, runner: 'TaskEventRunner'):
        super().__init__(runner)
        self.runner = runner
        self.swarm = runner.swarm
        self.endless_threshold = runner.endless_threshold
        self.task_id = runner.task.id

        self.agent_calls = []

    # def __init__(self, priority: int = 20):
    #     super().__init__("memory_processor", self.process_messages, priority=priority)
    #     self.thread_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="async_processor")

    def is_valid_message(self, message: Message):
        if message.category != Constants.CONTEXT \
                or not isinstance(message, ContextMessage):
            return False
        return True

    # receive message and handle
    async def _do_handle(self, message: Message) -> AsyncGenerator[Message, None]:
        if not self.is_valid_message(message):
            return

        headers = {"context": message.context}
        session_id = message.session_id

        context_message: ContextMessage = message
        event = context_message.payload

        res_message = await self.process_messages(event)
        yield Message(
            category=Constants.CONTEXT_RESPONSE,
            sender=self.name(),
            receiver=message.sender,
            session_id=session_id,
            payload=res_message,
            headers=headers,
        )
        return

    # notify complete
    async def post_handle(self, input:Message, output: Message) -> Message:
        if not self.is_valid_message(input):
            return output

        if output.category is not Constants.CONTEXT_RESPONSE:
            return output

        # update handle_result to state manager
        results = [HandleResult(
            name = output.category,
            status = RunNodeStatus.SUCCESS,
            result = output
        )]
        state_mng = RuntimeStateManager.instance()
        state_mng.run_succeed(node_id=input.id,
                              result_msg="run MemoryProcessorHandler succeed",
                              results=results)
        return output

    async def process_messages(self, event: ContextMessagePayload) -> Optional[BaseMessagePayload]:

        try:
            sync_processors = []
            async_processors = []

            for processor_config in event.context.get_config().processor_config:
                # check subscription
                if processor_config.subscription:
                    if not processor_config.subscription.should_process_event(event.event_type, event.namespace):
                        logger.debug(f"Skipping processor {processor_config.name} due to subscription filter")
                        continue

                if processor_config.is_async:
                    async_processors.append(processor_config)
                else:
                    sync_processors.append(processor_config)

            # sort processors by priority
            sync_processors.sort(key=lambda x: x.priority or 0)
            async_processors.sort(key=lambda x: x.priority or 0)

            results = []

            # first process sync processors
            for processor_config in sync_processors:
                self.log_start(event, processor_config)
                result = await self._process_single_processor(processor_config, event.deep_copy())
                if result:
                    results.append(result)
                self.log_end(event, processor_config)

            # then process async processors created tasks
            if async_processors:
                self._start_async_processors(async_processors, event)

            logger.info(f"Memory processing completed for event {event.event_type}")
            return None

        except Exception as e:
            logger.error(f"Memory processing failed: {e} {traceback.format_exc()}")
            return None

    def log_start(self, event, processor_config):
        logger.info(_generate_top_border())
        logger.info(f"|        {processor_config.name}         |")
        logger.info(f"Processing memory for event: {event.event_type} - {event.event_id}")

    def log_end(self, event, processor_config):
        logger.info(_generate_bottom_border())

    async def _process_single_processor(self, processor_config, event: BaseMessagePayload) -> Optional[Tuple[str, any]]:
        """process a single processor"""
        try:
            # Create processor
            processor = ProcessorFactory.create(processor_config=processor_config)
            if not processor:
                logger.warning(f"Failed to create processor: {processor_config.name} {traceback.format_exc()}")
                return None

            logger.info(f"Processing with {processor.__class__.__name__}")
            result = await processor.process(event.context, event=event)
            logger.info(f"Processor {processor.__class__.__name__} completed")
            return (processor.__class__.__name__, result)

        except Exception as e:
            logger.error(f"Processor {processor_config.name} failed: {e} {traceback.print_exc()}")
            return (processor_config.name, None)

    def _start_async_processors(self, async_processors: List, event: BaseMessagePayload):
        """Start async processors running in the background without waiting for completion"""
        for processor_config in async_processors:
            # Create background task without waiting for completion
            asyncio.create_task(
                self._run_processor_in_thread_pool(processor_config, event.deep_copy()
                                                   ))
            logger.info(f"Started async processor {processor_config.name} in background")

    async def _run_processor_in_thread_pool(self, processor_config, event: BaseMessagePayload) -> Optional[Tuple[str, any]]:
        """Run processor in thread pool"""
        try:
            self.log_start(event, processor_config)
            # Dynamically create processor instance
            processor = ProcessorFactory.create(processor_config=processor_config)
            if not processor:
                logger.warning(f"Failed to create async processor: {processor_config.name} {traceback.format_exc()}")
                return None

            logger.info(f"Processing async with {processor.__class__.__name__}")

            # Run the processor's process method in thread pool
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                self.thread_pool,
                self._sync_wrapper,
                processor,
                event.context,
                event
            )

            logger.info(f"Async processor {processor.__class__.__name__} completed")
            self.log_end(event, processor_config)
            return (processor.__class__.__name__, result)

        except Exception as e:
            logger.error(f"Async processor {processor_config.name} failed: {e} {traceback.print_exc()}")
            return (processor_config.name, None)

    def _sync_wrapper(self, processor, context, event: BaseMessagePayload):
        """Synchronous wrapper for running async methods in thread pool"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        # try:
        return loop.run_until_complete(processor.process(context, event=event))
        # except Exception as e:
        #     logger.error(f"Error in sync wrapper: {e}")
        #     return None
        # finally:
        #     # Ensure all tasks complete before closing the loop
        #     try:
        #         pending = asyncio.all_tasks(loop)
        #         if pending:
        #             loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        #     except Exception as e:
        #         logger.warning(f"Error waiting for tasks to complete: {e}")
        #     finally:
        #         loop.close()

    def __del__(self):
        """Clean up thread pool resources"""
        if hasattr(self, 'thread_pool'):
            self.thread_pool.shutdown(wait=True)

