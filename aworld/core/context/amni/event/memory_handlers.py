
# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import asyncio
import traceback
from abc import ABC
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, List, Tuple

from aworld.logs.util import logger
from .base import Event
from .base_handler import EventHandler
from .decorators import event_handler
from .. import AmniContextConfig
from ..processor.processor_factory import ProcessorFactory
from ..utils.context_log import _generate_top_border, _generate_bottom_border


@event_handler(event_types="*", priority=20)  # "*" 表示处理所有事件类型
class MemoryProcessorHandler(EventHandler, ABC):
    """基础记忆处理器，抽象 workflow 的解析和执行逻辑"""

    def __init__(self, config: AmniContextConfig, priority: int = 20):
        super().__init__("memory_processor", self.process_messages, priority=priority)
        self.config = config
        self.thread_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="async_processor")
        
    async def process_messages(self, event: Event) -> Optional[Event]:
        """处理所有类型的事件"""
        if not self.config:
            return None
            
        try:
            # 分离同步和异步处理器
            sync_processors = []
            async_processors = []
            
            for processor_config in self.config.processor_config:
                # 检查订阅条件
                if processor_config.subscription:
                    if not processor_config.subscription.should_process_event(event.event_type, event.namespace):
                        logger.debug(f"Skipping processor {processor_config.name} due to subscription filter")
                        continue
                
                # 根据is_async配置分类处理器
                if processor_config.is_async:
                    async_processors.append(processor_config)
                else:
                    sync_processors.append(processor_config)

            # 根据优先级排序
            sync_processors.sort(key=lambda x: x.priority or 0)
            async_processors.sort(key=lambda x: x.priority or 0)

            results = []

            # 先处理同步处理器
            for processor_config in sync_processors:
                self.log_start(event, processor_config)
                result = await self._process_single_processor(processor_config, event.deep_copy())
                if result:
                    results.append(result)
                self.log_end(event, processor_config)

            # 然后处理异步处理器（在后台运行，不等待完成）
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

    async def _process_single_processor(self, processor_config, event: Event) -> Optional[Tuple[str, any]]:
        """处理单个同步处理器"""
        try:
            # 动态创建处理器实例
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
    
    def _start_async_processors(self, async_processors: List, event: Event):
        """启动异步处理器在后台运行，不等待完成"""
        for processor_config in async_processors:
            # 创建后台任务，不等待完成
            asyncio.create_task(
                self._run_processor_in_thread_pool(processor_config, event.deep_copy()
            ))
            logger.info(f"Started async processor {processor_config.name} in background")
    
    async def _run_processor_in_thread_pool(self, processor_config, event: Event) -> Optional[Tuple[str, any]]:
        """在线程池中运行处理器"""
        try:
            self.log_start(event, processor_config)
            # 动态创建处理器实例
            processor = ProcessorFactory.create(processor_config=processor_config)
            if not processor:
                logger.warning(f"Failed to create async processor: {processor_config.name} {traceback.format_exc()}")
                return None
            
            logger.info(f"Processing async with {processor.__class__.__name__}")
            
            # 在线程池中运行处理器的process方法
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
    
    def _sync_wrapper(self, processor, context, event: Event):
        """同步包装器，用于在线程池中运行异步方法"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        # try:
        return loop.run_until_complete(processor.process(context, event=event))
        # except Exception as e:
        #     logger.error(f"Error in sync wrapper: {e}")
        #     return None
        # finally:
        #     # 确保所有任务完成后再关闭循环
        #     try:
        #         pending = asyncio.all_tasks(loop)
        #         if pending:
        #             loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        #     except Exception as e:
        #         logger.warning(f"Error waiting for tasks to complete: {e}")
        #     finally:
        #         loop.close()
    
    def __del__(self):
        """清理线程池资源"""
        if hasattr(self, 'thread_pool'):
            self.thread_pool.shutdown(wait=True)

