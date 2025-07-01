# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Dynamic Variables for Prompt Templates

This module provides dynamic variable functions that can be used as partial_variables
in PromptTemplate to inject runtime-generated values.

Example:
    >>> from aworld.core.context.prompts import PromptTemplate
    >>> from aworld.core.context.prompts.dynamic_variables import (
    ...     get_current_time, 
    ...     create_agent_field_getter,
    ...     create_agent_name_getter
    ... )
    >>> 
    >>> # 使用预定义的便捷函数
    >>> get_agent_name = create_agent_name_getter(agent_context)
    >>> 
    >>> # 或者使用通用的字段获取器
    >>> get_agent_name = create_agent_field_getter("agent_name", agent_context, "Assistant")
    >>> 
    >>> # 获取嵌套字段
    >>> get_model_name = create_agent_field_getter("model_config.llm_model_name", agent_context, "unknown")
    >>> 
    >>> # 带自定义处理的字段
    >>> get_tools = create_agent_field_getter(
    ...     "tool_names", agent_context, "无工具",
    ...     processor=lambda tools: ", ".join(tools) if tools else "无工具可用"
    ... )
    >>> 
    >>> prompt = PromptTemplate(
    ...     template="你好，我是{agent_name}。当前时间是{current_time}。{user_input}",
    ...     input_variables=["user_input"],
    ...     partial_variables={
    ...         "current_time": get_current_time,
    ...         "agent_name": get_agent_name,
    ...         "model_name": get_model_name,
    ...         "tools": get_tools
    ...     }
    ... )

通用字段获取器功能：
- 支持简单字段访问：create_agent_field_getter("agent_name", ctx)
- 支持嵌套字段访问：create_agent_field_getter("model_config.llm_model_name", ctx) 
- 支持自定义处理函数：processor=lambda x: f"处理后的{x}"
- 支持后备获取函数：fallback_getter=lambda ctx: ctx.get_special_field()
- 支持默认值：default_value="默认值"
"""

import os
import platform
import uuid
from datetime import datetime, timezone
from typing import Callable, Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from aworld.core.context.base import AgentContext


# ==================== 时间相关函数 ====================

def get_current_time() -> str:
    """获取当前时间 (MM/DD/YYYY, HH:MM:SS)"""
    return datetime.now().strftime("%m/%d/%Y, %H:%M:%S")


def get_current_date() -> str:
    """获取当前日期 (YYYY-MM-DD)"""
    return datetime.now().strftime("%Y-%m-%d")


def get_current_datetime() -> str:
    """获取当前日期时间 (YYYY-MM-DD HH:MM:SS)"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_current_timestamp() -> str:
    """获取当前时间戳"""
    return str(int(datetime.now().timestamp()))


def get_current_iso_time() -> str:
    """获取当前ISO格式时间"""
    return datetime.now(timezone.utc).isoformat()


def get_current_weekday() -> str:
    """获取当前星期几"""
    weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    return weekdays[datetime.now().weekday()]


def get_current_month() -> str:
    """获取当前月份名称"""
    return datetime.now().strftime("%B")


def get_current_year() -> str:
    """获取当前年份"""
    return str(datetime.now().year)


# ==================== 系统信息函数 ====================

def get_system_platform() -> str:
    """获取系统平台信息"""
    return platform.platform()


def get_system_os() -> str:
    """获取操作系统名称"""
    return platform.system()


def get_python_version() -> str:
    """获取Python版本"""
    return platform.python_version()


def get_hostname() -> str:
    """获取主机名"""
    return platform.node()


def get_username() -> str:
    """获取当前用户名"""
    return os.getenv("USER") or os.getenv("USERNAME") or "unknown"


def get_working_directory() -> str:
    """获取当前工作目录"""
    return os.getcwd()


def get_random_uuid() -> str:
    """生成随机UUID"""
    return str(uuid.uuid4())


def get_short_uuid() -> str:
    """生成短UUID (前8位)"""
    return str(uuid.uuid4())[:8]


# ==================== AgentContext 字段获取函数工厂 ====================

def create_agent_field_getter(
    field_path: str, 
    agent_context: "AgentContext", 
    default_value: str = "unknown",
    processor: Optional[Callable[[Any], str]] = None,
    fallback_getter: Optional[Callable[["AgentContext"], Any]] = None
) -> Callable[[], str]:
    """创建获取AgentContext指定字段的通用动态函数
    
    Args:
        field_path: 字段路径，支持嵌套访问，如 "agent_name" 或 "model_config.llm_model_name"
        agent_context: AgentContext实例
        default_value: 字段不存在时的默认值
        processor: 可选的值处理函数，接收原始值返回字符串
        fallback_getter: 可选的后备获取函数，当字段路径访问失败时使用
        
    Returns:
        返回一个无参数的函数，调用时返回字段值
        
    Examples:
        # 简单字段
        get_agent_name = create_agent_field_getter("agent_name", ctx, "Assistant")
        
        # 嵌套字段
        get_model = create_agent_field_getter("model_config.llm_model_name", ctx, "unknown_model")
        
        # 带处理函数
        get_prompt_preview = create_agent_field_getter(
            "system_prompt", ctx, "No system prompt",
            processor=lambda p: p[:100] + "..." if len(p) > 100 else p
        )
        
        # 带后备获取函数
        get_tools = create_agent_field_getter(
            "tool_names", ctx, "No tools available",
            processor=lambda tools: ", ".join(tools) if tools else "No tools available"
        )
    """
    def getter() -> str:
        if not agent_context:
            return default_value
            
        try:
            # 尝试使用字段路径获取值
            value = agent_context
            for field in field_path.split('.'):
                if hasattr(value, field):
                    value = getattr(value, field)
                else:
                    value = None
                    break
            
            # 如果字段路径失败，尝试使用后备获取函数
            if value is None and fallback_getter:
                value = fallback_getter(agent_context)
            
            # 如果仍然没有值，返回默认值
            if value is None:
                return default_value
            
            # 应用处理函数
            if processor:
                return processor(value)
            
            # 直接转换为字符串
            return str(value)
            
        except Exception:
            return default_value
    
    # 设置函数名和文档
    safe_field_name = field_path.replace('.', '_')
    getter.__name__ = f"get_agent_{safe_field_name}"
    getter.__doc__ = f"获取Agent的{field_path}字段"
    return getter


# ==================== 便捷创建函数 ====================

def create_simple_field_getter(field_path: str, agent_context: "AgentContext", default: str = "") -> Callable[[], str]:
    """创建简单字段获取器的便捷函数
    
    Args:
        field_path: 字段路径，支持嵌套如 "model_config.llm_model_name"
        agent_context: AgentContext实例
        default: 默认值
        
    Returns:
        字段获取函数
    """
    return create_agent_field_getter(field_path, agent_context, default)


def create_multiple_field_getters(
    field_configs: list[tuple[str, str]], 
    agent_context: "AgentContext"
) -> dict[str, Callable[[], str]]:
    """批量创建多个字段获取器
    
    Args:
        field_configs: 字段配置列表，每个元素为 (字段路径, 默认值) 的元组
        agent_context: AgentContext实例
        
    Returns:
        字段名到获取函数的映射字典
        
    Example:
        >>> getters = create_multiple_field_getters([
        ...     ("agent_name", "Assistant"),
        ...     ("agent_id", "unknown"),
        ...     ("model_config.llm_model_name", "unknown_model")
        ... ], agent_context)
        >>> 
        >>> # 使用获取器
        >>> agent_name = getters["agent_name"]()
    """
    getters = {}
    for field_path, default_value in field_configs:
        safe_key = field_path.replace('.', '_')
        getters[safe_key] = create_agent_field_getter(field_path, agent_context, default_value)
    return getters

# ==================== 工厂函数 ====================

def create_custom_time_formatter(format_string: str) -> Callable[[], str]:
    """创建自定义时间格式函数"""
    def formatter() -> str:
        return datetime.now().strftime(format_string)
    
    formatter.__name__ = f"get_time_{format_string.replace('%', '').replace(' ', '_').replace(':', '').replace('/', '_')}"
    formatter.__doc__ = f"获取格式为 {format_string} 的时间"
    return formatter


# ==================== 预定义动态变量集合 ====================

# 常用时间变量
TIME_VARIABLES = {
    "current_time": get_current_time,
    "current_date": get_current_date,
    "current_datetime": get_current_datetime,
    "current_timestamp": get_current_timestamp,
    "current_weekday": get_current_weekday,
    "current_month": get_current_month,
    "current_year": get_current_year,
}

# 系统信息变量
SYSTEM_VARIABLES = {
    "system_platform": get_system_platform,
    "system_os": get_system_os,
    "python_version": get_python_version,
    "hostname": get_hostname,
    "username": get_username,
    "working_directory": get_working_directory,
    "random_uuid": get_random_uuid,
    "short_uuid": get_short_uuid,
}

# 注意：Agent相关变量现在通过通用字段获取器创建，支持以下功能：
# 1. 使用预定义便捷函数：create_agent_name_getter(agent_context)
# 2. 使用通用字段获取器：create_agent_field_getter("agent_name", agent_context, "默认值")
# 3. 获取嵌套字段：create_agent_field_getter("model_config.llm_model_name", agent_context)
# 4. 自定义处理：create_agent_field_getter("field", ctx, processor=lambda x: f"处理:{x}")
# 5. 批量创建：create_multiple_field_getters([("field1", "default1"), ...], ctx)
# 6. 获取所有变量：create_all_variables(agent_context)
# 7. 自定义字段扩展：create_agent_variables_with_custom_fields(ctx, custom_fields)
