# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import atexit
import os

debug_mode = os.environ.get('AWORLD_DEBUG_MODE', 'false').lower() in ('true', '1', 't')
log_level = os.environ.get('AWORLD_LOG_LEVEL', 'INFO')
PROJECT_CONFIG = {"debug_mode": debug_mode, "log_level": log_level,
                  "use_trace": os.environ.get('AWORLD_USE_TRACE', 'false').lower() in ('true', '1', 't')}

# Try to load .env file if python-dotenv is available
# This is optional and should not fail if the package is not installed yet (e.g., during pip install)
try:
    from dotenv import load_dotenv

    success = load_dotenv()
    if not success:
        load_dotenv(os.path.join(os.getcwd(), ".env"))
except Exception as e:
    # Log other errors but don't fail initialization
    print(f"Warning: Failed to load .env file: {e}")


def configure(logger_level: str = "INFO", use_trace: bool = None, debug: bool = None):
    from aworld import trace
    from aworld.config import ConfigDict
    from aworld.logs.util import update_logger_level, LOGGER_COLOR

    global PROJECT_CONFIG
    PROJECT_CONFIG = ConfigDict(PROJECT_CONFIG)

    # update all loggers level in console
    if logger_level not in LOGGER_COLOR:
        logger_level = "INFO"
    update_logger_level(logger_level)
    global log_level
    log_level = logger_level
    PROJECT_CONFIG["log_level"] = log_level

    if use_trace:
        # default trace configure, can customize call
        trace.configure()
    if use_trace is not None:
        PROJECT_CONFIG["use_trace"] = use_trace

    if debug is not None:
        PROJECT_CONFIG["debug_mode"] = debug
        global debug_mode
        debug_mode = debug


def cleanup():
    import re

    try:
        value = os.environ.get("LOCAL_TOOLS_ENV_VAR", '')
        if value:
            for action_file in value.split(";"):
                v = re.split(r"\w{6}__tmp", action_file)[0]
                if v == action_file:
                    continue
                tool_file = action_file.replace("_action.py", ".py")
                try:
                    os.remove(action_file)
                    os.remove(tool_file)
                except:
                    pass
    except:
        pass
    os.environ["LOCAL_TOOLS_ENV_VAR"] = ''


atexit.register(cleanup, )
