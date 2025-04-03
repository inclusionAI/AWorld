# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from aworld.core.common import Tools
from aworld.core.envs.tool import ToolFactory
from aworld.core.envs.tool_action import AudioAnalysisAction
from aworld.virtual_environments.toolagents.tool_agent import ToolAgentBase


@ToolFactory.register(
    name=Tools.AUDIO_ANALYSIS.value,
    desc="Perform transcription over the given audio filepath or url",
    supported_action=AudioAnalysisAction,
)
class AudioAnalysisTool(ToolAgentBase):
    """A tool for performing audio analysis tasks like transcription and content analysis.

    This tool inherits from the base Tool class and specializes in processing audio
    through transcription and performing analysis tasks on audio files
    provided either as file paths or URLs.
    """
