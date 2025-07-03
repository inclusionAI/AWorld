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


# ==================== 支持运行时 Context 的新函数 ====================

def create_simple_field_getter(field_path: str, default: str = "") -> Callable[["Context"], str]:
    def field_getter(context: "Context" = None) -> str:
        return _get_context_field_value_with_fallback(context, field_path, default)
    
    return field_getter


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

def _get_context_field_value_with_fallback(context: "Context", field_path: str, default_value: str) -> str:
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




# ==================== 自定义Formatter支持的字段获取函数 ====================

def create_formatted_field_getter(
    field_path: str, 
    formatter: Optional[Callable[[Any], str]] = None,
    default: str = ""
) -> Callable[["Context"], str]:
    """创建支持自定义formatter的字段获取函数
    
    Args:
        field_path: 字段路径，支持嵌套访问，如 "agent_name" 或 "model_config.llm_model_name"
        formatter: 自定义格式化函数，用于将获取到的对象转换为字符串
                  可以处理OrderedDict、list、dict等复杂对象
        default: 字段不存在或格式化失败时的默认值
        
    Returns:
        可以接受 context 参数的函数
        
    Examples:
        # 格式化OrderedDict对象
        >>> def format_ordered_dict(od):
        ...     if not od:
        ...         return "Empty OrderedDict"
        ...     items = [f"{k}: {v}" for k, v in od.items()]
        ...     return "OrderedDict({" + ", ".join(items) + "})"
        
        >>> getter = create_formatted_field_getter(
        ...     "some_ordered_dict_field", 
        ...     formatter=format_ordered_dict,
        ...     default="No OrderedDict available"
        ... )
        
        # 格式化列表对象
        >>> def format_list_items(items):
        ...     if not items:
        ...         return "No items"
        ...     return f"Items: {', '.join(str(item) for item in items)}"
        
        >>> list_getter = create_formatted_field_getter(
        ...     "tool_names", 
        ...     formatter=format_list_items,
        ...     default="No tools"
        ... )
        
        # 格式化字典对象为JSON
        >>> import json
        >>> json_getter = create_formatted_field_getter(
        ...     "config_data", 
        ...     formatter=lambda d: json.dumps(d, indent=2) if d else "{}",
        ...     default="{}"
        ... )
        
        # 格式化复杂对象
        >>> def format_complex_object(obj):
        ...     if hasattr(obj, '__dict__'):
        ...         return f"{obj.__class__.__name__}: {vars(obj)}"
        ...     return str(obj)
        
        >>> complex_getter = create_formatted_field_getter(
        ...     "some_object",
        ...     formatter=format_complex_object,
        ...     default="No object"
        ... )
    """
    def field_getter(context: "Context" = None) -> str:
        if not context:
            return default
            
        try:
            # 获取字段值
            value = get_value_by_path(context, field_path)
            
            if value is None:
                return default
            
            # 如果提供了formatter，使用它来格式化
            if formatter is not None:
                try:
                    return formatter(value)
                except Exception as e:
                    # 格式化失败时记录错误并返回默认值
                    import logging
                    logging.warning(f"Formatter failed for field '{field_path}': {e}")
                    return default
            
            # 没有formatter时，使用默认的字符串转换
            return str(value)
            
        except Exception:
            return default
    
    return field_getter


def create_advanced_field_getter(
    field_path: str,
    formatter: Optional[Callable[[Any], str]] = None,
    fallback_paths: Optional[list[str]] = None,
    transform: Optional[Callable[[Any], Any]] = None,
    default: str = ""
) -> Callable[["Context"], str]:
    """创建高级字段获取函数，支持多种自定义选项
    
    Args:
        field_path: 主字段路径
        formatter: 自定义格式化函数
        fallback_paths: 后备字段路径列表，按顺序尝试
        transform: 在格式化之前对值进行转换的函数
        default: 默认值
        
    Returns:
        可以接受 context 参数的函数
        
    Examples:
        # 复杂的OrderedDict处理
        >>> def transform_ordered_dict(od):
        ...     # 转换OrderedDict为普通dict以便处理
        ...     return dict(od) if hasattr(od, 'items') else od
        
        >>> def format_dict_pretty(d):
        ...     if not d:
        ...         return "Empty"
        ...     lines = [f"  {k}: {v}" for k, v in d.items()]
        ...     return "{\n" + "\n".join(lines) + "\n}"
        
        >>> advanced_getter = create_advanced_field_getter(
        ...     field_path="primary_config",
        ...     fallback_paths=["secondary_config", "default_config"],
        ...     transform=transform_ordered_dict,
        ...     formatter=format_dict_pretty,
        ...     default="No configuration available"
        ... )
        
        # 处理嵌套对象
        >>> def extract_nested_info(obj):
        ...     # 从复杂对象中提取关键信息
        ...     if hasattr(obj, 'name') and hasattr(obj, 'version'):
        ...         return {'name': obj.name, 'version': obj.version}
        ...     return obj
        
        >>> def format_info(info):
        ...     if isinstance(info, dict):
        ...         return f"{info.get('name', 'Unknown')} v{info.get('version', '0.0')}"
        ...     return str(info)
        
        >>> info_getter = create_advanced_field_getter(
        ...     field_path="software_info",
        ...     transform=extract_nested_info,
        ...     formatter=format_info,
        ...     default="No software info"
        ... )
    """
    def field_getter(context: "Context" = None) -> str:
        if not context:
            return default
            
        # 尝试获取字段值，包括fallback路径
        value = None
        paths_to_try = [field_path] + (fallback_paths or [])
        
        for path in paths_to_try:
            try:
                value = get_value_by_path(context, path)
                if value is not None:
                    break
            except Exception:
                continue
        
        if value is None:
            return default
        
        try:
            # 应用转换函数
            if transform is not None:
                value = transform(value)
            
            # 应用格式化函数
            if formatter is not None:
                return formatter(value)
            
            # 默认字符串转换
            return str(value)
            
        except Exception as e:
            import logging
            logging.warning(f"Advanced field getter failed for field '{field_path}': {e}")
            return default
    
    return field_getter


# ==================== 预定义的Formatter函数 ====================

def format_ordered_dict_simple(od) -> str:
    """简单的OrderedDict格式化器"""
    if not od or not hasattr(od, 'items'):
        return "Empty"
    items = [f"{k}={v}" for k, v in od.items()]
    return ", ".join(items)


def format_ordered_dict_json(od) -> str:
    """将OrderedDict格式化为JSON字符串"""
    import json
    if not od:
        return "{}"
    try:
        # 转换为普通dict然后格式化为JSON
        regular_dict = dict(od) if hasattr(od, 'items') else od
        return json.dumps(regular_dict, ensure_ascii=False, indent=2)
    except Exception:
        return str(od)


def format_list_items(items) -> str:
    """格式化列表项"""
    if not items:
        return "Empty list"
    if isinstance(items, (list, tuple)):
        return f"[{', '.join(str(item) for item in items)}]"
    return str(items)


def format_dict_keys(d) -> str:
    """格式化字典键"""
    if not d or not hasattr(d, 'keys'):
        return "No keys"
    return f"Keys: {', '.join(str(k) for k in d.keys())}"


def format_object_summary(obj) -> str:
    """格式化对象摘要信息"""
    if obj is None:
        return "None"
    
    obj_type = type(obj).__name__
    
    if hasattr(obj, '__len__'):
        try:
            length = len(obj)
            return f"{obj_type}(length={length})"
        except:
            pass
    
    if hasattr(obj, '__dict__'):
        attrs = len(vars(obj))
        return f"{obj_type}(attributes={attrs})"
    
    return f"{obj_type}: {str(obj)[:50]}..."


# ==================== 便捷函数 ====================

def create_ordered_dict_getter(field_path: str, format_style: str = "simple", default: str = "Empty") -> Callable[["Context"], str]:
    """创建OrderedDict专用的字段获取函数
    
    Args:
        field_path: 字段路径
        format_style: 格式化风格，可选 "simple", "json", "keys"
        default: 默认值
        
    Returns:
        字段获取函数
    """
    formatters = {
        "simple": format_ordered_dict_simple,
        "json": format_ordered_dict_json,
        "keys": format_dict_keys,
    }
    
    formatter = formatters.get(format_style, format_ordered_dict_simple)
    return create_formatted_field_getter(field_path, formatter, default)


def create_list_getter(field_path: str, separator: str = ", ", default: str = "Empty list") -> Callable[["Context"], str]:
    """创建列表专用的字段获取函数
    
    Args:
        field_path: 字段路径
        separator: 列表项分隔符
        default: 默认值
        
    Returns:
        字段获取函数
    """
    def list_formatter(items):
        if not items:
            return "Empty list"
        if isinstance(items, (list, tuple)):
            return separator.join(str(item) for item in items)
        return str(items)
    
    return create_formatted_field_getter(field_path, list_formatter, default)

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

# Context相关变量 - 支持运行时Context传入
CONTEXT_VARIABLES = {
    "trajectories": create_simple_field_getter("trajectories"),
}

# 全部变量集合 - 包含时间、系统和Context变量
ALL_DYNAMIC_VARIABLES = {
    **TIME_VARIABLES,
    **SYSTEM_VARIABLES,
    **CONTEXT_VARIABLES,
}
