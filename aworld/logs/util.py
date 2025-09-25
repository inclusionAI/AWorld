# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import inspect
import os
import sys
from typing import Union, Callable

from loguru import logger as base_logger

base_logger.remove()
SEGMENT_LEN = 200
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

    def __init__(self, tag='AWorld', name: str = 'AWorld', formatter: Union[str, Callable] = None):
        file_formatter = formatter
        console_formatter = formatter
        if not formatter:
            format = """<black>{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | \
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

        base_logger.add(sys.stderr,
                        filter=lambda record: record['extra'].get('name') == tag,
                        colorize=True,
                        format=console_formatter,
                        level=CONSOLE_LEVEL)

        log_file = f'{os.getcwd()}/logs/{tag}-{{time:YYYY-MM-DD}}.log'
        handler_key = f'{name}_{tag}'
        if handler_key not in AWorldLogger._added_handlers:
            base_logger.add(log_file,
                            format=file_formatter,
                            filter=lambda record: record['extra'].get('name') == tag,
                            level=STORAGE_LEVEL,
                            rotation='32 MB',
                            retention='1 days',
                            enqueue=True,
                            backtrace=True,
                            compression='zip')
            AWorldLogger._added_handlers.add(handler_key)

        self._logger = base_logger.bind(name=tag)

    def __getattr__(self, name: str):
        if name in SUPPORTED_FUNC:
            frame = inspect.currentframe().f_back
            if frame.f_back and frame.f_code.co_qualname == 'aworld_log.<locals>.decorator':
                frame = frame.f_back

            module = inspect.getmodule(frame)
            module = module.__name__ if module else ''
            line = frame.f_lineno
            func_name = frame.f_code.co_qualname.replace("<module>", "")
            return getattr(self._logger.patch(lambda r: r.update(function=func_name, line=line, name=module)), name)
        raise AttributeError(f"'AWorldLogger' object has no attribute '{name}'")


logger = AWorldLogger(tag='AWorld', name='AWorld')
trace_logger = AWorldLogger(tag='Trace', name='AWorld')

monkey_logger(logger)
monkey_logger(trace_logger)

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
