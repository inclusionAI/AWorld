"""Generate, propagate, and log trace IDs."""

import contextvars
import logging
import uuid
from datetime import datetime
from typing import Optional


# Propagate trace IDs through asynchronous call chains.
trace_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    'trace_id', default=None
)


def generate_trace_id(method_name: str) -> str:
    """Generate ``{method}_{timestamp_ms}_{uuid_short}`` trace IDs."""
    # Build a human-readable timestamp with millisecond precision.
    now = datetime.now()
    # Format: YYYYMMDD-HHMMSS-mmm.
    date_part = now.strftime("%Y%m%d")
    time_part = now.strftime("%H%M%S")
    millisecond_part = now.strftime("%f")[:3]  # Keep three millisecond digits.
    timestamp_ms = f"{date_part}-{time_part}-{millisecond_part}"

    # Add a six-character UUID suffix.
    uuid_short = uuid.uuid4().hex[:6]

    # Combine method, timestamp, and random suffix.
    trace_id = f"{method_name}_{timestamp_ms}_{uuid_short}"

    return trace_id


def get_trace_id() -> Optional[str]:
    """Return the current trace ID, if one is set."""
    return trace_id_var.get()


def set_trace_id(trace_id: str) -> None:
    """Set the trace ID for the current context."""
    trace_id_var.set(trace_id)


def reset_trace_id() -> None:
    """Clear the trace ID for the current context."""
    trace_id_var.set(None)


class TraceIdFilter(logging.Filter):
    """Logging filter that injects ``trace_id`` into every record.

    Example:
        import logging
        from utils.trace_id_utils import TraceIdFilter

        handler = logging.StreamHandler()
        handler.addFilter(TraceIdFilter())

        Or install it on configured root handlers:
        logging.basicConfig(
            format='[%(levelname)s] [trace_id=%(trace_id)s] %(message)s'
        )
        for handler in logging.root.handlers:
            handler.addFilter(TraceIdFilter())
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Add the current trace ID to a log record."""
        record.trace_id = get_trace_id() or "N/A"
        return True


def get_trace_id_for_logging() -> str:
    """Return a trace ID suitable for logging, or ``N/A``."""
    return get_trace_id() or "N/A"
