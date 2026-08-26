import logging
from contextvars import ContextVar, Token

_TRACE_ID_CTX: ContextVar[str] = ContextVar("trace_id", default="NA")


def set_trace_id(trace_id: str | None) -> Token:
    value = trace_id.strip() if isinstance(trace_id, str) else ""
    return _TRACE_ID_CTX.set(value or "NA")


def reset_trace_id(token: Token) -> None:
    _TRACE_ID_CTX.reset(token)


def get_trace_id() -> str:
    return _TRACE_ID_CTX.get()


class TraceIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = get_trace_id()
        return True
