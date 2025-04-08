# coding: utf-8
# Copyright (c) 2025 inclusionAI.

from aworld.core.common import Tools
from aworld.core.envs.tool import ToolFactory
from aworld.core.envs.tool_action import VideoAnalysisAction
from aworld.virtual_environments.toolagents.tool_agent import ToolAgentBase


@ToolFactory.register(
    name=Tools.VIDEO_ANALYSIS.value,
    desc="Perform transcription, analysis or summarization over the given video filepath or url",
    supported_action=VideoAnalysisAction,
)
class VideoAnalysisTool(ToolAgentBase):
    """A tool for performing video analysis tasks like transcription and content analysis.

    This tool inherits from the base Tool class and specializes in processing video
    through transcription and performing analysis tasks on video files
    provided either as file paths or URLs.
    """
