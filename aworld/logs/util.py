# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
import inspect
import json
import os
import re
import sys
import traceback
from enum import Enum
from typing import Union, Callable, Dict, Any, Optional

from loguru import logger as base_logger

base_logger.remove()
SEGMENT_LEN = 9999999999999
CONSOLE_LEVEL = 'INFO'
STORAGE_LEVEL = 'INFO'
SUPPORTED_FUNC = ['info', 'debug', 'warning', 'error', 'critical', 'exception', 'trace', 'success', 'log', 'catch',
                  'opt', 'bind', 'unbind', 'contextualize', 'patch']


class Color:
    """Supported more color in log."""
    black = '\033[30m'
    red = '\033[31m'
    green = '\033[32m'
    orange = '\033[33m'
    blue = '\033[34m'
    purple = '\033[35m'
    cyan = '\033[36m'
    lightgrey = '\033[37m'
    darkgrey = '\033[90m'
    lightred = '\033[91m'
    lightgreen = '\033[92m'
    yellow = '\033[93m'
    lightblue = '\033[94m'
    pink = '\033[95m'
    lightcyan = '\033[96m'
    reset = '\033[0m'
    bold = '\033[01m'
    disable = '\033[02m'
    underline = '\033[04m'
    reverse = '\033[07m'
    strikethrough = '\033[09m'


def aworld_log(logger, color: str = Color.black, level: str = "INFO"):
    """Colored log style in the Aworld.

    Args:
        color: Default color set, different types of information can be set in different colors.
        level: Log level.
    """
    def_color = color

    def decorator(value: str, color: str = None, highlight_key=None):
        # Set color in the called.
        if not color:
            color = def_color

        if highlight_key is None:
            logger.log(level, f"{color}  {value} {Color.reset}")
        else:
            logger.log(level, f"{color} {highlight_key}: {Color.reset} {value}")

    return decorator


LOGGER_COLOR = {"TRACE": Color.darkgrey, "DEBUG": Color.lightgrey, "INFO": Color.green,
                "SUCCESS": Color.lightgreen, "WARNING": Color.orange, "ERROR": Color.lightred,
                "FATAL": Color.red}


def monkey_logger(logger: base_logger):
    logger.trace = aworld_log(logger, color=LOGGER_COLOR.get("TRACE"), level="TRACE")
    logger.debug = aworld_log(logger, color=LOGGER_COLOR.get("DEBUG"), level="DEBUG")
    logger.info = aworld_log(logger, color=LOGGER_COLOR.get("INFO"), level="INFO")
    logger.success = aworld_log(logger, color=LOGGER_COLOR.get("SUCCESS"), level="SUCCESS")
    logger.warning = aworld_log(logger, color=LOGGER_COLOR.get("WARNING"), level="WARNING")
    logger.warn = logger.warning
    logger.error = aworld_log(logger, color=LOGGER_COLOR.get("ERROR"), level="ERROR")
    logger.exception = logger.error
    logger.fatal = aworld_log(logger, color=LOGGER_COLOR.get("FATAL"), level="FATAL")


class AWorldLogger:
    _added_handlers = set()

    def __init__(self, tag='AWorld',
                 name: str = 'AWorld',
                 console_level: str = CONSOLE_LEVEL,
                 formatter: Union[str, Callable] = None,
                 disable_console: bool = None,
                 file_log_config: Dict[str, Any] = None):
        """
        Initialize AWorldLogger.

        Args:
            tag: Logger tag
            name: Logger name
            console_level: Console log level
            file_log_config: File log config
            formatter: Custom formatter
            disable_console: If True, disable console output. If None, check environment variable AWORLD_DISABLE_CONSOLE_LOG.

        Example:
            >>> logger = AWorldLogger(disable_console=True)  # Disable console output
            >>> logger = AWorldLogger()  # Use environment variable or default (False)
        """
        self.tag = tag
        self.name = name
        self.console_level = console_level

        # Check environment variable if disable_console is not explicitly set
        if disable_console is None:
            disable_console = os.getenv('AWORLD_DISABLE_CONSOLE_LOG', 'false').lower() in ('true', '1', 'yes')

        self.disable_console = disable_console
        file_formatter = formatter
        console_formatter = formatter
        if not formatter:
            format = """<black>{extra[trace_id]} | {time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | \
{extra[name]} PID: {process}, TID:{thread} |</black> <bold>{name}.{function}:{line}</bold> \
- \n<level>{message}</level> {exception} """

            def _formatter(record):
                if record['extra'].get('name') == 'AWorld':
                    return f"{format.replace('{extra[name]} ', '')}\n"

                if record["name"] == 'aworld':
                    return f"{format.replace('</cyan>.', '</cyan>')}\n"
                return f"{format}\n"

            def file_formatter(record):
                record['message'] = record['message'][5:].strip()
                return _formatter(record)

            def console_formatter(record):
                part_len = SEGMENT_LEN
                record['message'] = record['message'][:-5].strip()
                if 1 < part_len < len(record['message']):
                    part = int(len(record['message']) / part_len)
                    lines = []
                    i = 0
                    for i in range(part):
                        lines.append(record['message'][i * part_len: (i + 1) * part_len])
                    if part and len(record['message']) % part_len != 0:
                        lines.append(record['message'][(i + 1) * part_len:])
                    record['message'] = "\n".join(lines)

                return _formatter(record)

            console_formatter = console_formatter
            file_formatter = file_formatter

        # Only add stderr handler if console output is not disabled
        if not disable_console:
            self.log_id = base_logger.add(sys.stderr,
                                          filter=lambda record: record['extra'].get('name') == tag,
                                          colorize=True,
                                          format=console_formatter,
                                          level=console_level)

        # Before using aworld, including imports!
        log_path = os.environ.get('AWORLD_LOG_PATH', f"{os.getcwd()}/logs")
        log_file = f'{log_path}/{tag}.log'
        error_log_file = f'{log_path}/aworld_error.log'
        handler_key = f'{name}_{tag}'
        error_handler_key = f'{name}_{tag}_error'

        file_log_config = file_log_config or {
            "rotation": "32 MB",
            "retention": "1 days",
            "enqueue": False,
            "backtrace": True,
            "compression": "zip"
        }
        if handler_key not in AWorldLogger._added_handlers:
            if "level" not in file_log_config:
                file_log_config["level"] = STORAGE_LEVEL
            self.file_log_id = base_logger.add(log_file,
                                               format=file_formatter,
                                               filter=lambda record: record['extra'].get('name') == tag,
                                               **file_log_config)
            file_log_config.pop("level")
            AWorldLogger._added_handlers.add(handler_key)

        # Add error log handler, specifically for logging WARNING and ERROR level logs
        if error_handler_key not in AWorldLogger._added_handlers:
            if "level" not in file_log_config and file_log_config.get('level') not in ['WARNING', 'ERROR', 'FATAL']:
                file_log_config["level"] = 'WARNING'
            self.error_log_id = base_logger.add(error_log_file,
                                                format=file_formatter,
                                                filter=lambda record: (record['extra'].get('name') == tag and
                                                                       record['level'].name in ['WARNING', 'ERROR', 'FATAL', 'CRITICAL']),
                                                **file_log_config)
            AWorldLogger._added_handlers.add(error_handler_key)

        self.formater = console_formatter
        self.file_log_config = file_log_config
        self._logger = base_logger.bind(name=tag)

    def reset_level(self, level: str):
        from aworld.logs.instrument.loguru_instrument import _get_handlers

        handlers = _get_handlers(self._logger)
        for handler in handlers:
            if handler._id == self.log_id or handler._id == self.file_log_id or handler._id == self.error_log_id:
                self._logger.remove(handler._id)
        # Clear error log handler record to ensure it can be added correctly when reinitializing
        error_handler_key = f'{self.name}_{self.tag}_error'
        AWorldLogger._added_handlers.discard(error_handler_key)
        self.__init__(tag=self.tag,
                      name=self.name,
                      formatter=self.formater,
                      console_level=level,
                      disable_console=self.disable_console,
                      file_log_config=self.file_log_config)

    def reset_format(self, format_str: str):
        from aworld.logs.instrument.loguru_instrument import _get_handlers

        handlers = _get_handlers(self._logger)
        for handler in handlers:
            if handler._id == self.log_id or handler._id == self.file_log_id or handler._id == self.error_log_id:
                self._logger.remove(handler._id)
        # Clear error log handler record to ensure it can be added correctly when reinitializing
        error_handler_key = f'{self.name}_{self.tag}_error'
        AWorldLogger._added_handlers.discard(error_handler_key)
        self.__init__(tag=self.tag,
                      name=self.name,
                      console_level=self.console_level,
                      formatter=format_str,
                      disable_console=self.disable_console,
                      file_log_config=self.file_log_config)

    def __getattr__(self, name: str):
        from aworld.trace.base import get_trace_id

        if name in SUPPORTED_FUNC:
            frame = inspect.currentframe().f_back
            if frame.f_back and (
                    # python3.11+
                    (getattr(frame.f_code, "co_qualname", None) == 'aworld_log.<locals>.decorator') or
                    # python3.10
                    (frame.f_code.co_name == 'decorator' and os.path.basename(frame.f_code.co_filename) == 'util.py')):
                frame = frame.f_back

            module = inspect.getmodule(frame)
            module = module.__name__ if module else ''
            line = frame.f_lineno
            func_name = getattr(frame.f_code, "co_qualname", frame.f_code.co_name).replace("<module>", "")

            trace_id = get_trace_id()
            update = {"function": func_name, "line": line, "name": module,
                      "extra": {"trace_id": trace_id, "logger_name": "Aworld"}}

            def patch(record):
                extra = update.pop("extra")
                record.update(update)
                record['extra'].update(extra)
                return record

            return getattr(self._logger.patch(patch), name)
        raise AttributeError(f"'AWorldLogger' object has no attribute '{name}'")


def update_logger_level(level: str):
    logger.reset_level(level)
    prompt_logger.reset_level(level)
    trajectory_logger.reset_level(level)
    trace_logger.reset_level(level)
    digest_logger.reset_level(level)
    asyncio_monitor_logger.reset_level(level)
    llm_logger.reset_level(level)


logger = AWorldLogger(tag='aworld', name='AWorld', formatter=os.getenv('AWORLD_LOG_FORMAT'))
trace_logger = AWorldLogger(tag='trace', name='AWorld', formatter=os.getenv('AWORLD_LOG_FORMAT'))
trajectory_logger = AWorldLogger(tag='trajectory', name='AWorld', formatter=os.getenv('AWORLD_LOG_FORMAT'))

prompt_logger = AWorldLogger(tag='prompt_logger', name='AWorld',
                             formatter="<black>{time:YYYY-MM-DD HH:mm:ss.SSS}|prompt|{extra[trace_id]}|</black><level>{message}</level>")
digest_logger = AWorldLogger(tag='digest_logger', name='AWorld',
                             formatter=os.getenv('AWORLD_LOG_FORMAT', "{time:YYYY-MM-DD HH:mm:ss.SSS}| digest | {extra[trace_id]} |<level>{message}</level>"))
asyncio_monitor_logger = AWorldLogger(tag='asyncio_monitor', name='AWorld',
                                      formatter="<black>{time:YYYY-MM-DD HH:mm:ss.SSS} | </black> <level>{message}</level>")

_LLM_LOG_FORMATTER = (
        "{time:YYYY-MM-DD HH:mm:ss.SSS}|llm|{extra[direction]}|{extra[trace_id]}|"
        "model={extra[model_name]}|{extra[meta]}|{message}"
)
llm_logger = AWorldLogger(tag='llm', name='AWorld', formatter=_LLM_LOG_FORMATTER)

_SAFE_LLM_STATUS = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_SAFE_LLM_RECEIPT_LABEL = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:+/-]{0,127}$"
)


def _llm_field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _text_size(value: Any) -> int:
    return len(value) if isinstance(value, str) else 0


def summarize_tool_calls_for_log(tool_calls: Any) -> Dict[str, int]:
    """Return content-free counts without serializing cumulative arguments."""
    if not isinstance(tool_calls, (list, tuple)):
        return {"tool_call_count": 0, "tool_argument_chars": 0}
    argument_chars = 0
    count = 0
    for tool_call in tool_calls:
        if tool_call is None:
            continue
        count += 1
        tool_call = _llm_field(tool_call, "data", tool_call)
        function = _llm_field(tool_call, "function", {})
        arguments = _llm_field(function, "arguments", "")
        argument_chars += _text_size(arguments)
    return {
        "tool_call_count": count,
        "tool_argument_chars": argument_chars,
    }


def summarize_llm_payload_for_log(data: Any) -> Dict[str, Any]:
    """Project one chunk to constant-size, content-free INFO telemetry."""
    tool_counts = summarize_tool_calls_for_log(
        _llm_field(data, "tool_calls")
    )
    finish_reason = _llm_field(data, "finish_reason")
    if not isinstance(finish_reason, str) or not _SAFE_LLM_STATUS.fullmatch(
        finish_reason
    ):
        finish_reason = None
    return {
        "schema_version": "aworld.llm-stream-log.v1",
        "content_chars": _text_size(_llm_field(data, "content")),
        "reasoning_chars": _text_size(_llm_field(data, "reasoning_content")),
        **tool_counts,
        "usage_reported": _llm_field(data, "usage_reported", False) is True,
        "finish_reason": finish_reason,
        "provider_request_id_present": bool(
            _llm_field(data, "provider_request_id")
        ),
    }


def _nested_text_chars(value: Any, *, depth: int = 0) -> int:
    if depth > 8:
        return 0
    if isinstance(value, str):
        return len(value)
    if isinstance(value, (list, tuple)):
        return sum(
            _nested_text_chars(item, depth=depth + 1)
            for item in value[:32]
        )
    if isinstance(value, dict):
        return sum(
            _nested_text_chars(item, depth=depth + 1)
            for item in list(value.values())[:32]
        )
    return 0


def _input_log_summary(data: Any) -> Dict[str, Any]:
    messages = data if isinstance(data, (list, tuple)) else ()
    recent_messages = messages[-8:]
    role_counts: Dict[str, int] = {}
    tool_call_count = 0
    for message in recent_messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if isinstance(role, str) and _SAFE_LLM_STATUS.fullmatch(role):
            role_counts[role] = role_counts.get(role, 0) + 1
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, (list, tuple)):
            tool_call_count += len(tool_calls)
    return {
        "schema_version": "aworld.llm-input-log.v1",
        "message_count": len(messages),
        "recent_message_count": len(recent_messages),
        "recent_message_text_chars": _nested_text_chars(recent_messages),
        "recent_role_counts": role_counts,
        "recent_tool_call_count": tool_call_count,
    }


def _request_params_log_summary(data: Any) -> Dict[str, Any]:
    params = data if isinstance(data, dict) else {}
    tools = params.get("tools")
    template = params.get("chat_template_kwargs")
    if not isinstance(template, dict):
        extra_body = params.get("extra_body")
        template = (
            extra_body.get("chat_template_kwargs")
            if isinstance(extra_body, dict)
            else None
        )
    effort = params.get("reasoning_effort")
    if not isinstance(effort, str) and isinstance(template, dict):
        effort = template.get("reasoning_effort")
    if not isinstance(effort, str) or not _SAFE_LLM_STATUS.fullmatch(effort):
        effort = None
    thinking = template.get("thinking") if isinstance(template, dict) else None
    return {
        "schema_version": "aworld.llm-request-params-log.v1",
        "parameter_keys": sorted(
            key
            for key in params
            if isinstance(key, str) and _SAFE_LLM_STATUS.fullmatch(key)
        )[:64],
        "tool_count": len(tools) if isinstance(tools, (list, tuple)) else 0,
        "sampled_tool_schema_text_chars": _nested_text_chars(
            tools[:16] if isinstance(tools, (list, tuple)) else ()
        ),
        "reasoning_effort": effort,
        "thinking": thinking if isinstance(thinking, bool) else None,
        "stream": params.get("stream") is True,
    }


def summarize_llm_record_for_log(direction: str, data: Any) -> Dict[str, Any]:
    normalized = str(direction).upper()
    if normalized == "INPUT":
        return _input_log_summary(data)
    if normalized in {"OUTPUT", "CHUNK"}:
        return summarize_llm_payload_for_log(data)
    if normalized == "STREAM_SUMMARY":
        source = data if isinstance(data, dict) else {}

        def bounded_metric(name: str) -> int:
            value = source.get(name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return 0
            try:
                return max(0, min(int(value), 2_147_483_647))
            except (OverflowError, ValueError):
                return 0

        status = source.get("terminal_status")
        if not isinstance(status, str) or not _SAFE_LLM_STATUS.fullmatch(status):
            status = "unknown"
        error_code = source.get("terminal_error")
        if not isinstance(error_code, str) or not _SAFE_LLM_STATUS.fullmatch(
            error_code
        ):
            error_code = None
        return {
            "schema_version": "aworld.llm-stream-summary-log.v1",
            "terminal_status": status,
            "terminal_error": error_code,
            "reported": source.get("reported") is True,
            "chunk_count": bounded_metric("chunk_count"),
            "content_chars_observed": bounded_metric("content_chars_observed"),
            "reasoning_chars_observed": bounded_metric(
                "reasoning_chars_observed"
            ),
            "tool_call_chunks": bounded_metric("tool_call_chunks"),
            "tool_argument_chars_observed": bounded_metric(
                "tool_argument_chars_observed"
            ),
            "first_chunk_latency_ms": bounded_metric("first_chunk_latency_ms"),
            "duration_ms": bounded_metric("duration_ms"),
        }
    if normalized == "REASONING_SELECTION":
        source = data if isinstance(data, dict) else {}

        def safe_label(name: str) -> str | None:
            value = source.get(name)
            return (
                value
                if isinstance(value, str)
                and _SAFE_LLM_RECEIPT_LABEL.fullmatch(value)
                else None
            )

        status = source.get("status")
        return {
            "schema_version": "aworld.reasoning-selection-log.v1",
            "status": (
                status
                if isinstance(status, str) and _SAFE_LLM_STATUS.fullmatch(status)
                else "recorded"
            ),
            "phase": safe_label("phase"),
            "source": safe_label("source"),
            "reasoning_effort": safe_label("reasoning_effort"),
            "thinking": (
                source.get("thinking")
                if type(source.get("thinking")) is bool
                else None
            ),
            "policy_id": safe_label("policy_id"),
            "transport": safe_label("transport"),
            "applied": source.get("applied") is True,
            "reason_code": safe_label("reason_code"),
        }
    if normalized == "OPENAI_PARAMS":
        return _request_params_log_summary(data)
    return {
        "schema_version": "aworld.llm-log-summary.v1",
        "payload_type": type(data).__name__[:64],
        "text_chars": _nested_text_chars(data),
    }

if os.getenv('AWORLD_LOG_ENDABLE_MONKEY', 'true') == 'true':
    monkey_logger(logger)
    monkey_logger(trace_logger)
    monkey_logger(trajectory_logger)
    monkey_logger(prompt_logger)
    # monkey_logger(digest_logger)
    monkey_logger(asyncio_monitor_logger)
    # monkey_logger(llm_logger)


def log_llm_record(
        direction: str,
        model_name: str,
        data: Any,
        params: Optional[Dict[str, Any]] = None,
        trace_id: Optional[str] = None,
) -> None:
    """Log LLM request/response record.

    Args:
        direction: The direction of the data flow (e.g., "INPUT", "OUTPUT", "CHUNK").
        model_name: The model identifier being called (e.g. "gpt-4o").
        data: The data to be logged (e.g., messages list for input, ModelResponse for output).
        params: Optional dict of extra call parameters (temperature, max_tokens, etc.).
        trace_id: Optional trace ID; auto-resolved from context when omitted.
    """
    from aworld.models.usage import normalize_usage
    from aworld.trace.base import get_trace_id
    from aworld.utils.serialized_util import to_serializable

    resolved_trace_id = trace_id or get_trace_id()
    enriched_params = dict(params or {})

    provider_request_id = None
    raw_usage = None
    if isinstance(data, dict):
        provider_request_id = data.get("provider_request_id")
        raw_usage = data.get("raw_usage")
    else:
        provider_request_id = getattr(data, "provider_request_id", None)
        raw_usage = getattr(data, "raw_usage", None)

    if provider_request_id and "provider_request_id" not in enriched_params:
        enriched_params["provider_request_id"] = provider_request_id

    if isinstance(raw_usage, dict):
        for key in (
            "cache_hit_tokens",
            "cache_write_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        ):
            if key in raw_usage and key not in enriched_params:
                enriched_params[key] = raw_usage[key]

    meta_parts = []
    if enriched_params:
        meta_parts = [
            f"{str(k)[:64]}={str(v)[:256]}"
            for k, v in enriched_params.items()
        ]

    is_stream_chunk = str(direction).upper() == "CHUNK"
    if str(direction).upper() in {"CHUNK", "OUTPUT"}:
        stream_usage = (
            raw_usage
            if isinstance(raw_usage, dict)
            else _llm_field(data, "usage")
        )
        if isinstance(stream_usage, dict):
            normalized_stream_usage = normalize_usage(stream_usage)
            if any(
                normalized_stream_usage.get(key, 0)
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            ):
                meta_parts.extend(
                    [
                        f"prompt_tokens={normalized_stream_usage.get('prompt_tokens', 0)}",
                        f"completion_tokens={normalized_stream_usage.get('completion_tokens', 0)}",
                        f"total_tokens={normalized_stream_usage.get('total_tokens', 0)}",
                    ]
                )
            if normalized_stream_usage.get("cache_hit_tokens", 0):
                meta_parts.append(
                    f"cache_hit_tokens={normalized_stream_usage['cache_hit_tokens']}"
                )
            if normalized_stream_usage.get("cache_write_tokens", 0):
                meta_parts.append(
                    f"cache_write_tokens={normalized_stream_usage['cache_write_tokens']}"
                )
    body = summarize_llm_record_for_log(direction, data)
    if isinstance(data, dict):
        provider_request_id = data.get("provider_request_id")
        if provider_request_id:
            meta_parts.append(f"provider_request_id={str(provider_request_id)[:256]}")

        prompt_cache_key = data.get("prompt_cache_key")
        if prompt_cache_key:
            meta_parts.append("prompt_cache_key_present=true")

        stream_options = data.get("stream_options")
        if isinstance(stream_options, dict) and "include_usage" in stream_options:
            meta_parts.append(f"stream_include_usage={stream_options['include_usage']}")

    meta_str = ", ".join(meta_parts) if meta_parts else ""

    bound_logger = llm_logger._logger.bind(
        trace_id=resolved_trace_id,
        direction=direction,
        model_name=model_name,
        meta=meta_str,
    )
    raw_payload_opt_in = os.getenv(
        "AWORLD_LLM_LOG_RAW_PAYLOADS", "false"
    ).lower() in {"true", "1", "yes"} or (
        is_stream_chunk
        and os.getenv("AWORLD_LLM_LOG_RAW_CHUNKS", "false").lower()
        in {"true", "1", "yes"}
    )
    # A stream can contain tens of thousands of deltas.  Per-chunk INFO
    # summaries still exhaust log budgets even though each record is bounded.
    # The model boundary emits one STREAM_SUMMARY per call instead; raw chunk
    # inspection remains an explicit DEBUG-only diagnostic.
    if not is_stream_chunk:
        bound_logger.info(json.dumps(body, ensure_ascii=False))
    if raw_payload_opt_in:
        # Full payloads stay out of ordinary INFO logs. The explicit opt-in is
        # DEBUG-only because it may contain prompts, reasoning, or Tool args.
        bound_logger.debug(
            json.dumps(to_serializable(data), ensure_ascii=False)
        )


# log examples:
# the same as debug, warn, error, fatal
# logger.info("log")
# logger.info("log", color=Color.yellow)
# logger.info("log", highlight_key="custom_key")
# logger.info("log", color=Color.pink, highlight_key="custom_key")

# @logger.catch
# def div_zero():
#     return 1 / 0
# div_zero()
