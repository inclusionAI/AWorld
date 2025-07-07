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
    >>> # Use predefined convenience functions
    >>> get_agent_name = create_context_name_getter(context)
    >>> 
    >>> # Or use generic field getter
    >>> get_agent_name = create_context_field_getter("agent_name", context, "Assistant")
    >>> 
    >>> # Get nested fields
    >>> get_model_name = create_context_field_getter("model_config.llm_model_name", context, "unknown")
    >>> 
    >>> # Fields with custom processing
    >>> get_tools = create_context_field_getter(
    ...     "tool_names", context, "No tools",
    ...     processor=lambda tools: ", ".join(tools) if tools else "No tools available"
    ... )
    >>> 
    >>> prompt = PromptTemplate(
    ...     template="Hello, I'm {agent_name}. Current time is {current_time}. {user_input}",
    ...     input_variables=["user_input"],
    ...     partial_variables={
    ...         "current_time": get_current_time,
    ...         "agent_name": get_agent_name,
    ...         "model_name": get_model_name,
    ...         "tools": get_tools
    ...     }
    ... )

Generic field getter functionality:
- Support simple field access: create_context_field_getter("agent_name", ctx)
- Support nested field access: create_context_field_getter("model_config.llm_model_name", ctx) 
- Support custom processing functions: processor=lambda x: f"Processed {x}"
- Support fallback getter functions: fallback_getter=lambda ctx: ctx.get_special_field()
- Support default values: default_value="Default value"
"""

import os
import platform
import uuid
from aworld.core.context.prompts import logger
from datetime import datetime, timezone
from typing import Callable, Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from aworld.core.context.base import Context

# ==================== Generic Path Getter Functions ====================

def get_value_by_path(obj: Any, field_path: str) -> Any:
    """Generic function to get object member variables by path
    
    Args:
        obj: Object to get value from
        field_path: Field path, supports nested access like "agent_name" or "model_config.llm_model_name"
        
    Returns:
        Retrieved value, returns None if path doesn't exist
        
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
    """Serializable Context field getter class to replace closure functions
    
    This class can be pickle serialized, solving serialization issues during deep copy
    """
    
    def __init__(self, 
                 field_path: str, 
                 context: "Context" = None, 
                 default_value: str = "unknown",
                 processor: Optional[Callable[[Any], str]] = None,
                 fallback_getter: Optional[Callable[["Context"], Any]] = None):
        """Initialize field getter
        
        Args:
            field_path: Field path, supports nested access
            context: Context instance
            default_value: Default value
            processor: Value processing function (note: if using lambda, still cannot be serialized)
            fallback_getter: Fallback getter function (note: if using lambda, still cannot be serialized)
        """
        self.field_path = field_path
        self.context = context
        self.default_value = default_value
        self.processor = processor
        self.fallback_getter = fallback_getter
        
        # Set function attributes for compatibility with original interface
        safe_field_name = field_path.replace('.', '_')
        self.__name__ = f"get_context_{safe_field_name}"
        self.__doc__ = f"Get Context's {field_path} field"
    
    def __call__(self, context: "Context" = None) -> str:
        """Call getter to retrieve field value
        
        Args:
            context: Optional Context instance, if provided use this instance, otherwise use initialization instance
            
        Returns:
            Field value or default value
        """
        # Use passed context or initialization context
        ctx = context or self.context
        
        if not ctx:
            return self.default_value
            
        try:
            # First try to get value from context
            value = get_value_by_path(ctx, self.field_path)
            
            # If field path fails, try using fallback getter function
            if value is None and self.fallback_getter:
                value = self.fallback_getter(ctx)
            
            # If still no value, return default value
            if value is None:
                return self.default_value
            
            # Apply processing function
            if self.processor:
                return self.processor(value)
            
            # Direct conversion to string
            return str(value)
            
        except Exception:
            return self.default_value
    
    def __getstate__(self):
        """Custom serialization state, handle non-serializable functions"""
        state = self.__dict__.copy()
        # If processor or fallback_getter cannot be serialized, set to None
        if self.processor and not self._is_serializable(self.processor):
            state['processor'] = None
        if self.fallback_getter and not self._is_serializable(self.fallback_getter):
            state['fallback_getter'] = None
        return state
    
    def __setstate__(self, state):
        """Custom deserialization state"""
        self.__dict__.update(state)
    
    def _is_serializable(self, func):
        """Check if function is serializable"""
        try:
            import pickle
            pickle.dumps(func)
            return True
        except:
            return False


# ==================== Time Related Functions ====================

def get_current_time() -> str:
    """Get current time (MM/DD/YYYY, HH:MM:SS)"""
    try:
        return datetime.now().strftime("%m/%d/%Y, %H:%M:%S")
    except Exception:
        logger.warning("Failed to get current time.")
        return "unknown time"


def get_current_date() -> str:
    """Get current date (YYYY-MM-DD)"""
    try:
        return datetime.now().strftime("%Y-%m-%d")
    except Exception:
        logger.warning("Failed to get current date.")
        return "unknown date"


def get_current_datetime() -> str:
    """Get current datetime (YYYY-MM-DD HH:MM:SS)"""
    try:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        logger.warning("Failed to get current datetime.")
        return "unknown datetime"


def get_current_timestamp() -> str:
    """Get current timestamp"""
    try:
        return str(int(datetime.now().timestamp()))
    except Exception:
        logger.warning("Failed to get current timestamp.")
        return "unknown timestamp"


def get_current_iso_time() -> str:
    """Get current ISO format time"""
    try:
        return datetime.now(timezone.utc).isoformat()
    except Exception:
        logger.warning("Failed to get current ISO time.")
        return "unknown iso time"


def get_current_weekday() -> str:
    """Get current weekday"""
    try:
        weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        return weekdays[datetime.now().weekday()]
    except Exception:
        logger.warning("Failed to get current weekday.")
        return "unknown weekday"


def get_current_month() -> str:
    """Get current month name"""
    try:
        return datetime.now().strftime("%B")
    except Exception:
        logger.warning("Failed to get current month.")
        return "unknown month"


def get_current_year() -> str:
    """Get current year"""
    try:
        return str(datetime.now().year)
    except Exception:
        logger.warning("Failed to get current year.")
        return "unknown year"


# ==================== System Information Functions ====================

def get_system_platform() -> str:
    """Get system platform information"""
    try:
        return platform.platform()
    except Exception:
        logger.warning("Failed to get system platform.")
        return "unknown platform"


def get_system_os() -> str:
    """Get operating system name"""
    try:
        return platform.system()
    except Exception:
        logger.warning("Failed to get system OS.")
        return "unknown os"


def get_python_version() -> str:
    """Get Python version"""
    try:
        return platform.python_version()
    except Exception:
        logger.warning("Failed to get Python version.")
        return "unknown python version"


def get_hostname() -> str:
    """Get hostname"""
    try:
        return platform.node()
    except Exception:
        logger.warning("Failed to get hostname.")
        return "unknown hostname"


def get_username() -> str:
    """Get current username"""
    try:
        return os.getenv("USER") or os.getenv("USERNAME") or "unknown"
    except Exception:
        logger.warning("Failed to get username.")
        return "unknown user"


def get_working_directory() -> str:
    """Get current working directory"""
    try:
        return os.getcwd()
    except Exception:
        logger.warning("Failed to get working directory.")
        return "unknown directory"


def get_random_uuid() -> str:
    """Generate random UUID"""
    try:
        return str(uuid.uuid4())
    except Exception:
        logger.warning("Failed to generate random UUID.")
        return "unknown uuid"


def get_short_uuid() -> str:
    """Generate short UUID (first 8 characters)"""
    try:
        return str(uuid.uuid4())[:8]
    except Exception:
        logger.warning("Failed to generate short UUID.")
        return "unknown"


# ==================== Context Field Getter Function Factory ====================

def create_context_field_getter(
    field_path: str, 
    context: "Context" = None, 
    default_value: str = "unknown",
    processor: Optional[Callable[[Any], str]] = None,
    fallback_getter: Optional[Callable[["Context"], Any]] = None
) -> ContextFieldGetter:
    """Create generic dynamic function to get specified Context field
    
    Args:
        field_path: Field path, supports nested access like "agent_name" or "model_config.llm_model_name"
        context: Context instance
        default_value: Default value when field doesn't exist
        processor: Optional value processing function, receives original value returns string
        fallback_getter: Optional fallback getter function, used when field path access fails
        
    Returns:
        Returns a serializable ContextFieldGetter instance
        
    Examples:
        # Simple field
        get_agent_name = create_context_field_getter("agent_name", ctx, "Assistant")
        
        # Nested field
        get_model = create_context_field_getter("model_config.llm_model_name", ctx, "unknown_model")
        
        # With processing function
        get_prompt_preview = create_context_field_getter(
            "system_prompt", ctx, "No system prompt",
            processor=lambda p: p[:100] + "..." if len(p) > 100 else p
        )
        
        # With fallback getter function
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


# ==================== Functions Supporting Runtime Context ====================

def create_simple_field_getter(field_path: str, default: str = "") -> Callable[["Context"], str]:
    def field_getter(context: "Context" = None) -> str:
        return _get_context_field_value_with_fallback(context, field_path, default)
    
    return field_getter


def create_multiple_field_getters(
    field_configs: list[tuple[str, str]], 
    context: "Context"
) -> dict[str, ContextFieldGetter]:
    """Batch create multiple field getters
    
    Args:
        field_configs: List of field configurations, each element is a tuple of (field_path, default_value)
        context: Context instance
        
    Returns:
        Dictionary mapping field names to getter functions
        
    Example:
        >>> getters = create_multiple_field_getters([
        ...     ("agent_name", "Assistant"),
        ...     ("agent_id", "unknown"),
        ...     ("model_config.llm_model_name", "unknown_model")
        ... ], context)
        >>> 
        >>> # Use getters
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
        # First try to get value from context
        value = get_value_by_path(context, field_path)
        
        # If got value, convert to string and return
        if value is not None:
            return str(value)
        
        return default_value
    except Exception:
        return default_value




# ==================== Field Getter Functions Supporting Custom Formatters ====================

def create_formatted_field_getter(
    field_path: str, 
    formatter: Optional[Callable[[Any], str]] = None,
    default: str = ""
) -> Callable[["Context"], str]:
    """Create field getter function supporting custom formatter
    
    Args:
        field_path: Field path, supports nested access like "agent_name" or "model_config.llm_model_name"
        formatter: Custom formatting function to convert retrieved object to string
                  Can handle complex objects like OrderedDict, list, dict, etc.
        default: Default value when field doesn't exist or formatting fails
        
    Returns:
        Function that can accept context parameter
        
    Examples:
        # Format OrderedDict object
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
        
        # Format list object
        >>> def format_list_items(items):
        ...     if not items:
        ...         return "No items"
        ...     return f"Items: {', '.join(str(item) for item in items)}"
        
        >>> list_getter = create_formatted_field_getter(
        ...     "tool_names", 
        ...     formatter=format_list_items,
        ...     default="No tools"
        ... )
        
        # Format dictionary object as JSON
        >>> import json
        >>> json_getter = create_formatted_field_getter(
        ...     "config_data", 
        ...     formatter=lambda d: json.dumps(d, indent=2) if d else "{}",
        ...     default="{}"
        ... )
        
        # Format complex object
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
            # Get field value
            value = get_value_by_path(context, field_path)
            
            if value is None:
                return default
            
            # If formatter is provided, use it to format
            if formatter is not None:
                try:
                    return formatter(value)
                except Exception as e:
                    # Log error when formatting fails and return default value
                    logger.warning(f"Formatter failed for field '{field_path}': {e}")
                    return default
            
            # Without formatter, use default string conversion
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
    """Create advanced field getter function supporting multiple custom options
    
    Args:
        field_path: Primary field path
        formatter: Custom formatting function
        fallback_paths: List of fallback field paths, tried in order
        transform: Function to transform value before formatting
        default: Default value
        
    Returns:
        Function that can accept context parameter
        
    Examples:
        # Complex OrderedDict handling
        >>> def transform_ordered_dict(od):
        ...     # Convert OrderedDict to regular dict for easier handling
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
        
        # Handle nested objects
        >>> def extract_nested_info(obj):
        ...     # Extract key information from complex object
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
            
        # Try to get field value including fallback paths
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
            # Apply transform function
            if transform is not None:
                value = transform(value)
            
            # Apply formatting function
            if formatter is not None:
                return formatter(value)
            
            # Default string conversion
            return str(value)
            
        except Exception as e:
            logger.warning(f"Advanced field getter failed for field '{field_path}': {e}")
            return default
    
    return field_getter


# ==================== Predefined Formatter Functions ====================

def format_ordered_dict_simple(od) -> str:
    """Simple OrderedDict formatter"""
    if not od or not hasattr(od, 'items'):
        return "Empty"
    items = [f"{k}={v}" for k, v in od.items()]
    return ", ".join(items)


def format_ordered_dict_json(od) -> str:
    """Format OrderedDict as JSON string"""
    import json
    if not od:
        return "{}"
    try:
        # Convert to regular dict then format as JSON
        regular_dict = dict(od) if hasattr(od, 'items') else od
        return json.dumps(regular_dict, ensure_ascii=False, indent=2)
    except Exception:
        return str(od)


def format_list_items(items) -> str:
    """Format list items"""
    if not items:
        return "Empty list"
    if isinstance(items, (list, tuple)):
        return f"[{', '.join(str(item) for item in items)}]"
    return str(items)


def format_dict_keys(d) -> str:
    """Format dictionary keys"""
    if not d or not hasattr(d, 'keys'):
        return "No keys"
    return f"Keys: {', '.join(str(k) for k in d.keys())}"


def format_object_summary(obj) -> str:
    """Format object summary information"""
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


# ==================== Convenience Functions ====================

def create_ordered_dict_getter(field_path: str, format_style: str = "simple", default: str = "Empty") -> Callable[["Context"], str]:
    """Create OrderedDict-specific field getter function
    
    Args:
        field_path: Field path
        format_style: Formatting style, options: "simple", "json", "keys"
        default: Default value
        
    Returns:
        Field getter function
    """
    formatters = {
        "simple": format_ordered_dict_simple,
        "json": format_ordered_dict_json,
        "keys": format_dict_keys,
    }
    
    formatter = formatters.get(format_style, format_ordered_dict_simple)
    return create_formatted_field_getter(field_path, formatter, default)


def create_list_getter(field_path: str, separator: str = ", ", default: str = "Empty list") -> Callable[["Context"], str]:
    """Create list-specific field getter function
    
    Args:
        field_path: Field path
        separator: List item separator
        default: Default value
        
    Returns:
        Field getter function
    """
    def list_formatter(items):
        if not items:
            return "Empty list"
        if isinstance(items, (list, tuple)):
            return separator.join(str(item) for item in items)
        return str(items)
    
    return create_formatted_field_getter(field_path, list_formatter, default)

# ==================== Predefined Dynamic Variable Collections ====================

# Common time variables
TIME_VARIABLES = {
    "current_time": get_current_time,
    "current_date": get_current_date,
    "current_datetime": get_current_datetime,
    "current_timestamp": get_current_timestamp,
    "current_weekday": get_current_weekday,
    "current_month": get_current_month,
    "current_year": get_current_year,
}

# System information variables
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

# Context-related variables - support runtime Context passing
CONTEXT_VARIABLES = {
    "trajectories": create_simple_field_getter("trajectories"),
}

# All variable collections - includes time, system and Context variables
ALL_DYNAMIC_VARIABLES = {
    **TIME_VARIABLES,
    **SYSTEM_VARIABLES,
    **CONTEXT_VARIABLES,
}
