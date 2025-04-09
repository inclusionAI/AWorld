# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import re
from types import FunctionType


def convert_to_snake(name: str) -> str:
    """Class name convert to snake."""
    if '_' not in name:
        name = re.sub(r'([a-z])([A-Z])', r'\1_\2', name)
    return name.lower()


def is_abstract_method(cls, method_name):
    method = getattr(cls, method_name)
    return (hasattr(method, '__isabstractmethod__') and method.__isabstractmethod__) or (
        isinstance(method, FunctionType) and hasattr(
        method, '__abstractmethods__') and method in method.__abstractmethods__)

