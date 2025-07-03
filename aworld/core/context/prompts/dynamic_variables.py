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
    ...     create_context_field_getter,
    ...     create_context_name_getter
    ... )
    >>> 
    >>> # 使用预定义的便捷函数
    >>> get_agent_name = create_context_name_getter(context)
    >>> 
    >>> # 或者使用通用的字段获取器
    >>> get_agent_name = create_context_field_getter("agent_name", context, "Assistant")
    >>> 
    >>> # 获取嵌套字段
    >>> get_model_name = create_context_field_getter("model_config.llm_model_name", context, "unknown")
    >>> 
    >>> # 带自定义处理的字段
    >>> get_tools = create_context_field_getter(
    ...     "tool_names", context, "无工具",
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
- 支持简单字段访问：create_context_field_getter("agent_name", ctx)
- 支持嵌套字段访问：create_context_field_getter("model_config.llm_model_name", ctx) 
- 支持自定义处理函数：processor=lambda x: f"处理后的{x}"
- 支持后备获取函数：fallback_getter=lambda ctx: ctx.get_special_field()
- 支持默认值：default_value="默认值"
"""

import os
import platform
import socket
import uuid
from datetime import datetime, timezone
from typing import Callable, Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from aworld.core.context.base import Context


# ==================== 通用路径获取函数 ====================

def get_value_by_path(obj: Any, field_path: str) -> Any:
    """通用的根据路径获取对象成员变量的函数
    
    Args:
        obj: 要获取值的对象
        field_path: 字段路径，支持嵌套访问，如 "agent_name" 或 "model_config.llm_model_name"
        
    Returns:
        获取到的值，如果路径不存在则返回 None
        
    Examples:
        >>> value = get_value_by_path(context, "agent_name")
        >>> model_name = get_value_by_path(context, "model_config.llm_model_name")
        >>> nested_value = get_value_by_path(obj, "a.b.c.d")
    """
    if obj is None:
        return None
        
    try:
        current_value = obj
        for field in field_path.split('.'):
            if hasattr(current_value, field):
                current_value = getattr(current_value, field)
            else:
                return None
        return current_value
    except Exception:
        return None


class ContextFieldGetter:
    """可序列化的Context字段获取器类，用于替代闭包函数
    
    这个类可以被pickle序列化，解决深拷贝时的序列化问题
    """
    
    def __init__(self, 
                 field_path: str, 
                 context: "Context" = None, 
                 default_value: str = "unknown",
                 processor: Optional[Callable[[Any], str]] = None,
                 fallback_getter: Optional[Callable[["Context"], Any]] = None):
        """初始化字段获取器
        
        Args:
            field_path: 字段路径，支持嵌套访问
            context: Context实例
            default_value: 默认值
            processor: 值处理函数（注意：如果使用lambda，仍然不能序列化）
            fallback_getter: 后备获取函数（注意：如果使用lambda，仍然不能序列化）
        """
        self.field_path = field_path
        self.context = context
        self.default_value = default_value
        self.processor = processor
        self.fallback_getter = fallback_getter
        
        # 设置函数属性以兼容原有接口
        safe_field_name = field_path.replace('.', '_')
        self.__name__ = f"get_context_{safe_field_name}"
        self.__doc__ = f"获取Context的{field_path}字段"
    
    def __call__(self, context: "Context" = None) -> str:
        """调用获取器获取字段值
        
        Args:
            context: 可选的Context实例，如果提供则使用此实例，否则使用初始化时的实例
            
        Returns:
            字段值或默认值
        """
        # 使用传入的context或初始化时的context
        ctx = context or self.context
        
        if not ctx:
            return self.default_value
            
        try:
            # 首先尝试从 context 中获取值
            value = get_value_by_path(ctx, self.field_path)
            
            # 如果字段路径失败，尝试使用后备获取函数
            if value is None and self.fallback_getter:
                value = self.fallback_getter(ctx)
            
            # 如果仍然没有值，返回默认值
            if value is None:
                return self.default_value
            
            # 应用处理函数
            if self.processor:
                return self.processor(value)
            
            # 直接转换为字符串
            return str(value)
            
        except Exception:
            return self.default_value
    
    def __getstate__(self):
        """自定义序列化状态，处理不可序列化的函数"""
        state = self.__dict__.copy()
        # 如果processor或fallback_getter不能序列化，则设为None
        if self.processor and not self._is_serializable(self.processor):
            state['processor'] = None
        if self.fallback_getter and not self._is_serializable(self.fallback_getter):
            state['fallback_getter'] = None
        return state
    
    def __setstate__(self, state):
        """自定义反序列化状态"""
        self.__dict__.update(state)
    
    def _is_serializable(self, func):
        """检查函数是否可序列化"""
        try:
            import pickle
            pickle.dumps(func)
            return True
        except:
            return False


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


# ==================== Context 字段获取函数工厂 ====================

def create_context_field_getter(
    field_path: str, 
    context: "Context" = None, 
    default_value: str = "unknown",
    processor: Optional[Callable[[Any], str]] = None,
    fallback_getter: Optional[Callable[["Context"], Any]] = None
) -> ContextFieldGetter:
    """创建获取Context指定字段的通用动态函数
    
    Args:
        field_path: 字段路径，支持嵌套访问，如 "agent_name" 或 "model_config.llm_model_name"
        context: Context实例
        default_value: 字段不存在时的默认值
        processor: 可选的值处理函数，接收原始值返回字符串
        fallback_getter: 可选的后备获取函数，当字段路径访问失败时使用
        
    Returns:
        返回一个可序列化的ContextFieldGetter实例
        
    Examples:
        # 简单字段
        get_agent_name = create_context_field_getter("agent_name", ctx, "Assistant")
        
        # 嵌套字段
        get_model = create_context_field_getter("model_config.llm_model_name", ctx, "unknown_model")
        
        # 带处理函数
        get_prompt_preview = create_context_field_getter(
            "system_prompt", ctx, "No system prompt",
            processor=lambda p: p[:100] + "..." if len(p) > 100 else p
        )
        
        # 带后备获取函数
        get_tools = create_context_field_getter(
            "tool_names", ctx, "No tools available",
            processor=lambda tools: ", ".join(tools) if tools else "No tools available"
        )
    """
    return ContextFieldGetter(
        field_path=field_path,
        context=context,
        default_value=default_value,
        processor=processor,
        fallback_getter=fallback_getter
    )


# ==================== 便捷创建函数 ====================

def create_simple_field_getter(field_path: str, context: "Context" = None, default: str = "") -> ContextFieldGetter:
    """创建简单字段获取器的便捷函数
    
    Args:
        field_path: 字段路径，支持嵌套如 "model_config.llm_model_name"
        context: Context实例
        default: 默认值
        
    Returns:
        可序列化的ContextFieldGetter实例
    """
    return ContextFieldGetter(field_path, context, default)


def create_multiple_field_getters(
    field_configs: list[tuple[str, str]], 
    context: "Context"
) -> dict[str, ContextFieldGetter]:
    """批量创建多个字段获取器
    
    Args:
        field_configs: 字段配置列表，每个元素为 (字段路径, 默认值) 的元组
        context: Context实例
        
    Returns:
        字段名到获取函数的映射字典
        
    Example:
        >>> getters = create_multiple_field_getters([
        ...     ("agent_name", "Assistant"),
        ...     ("agent_id", "unknown"),
        ...     ("model_config.llm_model_name", "unknown_model")
        ... ], context)
        >>> 
        >>> # 使用获取器
        >>> agent_name = getters["agent_name"]()
    """
    getters = {}
    for field_path, default_value in field_configs:
        safe_key = field_path.replace('.', '_')
        getters[safe_key] = ContextFieldGetter(field_path, context, default_value)
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

# 注意：Context相关变量现在通过通用字段获取器创建，支持以下功能：
# 1. 使用预定义便捷函数：create_context_name_getter(context)
# 2. 使用通用字段获取器：create_context_field_getter("agent_name", context, "默认值")
# 3. 获取嵌套字段：create_context_field_getter("model_config.llm_model_name", context)
# 4. 自定义处理：create_context_field_getter("field", ctx, processor=lambda x: f"处理:{x}")
# 5. 批量创建：create_multiple_field_getters([("field1", "default1"), ...], ctx)
# 6. 获取所有变量：create_all_variables(context)
# 7. 自定义字段扩展：create_context_variables_with_custom_fields(ctx, custom_fields)

# ==================== 支持运行时 Context 的新函数 ====================

def _get_context_field_value_with_fallback(context: "Context", field_path: str, default_value: str) -> str:
    """通用的获取Context字段值的helper函数
    
    Args:
        context: Context实例
        field_path: 字段路径，如 "agent_name" 或 "model_config.llm_model_name"
        default_value: 默认值
        
    Returns:
        字段值或默认值
    """
    if not context:
        return default_value
        
    try:
        # 首先尝试从 context 中获取值
        value = get_value_by_path(context, field_path)
        
        # 如果获取到值，转换为字符串返回
        if value is not None:
            return str(value)
        
        return default_value
    except Exception:
        return default_value


def get_agent_name_from_context(context: "Context" = None) -> str:
    """从context获取agent名称"""
    return _get_context_field_value_with_fallback(context, "agent_name", "unknown_agent")


def get_agent_id_from_context(context: "Context" = None) -> str:
    """从context获取agent ID"""
    return _get_context_field_value_with_fallback(context, "agent_id", "unknown_id")


def get_agent_desc_from_context(context: "Context" = None) -> str:
    """从context获取agent描述"""
    return _get_context_field_value_with_fallback(context, "agent_desc", "unknown_desc")


def get_system_prompt_from_context(context: "Context" = None) -> str:
    """从context获取系统提示"""
    value = _get_context_field_value_with_fallback(context, "system_prompt", "")
    return value if value else "No system prompt"


def get_model_name_from_context(context: "Context" = None) -> str:
    """从context获取模型名称"""
    return _get_context_field_value_with_fallback(context, "model_config.llm_model_name", "unknown_model")


def get_current_step_from_context(context: "Context" = None) -> str:
    """从context获取当前步骤"""
    return _get_context_field_value_with_fallback(context, "step", "0")


def get_tools_from_context(context: "Context" = None) -> str:
    """从context获取工具列表"""
    if not context:
        return "No tools available"
    
    try:
        # 首先尝试从 context 中获取值
        tools = get_value_by_path(context, "tool_names")
        
        if tools and isinstance(tools, (list, tuple)):
            return ", ".join(str(tool) for tool in tools)
        
        return "No tools available"
    except Exception:
        return "No tools available"


def get_trajectories_from_context(context: "Context" = None) -> str:
    """从context获取trajectories轨迹信息"""
    if not context:
        return "No trajectories"
    
    try:
        # 获取trajectories字段
        trajectories = get_value_by_path(context, "trajectories")
        
        if not trajectories:
            return "No trajectories"
        
        # 如果是列表，格式化输出
        if isinstance(trajectories, (list, tuple)):
            if len(trajectories) == 0:
                return "No trajectories"
            
            # 格式化轨迹信息
            formatted_trajectories = []
            for i, trajectory in enumerate(trajectories):
                if hasattr(trajectory, '__dict__'):
                    # 如果是对象，尝试获取关键信息
                    info_parts = []
                    if hasattr(trajectory, 'action'):
                        info_parts.append(f"action: {trajectory.action}")
                    if hasattr(trajectory, 'observation'):
                        obs = str(trajectory.observation)
                        # 截断过长的observation
                        if len(obs) > 100:
                            obs = obs[:100] + "..."
                        info_parts.append(f"observation: {obs}")
                    if hasattr(trajectory, 'thought'):
                        info_parts.append(f"thought: {trajectory.thought}")
                    
                    if info_parts:
                        formatted_trajectories.append(f"Step {i+1}: {', '.join(info_parts)}")
                    else:
                        formatted_trajectories.append(f"Step {i+1}: {str(trajectory)}")
                else:
                    # 如果不是对象，直接转换为字符串
                    formatted_trajectories.append(f"Step {i+1}: {str(trajectory)}")
            
            return "\n".join(formatted_trajectories)
        
        # 如果不是列表，直接转换为字符串
        return str(trajectories)
        
    except Exception:
        return "No trajectories"

# Context相关变量 - 支持运行时Context传入
CONTEXT_VARIABLES = {
    "agent_name": get_agent_name_from_context,
    "agent_id": get_agent_id_from_context, 
    "agent_desc": get_agent_desc_from_context,
    "system_prompt": get_system_prompt_from_context,
    "model_name": get_model_name_from_context,
    "current_step": get_current_step_from_context,
    "tools": get_tools_from_context,
    "trajectories": get_trajectories_from_context,
}

# 全部变量集合 - 包含时间、系统和Context变量
ALL_DYNAMIC_VARIABLES = {
    **TIME_VARIABLES,
    **SYSTEM_VARIABLES,
    **CONTEXT_VARIABLES,
}
