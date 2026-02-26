# coding: utf-8
# Copyright (c) 2025 inclusionAI.

"""Tool namespaces for sandbox: sandbox.file.*, sandbox.terminal.*."""

from aworld.sandbox.namespaces.base import ToolNamespace
from aworld.sandbox.namespaces.file import FileNamespace
from aworld.sandbox.namespaces.terminal import TerminalNamespace

__all__ = [
    "ToolNamespace",
    "FileNamespace",
    "TerminalNamespace",
]
