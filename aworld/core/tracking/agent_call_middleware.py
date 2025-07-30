from typing import Optional, List, Any

from aworld.core.agent.base import is_agent_by_name
from aworld.core.event.base import Message, Constants
from aworld.core.tracking.agent_call_tracker import AgentCallTracker
from aworld.logs.util import logger
from aworld.runners.utils import _to_serializable
from aworld.utils.common import sync_exec


# 定义一个全局变量存储tracker实例，用于在静态方法中访问
_global_tracker = None


async def intercept_agent_message(message: Message) -> Message:
    """
    拦截并处理Agent消息的全局函数
    
    Args:
        message: 消息实例
        
    Returns:
        Message: 原始消息
    """
    global _global_tracker
    if not _global_tracker:
        logger.warning("Agent call tracker not initialized")
        return message
    
    sender = message.sender
    receiver = message.receiver


    logger.warn("-"*50)
    logger.warn(f"Agent call intercept_agent_message: {_to_serializable(message)}")
    logger.warn("-"*50)

    # 必须有发送方和接收方
    if not sender or not receiver or not is_agent_by_name(receiver):
        return message
    
    # 检查是否是作为工具被调用
    # agent_as_tool = message.headers.get("agent_as_tool", False)
    agent_as_tool = message.call_type == "agent_as_tool"
    if message.call_type == "tool_result":
        sender = message.caller
    
    # 记录调用关系
    _global_tracker.track_call(
        caller_id=sender,
        callee_id=receiver,
        as_tool=agent_as_tool,
        message=message
    )
    
    return message


class AgentCallTrackingMiddleware:
    """Agent调用关系跟踪中间件"""
    
    def __init__(self, tracker: Optional[AgentCallTracker] = None):
        """
        初始化中间件
        
        Args:
            tracker: 可选的AgentCallTracker实例，如果不提供则创建新实例
        """
        global _global_tracker
        self.tracker = tracker or AgentCallTracker()
        _global_tracker = self.tracker
        self.event_manager = None
    
    def register_to_event_manager(self, event_manager):
        """
        注册到事件管理器
        
        Args:
            event_manager: 事件管理器实例
        """
        self.event_manager = event_manager
        # 使用事件订阅机制，注册为Agent类型消息的处理器
        # 注册为优先级最高的处理器，以便在其他处理器之前处理
        sync_exec(self.event_manager.register_transformer,
            Constants.AGENT,
            "",
            intercept_agent_message,
            order=0)
        logger.info("AgentCallTrackingMiddleware registered to event manager")


class AgentCallTrackingService:
    """Agent调用关系跟踪服务，提供全局单例访问"""
    
    _instance = None
    
    @classmethod
    def instance(cls) -> AgentCallTracker:
        """获取全局AgentCallTracker实例"""
        global _global_tracker
        if cls._instance is None:
            cls._instance = AgentCallTracker()
            _global_tracker = cls._instance
        return cls._instance
    
    @classmethod
    def set_instance(cls, tracker: AgentCallTracker):
        """设置全局AgentCallTracker实例"""
        global _global_tracker
        cls._instance = tracker
        _global_tracker = tracker 