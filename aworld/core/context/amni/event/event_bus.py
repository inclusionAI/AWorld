# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
import inspect
import logging
import traceback
from typing import Dict, List, Optional, Any

from aworld.logs.util import logger
from ..config import AmniContextConfig, get_amnicontext_config
from .base import Event, EventType, ContextEvent, EventStatus, SystemPromptEvent, ToolResultEvent
from .base_handler import EventHandler
from .storage import InMemoryEventStorage
from aworld.core.context.base import Context
from aworld.memory.main import Memory


class EventBus:

    def __init__(self):
        self.handlers: Dict[str, List[EventHandler]] = {}
        self.event_storage: InMemoryEventStorage = InMemoryEventStorage()
        self.is_running = False
        self.worker_task: Optional[asyncio.Task] = None
        self.config: Optional[AmniContextConfig] = None
        self._auto_registered_handlers: List[EventHandler] = []
    
    def set_config(self, config: AmniContextConfig):
        self.config = config
        
    def auto_register_handlers(self):
        """自动注册所有标记的事件处理器"""
        if not self.config:
            logger.warning("No config set, cannot auto-register handlers")
            return
            
        handler_modules = [
            'aworld.core.context.amni.event.memory_handlers'
        ]
        
        for module_name in handler_modules:
            try:
                module = __import__(module_name, fromlist=['*'])
                self._register_handlers_from_module(module)
            except ImportError as e:
                logger.warning(f"Module {module_name} not found: {e}, exception is {traceback.format_exc()}")

    def _register_handlers_from_module(self, module):
        """从模块中注册事件处理器"""
        for name, obj in inspect.getmembers(module):
            if (inspect.isclass(obj) and 
                hasattr(obj, '__event_handler__') and 
                obj.__event_handler__ and
                issubclass(obj, EventHandler)):
                
                try:
                    sig = inspect.signature(obj.__init__)
                    params = list(sig.parameters.keys())
                    
                    if 'config' in params:
                        handler = obj(config=self.config)
                    else:
                        handler = obj()
                    
                    if hasattr(obj, '__event_types__'):
                        event_types = obj.__event_types__
                        if event_types == "*":
                            event_types = EventType.as_list()
                        
                        for event_type in event_types:
                            self.subscribe(event_type, handler)
                            
                        self._auto_registered_handlers.append(handler)
                        logger.info(f"Auto-registered handler {name} for events: {event_types}")
                        
                except Exception as e:
                    logger.error(f"Failed to auto-register handler {name}: {e}: traceback is {traceback.format_exc()}")
    
    async def start(self):
        """启动事件总线"""
        if self.is_running == True:
            return
        
        self.is_running = True
        self.worker_task = asyncio.create_task(self._event_worker())
        logger.info(f"EventBus started")
        logger.info(f"EventBus started")
    
    async def stop(self):
        """停止事件总线"""
        if not self.is_running == False:
            return
        
        self.is_running = False
        
        if self.worker_task:
            self.worker_task.cancel()
            try:
                await self.worker_task
            except asyncio.CancelledError:
                pass
        
        logger.info(f"EventBus stopped")
        logger.info(f"EventBus stopped")
    
    async def _event_worker(self):
        logger.info("AmniContext EventBus _event_worker created...")
        while self.is_running:
            try:
                event = await self.event_storage.get_from_queue(timeout=1.0)
                if event is None:
                    continue
                await self._process_event(event)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in event worker: {e}")
    
    async def _process_event(self, event: Event):
        """处理单个事件"""
        try:
            await self.event_storage.update_status(event.event_id, EventStatus.PROCESSING)
            
            handlers = self.handlers.get(event.event_type, [])
            
            if not handlers:
                logger.debug(f"No handlers for event type: {event.event_type}")
                await self.event_storage.update_status(event.event_id, EventStatus.SUCCESS)
                return
            
            sorted_handlers = sorted(handlers, key=lambda h: h.priority, reverse=True)
            
            for handler in sorted_handlers:
                if not handler.is_active:
                    continue
                
                try:
                    result = await handler.handle(event)
                    if result and isinstance(result, Event):
                        await self.publish(result)
                except Exception as e:
                    logger.error(f"Handler {handler.name} failed: {e}")
                    await self.event_storage.update_status(event.event_id, EventStatus.FAILED)
                    return
            
            await self.event_storage.update_status(event.event_id, EventStatus.SUCCESS)
            
        except Exception as e:
            logger.error(f"Failed to process event {event.event_id}: {e}, traceback is {traceback.format_exc()}")
            await self.event_storage.update_status(event.event_id, EventStatus.FAILED)
    
    async def publish(self, event: Event) -> bool:
        """发布事件"""
        if not self.is_running:
            logger.warning("EventBus is not running, event dropped")
            return False
        
        try:
            success = await self.event_storage.put(event)
            if success:
                logger.debug(f"Event {event.event_id} published")
            else:
                logger.error(f"Failed to store event {event.event_id}")
            return success
        except Exception as e:
            logger.error(f"Failed to publish event {event.event_id}: {e}, traceback is {traceback}")
            return False
    
    async def publish_and_wait(self, event: Event, timeout: float = 30.0, poll_interval: float = 0.1) -> bool:
        """
        发布事件并等待处理完成
        
        Args:
            event: 要发布的事件
            timeout: 超时时间（秒），默认30秒
            poll_interval: 轮询间隔（秒），默认0.1秒
            
        Returns:
            bool: 事件是否成功处理完成
        """
        if not self.is_running:
            logger.warning("EventBus is not running, event dropped")
            return False
        
        await self._process_event(event)

    
    def subscribe(self, event_type: str, handler: EventHandler):
        """订阅事件"""
        event_type_str = event_type
        if event_type_str not in self.handlers:
            self.handlers[event_type_str] = []
        
        self.handlers[event_type_str].append(handler)
        logger.info(f"Handler {handler.name} subscribed to {event_type_str}")
    
    def unsubscribe(self, event_type: str, handler_name: str):
        """取消订阅"""
        event_type_str = event_type
        if event_type_str in self.handlers:
            self.handlers[event_type_str] = [
                h for h in self.handlers[event_type_str] 
                if h.name != handler_name
            ]
            logger.info(f"Handler {handler_name} unsubscribed from {event_type_str}")

    @staticmethod
    def create_context_event(event_type: str,
                             context: Context,
                             namespace: str = ""
                             ) -> Event:
        return ContextEvent(
            event_type=event_type,
            namespace=namespace,
            context=context
        )


    @staticmethod
    def create_system_prompt_event(event_type: str,
              context: Context,
              system_prompt: Optional[str] = None,
              user_query: Optional[str] = None,
              agent_id: Optional[str] = None,
              agent_name: Optional[str] = None,
              namespace: str = "",) -> 'ContextEvent':
        return SystemPromptEvent(
            context=context,
            system_prompt=system_prompt,
            user_query=user_query,
            agent_id=agent_id,
            agent_name=agent_name,
            event_type=event_type,
            namespace=namespace
        )

    @staticmethod
    def create_tool_result_event(tool_result: Any,
                                 context: Context,
                                 tool_call_id: Optional[str] = None,
                                 agent_id: Optional[str] = None,
                                 agent_name: Optional[str] = None,
                                 namespace: str = "") -> 'ToolResultEvent':
        return ToolResultEvent(
            event_type=EventType.TOOL_RESULT,
            tool_result=tool_result,
            context=context,
            tool_call_id=tool_call_id,
            agent_id=agent_id,
            agent_name=agent_name,
            namespace=namespace
        )

    async def get_event_by_id(self, event_id: str) -> Optional[Event]:
        return await self.event_storage.get_by_id(event_id)
    
    async def get_events_by_type(self, event_type: str) -> List[Event]:
        return await self.event_storage.get_by_type(event_type)
    
    async def get_events_by_namespace(self, namespace: str) -> List[Event]:
        return await self.event_storage.get_by_namespace(namespace)
    
    async def list_events(self, limit: int = 100, offset: int = 0) -> List[Event]:
        return await self.event_storage.list_events(limit, offset)
    
    async def get_event_count(self) -> int:
        return await self.event_storage.count()
    
    async def get_queue_size(self) -> int:
        return await self.event_storage.get_queue_size()
    
    async def clear_events(self) -> bool:
        return await self.event_storage.clear()



_global_event_bus: Optional[EventBus] = None
_global_config: Optional['AmniContextConfig'] = None
_global_event_bus_started = False

async def start_global_event_bus(config: Optional['AmniContextConfig'] = None) -> EventBus:
    """
    异步启动全局事件总线
    
    Args:
        config: 配置对象，如果为None则使用默认配置
        
    Returns:
        EventBus: 已启动的事件总线实例
    """
    global _global_event_bus, _global_config, _global_event_bus_started

    if _global_event_bus_started and _global_event_bus is not None:
        return _global_event_bus

    _global_event_bus = EventBus()
    _global_event_bus.set_config(config)
    
    if not _global_event_bus_started:
        try:
            await _global_event_bus.start()
            _global_event_bus.auto_register_handlers()
            _global_event_bus_started = True
            logging.getLogger("EventBus").info("Global event bus started successfully")
        except Exception as e:
            logging.getLogger("EventBus").error(f"Failed to start global event bus: {e} {traceback.format_exc()}")
            raise
    
    return _global_event_bus

async def stop_global_event_bus():
    """异步停止全局事件总线"""
    global _global_event_bus, _global_event_bus_started
    
    if _global_event_bus is not None and _global_event_bus_started:
        try:
            await _global_event_bus.stop()
            _global_event_bus_started = False
            logging.getLogger("EventBus").info("Global event bus stopped successfully")
        except Exception as e:
            logging.getLogger("EventBus").error(f"Failed to stop global event bus: {e}")
            raise

async def get_global_event_bus(config: Optional['AmniContextConfig'] = None) -> EventBus:
    global _global_event_bus, _global_config

    if _global_event_bus is not None:
        return _global_event_bus

    if config is None:
        config = get_amnicontext_config()

    _global_config = config
    _global_event_bus = await start_global_event_bus(config)

    return _global_event_bus

def get_global_config() -> Optional['AmniContextConfig']:
    return _global_config

def is_global_event_bus_started() -> bool:
    return _global_event_bus_started
