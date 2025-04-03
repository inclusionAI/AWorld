# coding: utf-8
# Copyright (c) 2025 inclusionAI.

from typing import Any, Tuple
from urllib.parse import urlparse

from langchain_core.language_models.chat_models import BaseChatModel

from aworld.core.common import ActionModel, ActionResult, Tools
from aworld.core.envs.action_factory import ActionFactory
from aworld.core.envs.tool_action import AudioAnalysisAction
from aworld.logs.util import logger
from aworld.virtual_environments.action import ExecutableAction
from aworld.virtual_environments.toolagents.audio.utils import (
    get_audio_filepath_from_url,
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
        logger.info(f"exec {AudioAnalysisAction.TRANSCRIBE.value.name} action")

        try:
            params = action.params
            audio_url = params.get("audio_url", "")

            parsed_url = urlparse(audio_url)
            is_url = all([parsed_url.scheme, parsed_url.netloc])
            if is_url:
                audio_url = get_audio_filepath_from_url(audio_url)

            with open(audio_url, "rb") as audio_file:
                transcription = llm.audio.transcriptions.create(
                    file=audio_file,
                    model="gpt-4o-transcribe",
                    response_format="text",
                )
            logger.success(f"LLM response: {transcription}")
        except (ValueError, IOError, RuntimeError) as e:
            transcription = ""
            logger.error(f"Execute error: {str(e)}")

        return ActionResult(content=transcription, keep=True), audio_url

    async def async_act(
        self, action: ActionModel, llm: BaseChatModel, **kwargs
    ) -> Tuple[ActionResult, Any]:
        """Asynchronous execution method for audio transcription"""
        return self.act(action, llm, **kwargs)
