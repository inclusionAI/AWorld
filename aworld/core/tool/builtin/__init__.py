# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Built-in tools package.

This package contains pre-defined tool classes that avoid the dynamic
code generation issues of @be_tool decorator.
"""

from aworld.core.tool.builtin.spawn_subagent_tool import SpawnSubagentTool

__all__ = ['SpawnSubagentTool']
