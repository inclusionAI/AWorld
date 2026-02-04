# coding: utf-8
# Copyright (c) 2025 inclusionAI.

# 导入模块以触发装饰器注册
# 必须导入模块本身，而不仅仅是类，这样才能执行 @ToolFactory.register 装饰器
import aworld.experimental.cast.tools.cast_analysis_tool  # noqa: F401
import aworld.experimental.cast.tools.cast_patch_tool  # noqa: F401

from aworld.experimental.cast.tools.cast_analysis_tool import (
    CAST_ANALYSIS,
    CAstAnalysisTool,
    CAstAnalysisAction,
)

from aworld.experimental.cast.tools.cast_patch_tool import (
    CAST_PATCH,
    CAstPatchTool,
    CAstPatchAction,
)

__all__ = [
    # Original tools

    'CAST_ANALYSIS',
    'CAST_PATCH',
    # Actions
    'CAstAnalysisAction',
    'CAstPatchAction',
    # AWorld Tools
    'CAstAnalysisTool',
    'CAstPatchTool',
]
