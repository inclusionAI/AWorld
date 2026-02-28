# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import sys


def _suppress_keyboard_interrupt_traceback(exc_type, exc_value, exc_tb):
    """Suppress KeyboardInterrupt traceback; exit cleanly."""
    if exc_type is KeyboardInterrupt:
        sys.exit(0)
    sys.__excepthook__(exc_type, exc_value, exc_tb)


sys.excepthook = _suppress_keyboard_interrupt_traceback

from dotenv import load_dotenv

load_dotenv()