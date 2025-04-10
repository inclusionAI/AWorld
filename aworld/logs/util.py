# coding: utf-8
# Copyright (c) 2025 inclusionAI.
try:
    from loguru import logger
except ImportError:
    import logging

    LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    logger = logging.getLogger("common")


class Color:
    black = "\033[30m"
    red = "\033[31m"
    green = "\033[32m"
    orange = "\033[33m"
    blue = "\033[34m"
    purple = "\033[35m"
    cyan = "\033[36m"
    lightgrey = "\033[37m"
    darkgrey = "\033[90m"
    lightred = "\033[91m"
    lightgreen = "\033[92m"
    yellow = "\033[93m"
    lightblue = "\033[94m"
    pink = "\033[95m"
    lightcyan = "\033[96m"
    reset = "\033[0m"
    bold = "\033[01m"
    disable = "\033[02m"
    underline = "\033[04m"
    reverse = "\033[07m"
    strikethrough = "\033[09m"


def color_value(value, color: Color = Color.blue, hightlight_key=None):
    """Colored value or highlight key in console.

    Args:
        value:
        color: Color
        hightlight_key: Color segment key.
    """
    if hightlight_key is None:
        print(f"{color} {value}")
    else:
        print(f"{color} {hightlight_key}: {Color.reset} {value}")


def color_log(value, color: Color = Color.blue, hightlight_key=None):
    """Colored value or highlight key in log.

    Args:
        value:
        color: Color
        hightlight_key: Color segment key.
    """
    if hightlight_key is None:
        logger.info(f"{color} {value}")
    else:
        logger.info(f"{color} {hightlight_key}: {Color.reset} {value}")
