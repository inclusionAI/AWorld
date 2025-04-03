# coding: utf-8
# Copyright (c) 2025 inclusionAI.

from aworld.core.common import Tools
from aworld.core.envs.tool import ToolFactory
from aworld.core.envs.tool_action import ImageAnalysisAction
from aworld.virtual_environments.toolagents.tool_agent import ToolAgentBase


@ToolFactory.register(
    name=Tools.IMAGE_ANALYSIS.value,
    desc="Perform OCR or reasoning over the given image filepath or url",
    supported_action=ImageAnalysisAction,
)
class ImageAnalysisTool(ToolAgentBase):
    """A tool for performing image analysis tasks like OCR and reasoning.

    This tool inherits from the base Tool class and specializes in processing images
    through OCR (Optical Character Recognition) and performing reasoning tasks on images
    provided either as file paths or URLs.
    """
