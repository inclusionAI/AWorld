# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import logging

LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
# common logger info
logger = logging.getLogger("common")
# for trace info
trace_logger = logging.getLogger("traced")


class Color:
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


def color_log(value, color: str = Color.black, logger_: logging.Logger = logger, level: int = logging.INFO, hightlight_key=None):
    """ Colored value or highlight key in log.

    Args:
        value:
        color: Color
        hightlight_key: Color segment key.
    """
    if hightlight_key is None:
        logger_.log(level, f"{color} {value} {Color.reset}")
    else:
        logger_.log(level, f"{color} {hightlight_key}: {Color.reset} {value}")


def aworld_log(logger, color: str = Color.black, level: int = logging.INFO):
    """Colored log style in the Aworld.

    Args:
        color: Default color set, different types of information can be set in different colors.
        level: Log level.
    """
    def_color = color

    def decorator(value, color: str = None):
        # Set color in the called.
        if color:
            color_log(value, color, logger, level)
        else:
            color_log(value, def_color, logger, level)

    return decorator


def init_logger(logger: logging.Logger):
    logger.debug = aworld_log(logger, color=Color.lightgrey, level=logging.DEBUG)
    logger.info = aworld_log(logger, color=Color.black, level=logging.INFO)
    logger.warning = aworld_log(logger, color=Color.orange, level=logging.WARNING)
    logger.warn = logger.warning
    logger.error = aworld_log(logger, color=Color.red, level=logging.ERROR)
    logger.fatal = logger.error


init_logger(logger)
init_logger(trace_logger)
