# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import logging
import os

from dotenv import load_dotenv

load_dotenv()

LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
logger = logging.getLogger("common")

framework_log_level = os.getenv("FRAMEWORK_LOG_LEVEL")
if framework_log_level == "DEBUG":
    logging.basicConfig(level=logging.DEBUG, format=LOG_FORMAT)
    logger.setLevel(logging.DEBUG)
elif framework_log_level == "INFO":
    logging.basicConfig(level=logging.ERROR, format=LOG_FORMAT)
    logger.setLevel(logging.ERROR)
elif framework_log_level == "WARNING":
    logging.basicConfig(level=logging.WARNING, format=LOG_FORMAT)
    logger.setLevel(logging.WARNING)
elif framework_log_level == "ERROR":
    logging.basicConfig(level=logging.ERROR, format=LOG_FORMAT)
    logger.setLevel(logging.ERROR)
else:
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    logger.setLevel(logging.INFO)


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
        print(f"{color} {value} {Color.reset}")
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
        logging.info(f"{color} {value} {Color.reset}")
    else:
        logging.info(f"{color} {hightlight_key}: {Color.reset} {value}")
