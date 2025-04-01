# coding: utf-8
# Copyright (c) 2025 inclusionAI.

from typing import Any, Tuple

from langchain_core.language_models.chat_models import BaseChatModel

from aworld.core.common import ActionModel, ActionResult, Tools
from aworld.core.envs.action_factory import ActionFactory
from aworld.core.envs.tool_action import VideoAnalysisAction
from aworld.logs.util import logger
from aworld.virtual_environments.action import ExecutableAction
from aworld.virtual_environments.video.prompts import (
    VIDEO_ANALYZE,
    VIDEO_SUMMARIZE,
    VIDEO_TRANSCRIBE,
)
from aworld.virtual_environments.video.utils import (
    create_video_content,
    encode_video,
    handle_llm_response,
)


@ActionFactory.register(
    name=VideoAnalysisAction.TRANSCRIBE.value.name,
    desc=VideoAnalysisAction.TRANSCRIBE.value.desc,
    tool_name=Tools.VIDEO_ANALYSIS.value,
)
class VideoTranscribeAction(ExecutableAction):
    """Video transcription action class for converting speech in video to text.

    Inherits from ExecutableAction base class to implement video transcription functionality.
    """

    def act(
        self, action: ActionModel, llm: BaseChatModel, **kwargs
    ) -> Tuple[ActionResult, Any]:
        logger.info("exec %s action", VideoAnalysisAction.TRANSCRIBE.value.name)

        params = action.params
        video_url = params.get("video_url", "")
        video_with_header = params.get("with_header", True)
        video_base64 = encode_video(video_url, with_header=video_with_header)

        inputs = []
        try:
            content = create_video_content(VIDEO_TRANSCRIBE, video_base64)
            inputs.append({"role": "user", "content": content})

            response = llm.chat.completions.create(
                messages=inputs,
                model=kwargs.get("model", "gpt-4o"),
                **{"temperature": kwargs.get("temperature", 0.0)},
            )
            video_text = handle_llm_response(
                response.choices[0].message.content, "video_text"
            )
        except (ValueError, IOError, RuntimeError) as e:
            video_text = ""
            logger.error("Execute error: %s", str(e))

        return ActionResult(content=video_text, keep=True), video_base64

    async def async_act(
        self, action: ActionModel, llm: BaseChatModel, **kwargs
    ) -> Tuple[ActionResult, Any]:
        """Asynchronous execution method for video transcription"""
        return self.act(action, llm, **kwargs)


@ActionFactory.register(
    name=VideoAnalysisAction.ANALYZE.value.name,
    desc=VideoAnalysisAction.ANALYZE.value.desc,
    tool_name=Tools.VIDEO_ANALYSIS.value,
)
class VideoAnalyzeAction(ExecutableAction):
    """Video analysis action class for processing and analyzing video content.

    Inherits from ExecutableAction base class to implement video analysis functionality.
    """

    def act(
        self, action: ActionModel, llm: BaseChatModel, **kwargs
    ) -> Tuple[ActionResult, Any]:
        logger.info("exec %s action", VideoAnalysisAction.ANALYZE.value.name)

        params = action.params
        question = params.get("question", "")
        video_url = params.get("video_url", "")
        video_with_header = params.get("with_header", True)
        video_base64 = encode_video(video_url, with_header=video_with_header)

        inputs = []
        try:
            content = create_video_content(
                VIDEO_ANALYZE.format(question=question), video_base64
            )
            inputs.append({"role": "user", "content": content})

            response = llm.chat.completions.create(
                messages=inputs,
                model=kwargs.get("model", "gpt-4o"),
                **{"temperature": kwargs.get("temperature", 0.0)},
            )
            video_analysis_result = handle_llm_response(
                response.choices[0].message.content, "video_analysis_result"
            )
        except (ValueError, IOError, RuntimeError) as e:
            video_analysis_result = ""
            logger.error("Execute error: %s", str(e))

        return ActionResult(content=video_analysis_result, keep=True), video_base64

    async def async_act(
        self, action: ActionModel, llm: BaseChatModel, **kwargs
    ) -> Tuple[ActionResult, Any]:
        """Asynchronous execution method for video analysis"""
        return self.act(action, llm, **kwargs)


@ActionFactory.register(
    name=VideoAnalysisAction.SUMMARIZE.value.name,
    desc=VideoAnalysisAction.SUMMARIZE.value.desc,
    tool_name=Tools.VIDEO_ANALYSIS.value,
)
class VideoSummarizeAction(ExecutableAction):
    """Video summarization action class for creating concise summaries of video content.

    Inherits from ExecutableAction base class to implement video summarization functionality.
    """

    def act(
        self, action: ActionModel, llm: BaseChatModel, **kwargs
    ) -> Tuple[ActionResult, Any]:
        logger.info("exec %s action", VideoAnalysisAction.SUMMARIZE.value.name)

        params = action.params
        video_url = params.get("video_url", "")
        video_with_header = params.get("with_header", True)
        video_base64 = encode_video(video_url, with_header=video_with_header)

        inputs = []
        try:
            content = create_video_content(VIDEO_SUMMARIZE, video_base64)
            inputs.append({"role": "user", "content": content})

            response = llm.chat.completions.create(
                messages=inputs,
                model=kwargs.get("model", "gpt-4o"),
                **{"temperature": kwargs.get("temperature", 0.0)},
            )
            video_summary = handle_llm_response(
                response.choices[0].message.content, "video_summary"
            )
        except (ValueError, IOError, RuntimeError) as e:
            video_summary = ""
            logger.error("Execute error: %s", str(e))

        return ActionResult(content=video_summary, keep=True), video_base64

    async def async_act(
        self, action: ActionModel, llm: BaseChatModel, **kwargs
    ) -> Tuple[ActionResult, Any]:
        """Asynchronous execution method for video summarization"""
        return self.act(action, llm, **kwargs)
