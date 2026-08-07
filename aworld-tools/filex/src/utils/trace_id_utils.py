"""
Trace ID生成和管理工具模块

提供统一的Trace ID生成、存储和日志集成功能
可在整个项目中复用
"""

import contextvars
import logging
import uuid
from datetime import datetime
from typing import Optional


# 创建Trace ID的ContextVar，用于在异步调用链中传递Trace ID
trace_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    'trace_id', default=None
)


def generate_trace_id(method_name: str) -> str:
    """生成Trace ID

    格式：{method_name}_{timestamp_ms}_{uuid_short}
    示例：upload_file_20241215-143022-123_a3f5b2

    参数：
        method_name: 方法名称（如 'upload_file', 'file_parse', 'convert_file'）

    返回：
        Trace ID字符串
    """
    # 获取当前时间戳（年月日时分秒毫秒）
    now = datetime.now()
    # 格式：YYYYMMDD-HHMMSS-mmm（年月日-时分秒-毫秒，便于查看）
    date_part = now.strftime("%Y%m%d")
    time_part = now.strftime("%H%M%S")
    millisecond_part = now.strftime("%f")[:3]  # 取前3位毫秒
    timestamp_ms = f"{date_part}-{time_part}-{millisecond_part}"

    # 生成6位随机字符串（从UUID中截取）
    uuid_short = uuid.uuid4().hex[:6]

    # 组合：方法名_时间戳_随机字符串
    trace_id = f"{method_name}_{timestamp_ms}_{uuid_short}"

    return trace_id


def get_trace_id() -> Optional[str]:
    """获取当前Trace ID

    返回：
        当前Trace ID，如果未设置则返回None
    """
    return trace_id_var.get()


def set_trace_id(trace_id: str) -> None:
    """设置Trace ID到当前上下文

    参数：
        trace_id: Trace ID字符串
    """
    trace_id_var.set(trace_id)


def reset_trace_id() -> None:
    """重置Trace ID（清除当前上下文中的Trace ID）"""
    trace_id_var.set(None)


class TraceIdFilter(logging.Filter):
    """日志过滤器，自动添加trace_id到日志记录

    使用方法：
        import logging
        from utils.trace_id_utils import TraceIdFilter

        handler = logging.StreamHandler()
        handler.addFilter(TraceIdFilter())

        或者在日志配置中：
        logging.basicConfig(
            format='[%(levelname)s] [trace_id=%(trace_id)s] %(message)s'
        )
        for handler in logging.root.handlers:
            handler.addFilter(TraceIdFilter())
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """过滤日志记录，添加trace_id字段"""
        record.trace_id = get_trace_id() or "N/A"
        return True


def get_trace_id_for_logging() -> str:
    """获取用于日志记录的Trace ID（如果不存在则返回"N/A"）

    返回：
        Trace ID字符串，如果未设置则返回"N/A"
    """
    return get_trace_id() or "N/A"
