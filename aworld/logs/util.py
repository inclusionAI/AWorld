# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import inspect
import os
import sys
from typing import Union, Callable, Dict, Any

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
        log_path = os.environ.get('AWORLD_LOG_PATH')
        if log_path:
            log_file = f'{log_path}/{tag}.log'
        else:
            log_file = f'{os.getcwd()}/logs/{tag}.log'
        error_log_file = f'{os.getcwd()}/logs/AWorld_error.log'
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
                                                                       record['level'].name in ['WARNING', 'ERROR']),
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


logger = AWorldLogger(tag='aworld', name='AWorld', formatter=os.getenv('AWORLD_LOG_FORMAT'))
trace_logger = AWorldLogger(tag='trace', name='AWorld', formatter=os.getenv('AWORLD_LOG_FORMAT'))
trajectory_logger = AWorldLogger(tag='trajectory', name='AWorld', formatter=os.getenv('AWORLD_LOG_FORMAT'))

prompt_logger = AWorldLogger(tag='prompt_logger', name='AWorld',
                             formatter="<black>{time:YYYY-MM-DD HH:mm:ss.SSS}|prompt|{extra[trace_id]}|</black><level>{message}</level>")
digest_logger = AWorldLogger(tag='digest_logger', name='AWorld',
                             formatter=os.getenv('AWORLD_LOG_FORMAT', "{time:YYYY-MM-DD HH:mm:ss.SSS}| digest | {extra[trace_id]} |<level>{message}</level>"))
asyncio_monitor_logger = AWorldLogger(tag='asyncio_monitor', name='AWorld',
                                      formatter="<black>{time:YYYY-MM-DD HH:mm:ss.SSS} | </black> <level>{message}</level>")

if os.getenv('AWORLD_LOG_ENDABLE_MONKEY', 'true') == 'true':
    monkey_logger(logger)
    monkey_logger(trace_logger)
    monkey_logger(trajectory_logger)
    monkey_logger(prompt_logger)
    # monkey_logger(digest_logger)
    monkey_logger(asyncio_monitor_logger)

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
