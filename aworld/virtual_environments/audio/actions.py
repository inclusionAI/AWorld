# coding: utf-8
# Copyright (c) 2025 inclusionAI.

from typing import Any, Tuple

from langchain_core.language_models.chat_models import BaseChatModel

from aworld.core.common import ActionModel, ActionResult, Tools
from aworld.core.envs.action_factory import ActionFactory
from aworld.core.envs.tool_action import AudioAnalysisAction
from aworld.logs.util import logger
from aworld.virtual_environments.action import ExecutableAction
from aworld.virtual_environments.audio.prompts import AUDIO_ANALYZE, AUDIO_TRANSCRIBE
from aworld.virtual_environments.audio.utils import (
    create_audio_content,
    encode_audio,
    handle_llm_response,
)


@ActionFactory.register(
    name=AudioAnalysisAction.TRANSCRIBE.value.name,
    desc=AudioAnalysisAction.TRANSCRIBE.value.desc,
    tool_name=Tools.AUDIO_ANALYSIS.value,
)
class AudioTranscribeAction(ExecutableAction):
    """Audio transcription action class for converting audio to text.

    Inherits from ExecutableAction base class to implement audio transcription functionality.
    """

    def act(
        self, action: ActionModel, llm: BaseChatModel, **kwargs
    ) -> Tuple[ActionResult, Any]:
        logger.info("exec %s action", AudioAnalysisAction.TRANSCRIBE.value.name)

        params = action.params
        audio_url = params.get("audio_url", "")
        audio_with_header = params.get("with_header", True)
        audio_base64 = encode_audio(audio_url, with_header=audio_with_header)

        inputs = []
        try:
            content = create_audio_content(AUDIO_TRANSCRIBE, audio_base64)
            inputs.append({"role": "user", "content": content})

            response = llm.chat.completions.create(
                messages=inputs,
                model=kwargs.get("model", "gpt-4o"),
                **{"temperature": kwargs.get("temperature", 0.0)},
            )
            audio_text = handle_llm_response(
                response.choices[0].message.content, "audio_text"
            )
        except (ValueError, IOError, RuntimeError) as e:
            audio_text = ""
            logger.error("Execute error: %s", str(e))

        return ActionResult(content=audio_text, keep=True), audio_base64

    async def async_act(
        self, action: ActionModel, llm: BaseChatModel, **kwargs
    ) -> Tuple[ActionResult, Any]:
        """Asynchronous execution method for audio transcription"""
        return self.act(action, llm, **kwargs)


@ActionFactory.register(
    name=AudioAnalysisAction.ANALYZE.value.name,
    desc=AudioAnalysisAction.ANALYZE.value.desc,
    tool_name=Tools.AUDIO_ANALYSIS.value,
)
class AudioAnalyzeAction(ExecutableAction):
    """Audio analysis action class for processing and analyzing audio content.

    Inherits from ExecutableAction base class to implement audio analysis functionality.
    """

    def act(
        self, action: ActionModel, llm: BaseChatModel, **kwargs
    ) -> Tuple[ActionResult, Any]:
        logger.info("exec %s action", AudioAnalysisAction.ANALYZE.value.name)

        params = action.params
        question = params.get("question", "")
        audio_url = params.get("audio_url", "")
        audio_with_header = params.get("with_header", True)
        audio_base64 = encode_audio(audio_url, with_header=audio_with_header)

        inputs = []
        try:
            content = create_audio_content(
                AUDIO_ANALYZE.format(question=question), audio_base64
            )
            inputs.append({"role": "user", "content": content})

            response = llm.chat.completions.create(
                messages=inputs,
                model=kwargs.get("model", "gpt-4o"),
                **{"temperature": kwargs.get("temperature", 0.0)},
            )
            audio_analysis_result = handle_llm_response(
                response.choices[0].message.content, "audio_analysis_result"
            )
        except (ValueError, IOError, RuntimeError) as e:
            audio_analysis_result = ""
            logger.error("Execute error: %s", str(e))

        return ActionResult(content=audio_analysis_result, keep=True), audio_base64

    async def async_act(
        self, action: ActionModel, llm: BaseChatModel, **kwargs
    ) -> Tuple[ActionResult, Any]:
        """Asynchronous execution method for audio analysis"""
        return self.act(action, llm, **kwargs)
